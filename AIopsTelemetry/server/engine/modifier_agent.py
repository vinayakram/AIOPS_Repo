"""
Codebase Modifier Agent — powered by GPT-4o.

Takes a project directory, explores it with file tools, then intelligently
injects AIops telemetry (AIopsCallbackHandler for LangGraph projects, or
direct HTTP ingest for custom pipelines).

Yields SSE-ready event dicts throughout execution so the dashboard can
stream live progress to the user.
"""
import json
import os
import re
import asyncio
from pathlib import Path
from typing import AsyncIterator

from openai import AsyncOpenAI

from server.config import settings

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": (
                "List files in the project directory matching a glob pattern. "
                "Returns a JSON array of relative file paths, capped at 200 results. "
                "Use this to explore the project structure before reading files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern relative to project root, e.g. '**/*.py', '*.txt'"
                    },
                    "subdir": {
                        "type": "string",
                        "description": "Optional subdirectory within project root to start from. Default is root."
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the full text content of a file within the project directory. "
                "Files larger than 50 KB are truncated with a notice. "
                "Use relative paths from the project root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file from the project root, e.g. 'backend/main.py'"
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write (create or overwrite) a file with the given content. "
                "Always pass the COMPLETE new file content — not a diff or partial update. "
                "Creates parent directories if needed. Use relative paths from the project root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path for the file from the project root."
                    },
                    "content": {
                        "type": "string",
                        "description": "Complete new content for the file."
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_in_files",
            "description": (
                "Search for a regex pattern across files in the project directory. "
                "Returns up to 50 matches as a JSON array with file path, line number, and matched text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Python regex pattern, e.g. r'\\.invoke\\(', 'StateGraph', 'import langchain'"
                    },
                    "glob_pattern": {
                        "type": "string",
                        "description": "Glob to filter files. Default '**/*.py'.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]

# ── System prompt template ────────────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
You are an expert Python developer specialising in AI observability and LangGraph/LangChain instrumentation.

Your task is to instrument the Python codebase in the provided project directory so it automatically
sends telemetry to the AIops Telemetry server at {aiops_server_url}.

The application name for telemetry is: {app_name}

═══ INTEGRATION PATTERNS ═══

PATTERN A — LangGraph / LangChain project (has StateGraph, CompiledGraph, or LangChain LCEL):
  1. Add import: `from aiops_sdk import AIopsCallbackHandler`
  2. Inject `AIopsCallbackHandler()` into the `config={{"callbacks": [AIopsCallbackHandler()]}}` argument
     of every `.invoke()`, `.astream()`, `.stream()`, or `.ainvoke()` call on a compiled graph.
  3. Create `aiops_init.py` at project root (see template below).

PATTERN B — Custom pipeline project (no LangGraph, but has OpenAI/LLM calls and pipeline steps):
  1. Add a thin tracing module that posts traces to {aiops_server_url}/api/ingest/trace using `requests`.
  2. Inject start_trace() before the pipeline runs and finish_trace() at the end.
  3. Wrap key steps with start_step() / end_step() calls.
  4. Create `aiops_init.py` at project root (see template below).

`aiops_init.py` template:
```python
import os
from aiops_sdk import AIopsClient

def setup_aiops():
    AIopsClient.configure(
        server_url=os.getenv("AIOPS_SERVER_URL", "{aiops_server_url}"),
        app_name=os.getenv("AIOPS_APP_NAME", "{app_name}"),
        api_key=os.getenv("AIOPS_API_KEY"),
    )
```

═══ WORKFLOW ═══

1. Use glob_files("**/*.py") to see all Python files.
2. Use read_file on key entry points: main.py, app.py, run.py, graph.py, pipeline.py, __init__.py etc.
3. Use search_in_files to find: StateGraph, .invoke(, .astream(, import langchain, import openai.
4. Determine which pattern fits (A or B).
5. Read each file you need to modify.
6. Write the complete modified file content using write_file.
7. Create aiops_init.py if it doesn't already exist.
8. After all writes, summarise what you changed and why.

═══ STRICT RULES ═══

- Only read/write files inside the project directory. Never use absolute paths that escape it.
- Do NOT modify .env files, secret files, or test files (*test*.py, test_*.py).
- Do NOT delete any files. Do NOT break existing functionality.
- If AIopsCallbackHandler or aiops_sdk is already imported in a file, skip that file.
- When writing a modified file, include ALL the original code plus your additions.
- Keep changes minimal and surgical — only add what is necessary.
"""


# ── Agent class ───────────────────────────────────────────────────────────────

class ModifierAgent:
    def __init__(self, project_dir: str, app_name: str, aiops_server_url: str):
        self._root = Path(project_dir).resolve()
        self.app_name = app_name
        self.aiops_server_url = aiops_server_url.rstrip("/")
        self._files_modified: list[str] = []

    async def run(self) -> AsyncIterator[dict]:
        api_key = (
            settings.OPENAI_API_KEY
            or os.environ.get("OPENAI_API_KEY")
        )
        if not api_key:
            yield {"type": "error", "message": (
                "OPENAI_API_KEY not set. "
                "Set AIOPS_OPENAI_API_KEY or OPENAI_API_KEY in environment."
            )}
            return

        if not self._root.is_dir():
            yield {"type": "error", "message": f"Project directory not found: {self._root}"}
            return

        yield {"type": "status", "message": f"Scanning project: {self._root}"}

        client = AsyncOpenAI(api_key=api_key)
        system = SYSTEM_PROMPT_TEMPLATE.format(
            aiops_server_url=self.aiops_server_url,
            app_name=self.app_name,
        )
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Please instrument the project at: {self._root}\n"
                    f"App name: {self.app_name}\n"
                    f"AIops server: {self.aiops_server_url}\n\n"
                    "Start by exploring the project structure, then make the necessary changes."
                ),
            },
        ]

        async for event in self._agentic_loop(client, messages):
            yield event

    async def _agentic_loop(
        self, client: AsyncOpenAI, messages: list
    ) -> AsyncIterator[dict]:
        max_iterations = 20
        iteration = 0

        while iteration < max_iterations:
            iteration += 1
            yield {"type": "status", "message": f"Calling GPT-4o (turn {iteration})…"}

            try:
                response = await client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                )
            except Exception as e:
                yield {"type": "error", "message": f"OpenAI API error: {e}"}
                return

            choice = response.choices[0]
            msg = choice.message

            # Emit any text content as status
            if msg.content and msg.content.strip():
                yield {"type": "status", "message": msg.content.strip()}

            # Append assistant message to history
            messages.append(msg.model_dump(exclude_unset=False))

            if choice.finish_reason == "stop":
                yield {
                    "type": "done",
                    "message": "Instrumentation complete.",
                    "files_modified": self._files_modified,
                }
                return

            if choice.finish_reason != "tool_calls":
                yield {"type": "error", "message": f"Unexpected finish_reason: {choice.finish_reason}"}
                return

            # Execute all tool calls, then add all results in separate tool messages
            tool_calls = msg.tool_calls or []
            for tc in tool_calls:
                inp = json.loads(tc.function.arguments)
                yield {
                    "type": "tool_call",
                    "tool": tc.function.name,
                    "input": inp,
                    "tool_call_id": tc.id,
                }

                result_text, extra_event = self._dispatch_tool(tc.function.name, inp)
                if extra_event:
                    yield extra_event

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

        yield {"type": "error", "message": f"Reached max iterations ({max_iterations}). Stopping."}

    # ── Tool dispatcher ───────────────────────────────────────────────────────

    def _dispatch_tool(self, name: str, inp: dict) -> tuple[str, dict | None]:
        try:
            if name == "glob_files":
                return self._glob_files(inp.get("pattern", "**/*.py"), inp.get("subdir", "")), None
            if name == "read_file":
                return self._read_file(inp["path"]), None
            if name == "write_file":
                return self._write_file(inp["path"], inp["content"])
            if name == "search_in_files":
                return self._search_in_files(inp["pattern"], inp.get("glob_pattern", "**/*.py")), None
            return f"Unknown tool: {name}", None
        except ValueError as e:
            return f"Error: {e}", None
        except Exception as e:
            return f"Tool error: {e}", None

    # ── Tool implementations ──────────────────────────────────────────────────

    def _safe_path(self, rel: str) -> Path:
        p = (self._root / rel).resolve()
        if not str(p).startswith(str(self._root)):
            raise ValueError(f"Path {rel!r} escapes project boundary")
        return p

    def _glob_files(self, pattern: str, subdir: str = "") -> str:
        base = self._safe_path(subdir) if subdir else self._root
        results = []
        for p in base.rglob(pattern):
            if p.is_file():
                try:
                    results.append(str(p.relative_to(self._root)))
                except ValueError:
                    pass
            if len(results) >= 200:
                break
        return json.dumps(results)

    def _read_file(self, path: str) -> str:
        p = self._safe_path(path)
        if not p.is_file():
            return f"Error: file not found: {path}"
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"Error reading file: {e}"
        MAX = 50_000
        if len(content) > MAX:
            content = content[:MAX] + f"\n\n... [truncated at {MAX} chars] ..."
        return content

    def _write_file(self, path: str, content: str) -> tuple[str, dict]:
        p = self._safe_path(path)
        name = p.name.lower()
        if ".env" in name or "secret" in name or "credential" in name:
            return "Refused: will not write to secret/env files.", None
        if re.match(r"(test_|_test\.py$)", name):
            return "Refused: will not modify test files.", None

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

        rel = str(p.relative_to(self._root))
        self._files_modified.append(rel)
        event = {"type": "file_modified", "path": rel, "size": len(content)}
        return "ok", event

    def _search_in_files(self, pattern: str, glob_pattern: str = "**/*.py") -> str:
        results = []
        try:
            compiled = re.compile(pattern, re.MULTILINE)
        except re.error as e:
            return f"Invalid regex: {e}"

        for p in self._root.rglob(glob_pattern):
            if not p.is_file():
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                if compiled.search(line):
                    results.append({
                        "file": str(p.relative_to(self._root)),
                        "line": i,
                        "match": line.strip()[:200],
                    })
                    if len(results) >= 50:
                        return json.dumps(results)

        return json.dumps(results)
