"""
LangChain/LangGraph callback handler.
Drop this into any graph's .invoke() / .astream() call to get automatic tracing.

Usage:
    from aiops_sdk import AIopsCallbackHandler
    graph.invoke(inputs, config={"callbacks": [AIopsCallbackHandler()]})
"""
import logging
from typing import Any, Optional, Union
from uuid import UUID

from aiops_sdk.client import AIopsClient
from aiops_sdk.context import SpanContext, push_span, pop_span, current_span, new_span_id, new_trace_id

try:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.outputs import LLMResult
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False
    BaseCallbackHandler = object
    LLMResult = Any

logger = logging.getLogger("aiops.callback")


class AIopsCallbackHandler(BaseCallbackHandler if _LANGCHAIN_AVAILABLE else object):
    """LangGraph/LangChain callback handler that forwards telemetry to AIops server."""

    raise_error = False  # never crash the caller

    def __init__(self, trace_id: str = None, user_id: str = None, session_id: str = None):
        if _LANGCHAIN_AVAILABLE:
            super().__init__()
        self._client = AIopsClient.get()
        self._user_id = user_id
        self._session_id = session_id
        self._trace_id = trace_id  # external override
        self._run_to_span: dict[str, SpanContext] = {}  # run_id → SpanContext

    # ── Chain (graph node / LCEL chain) ──────────────────────────────────────

    def _log(self, level: str, message: str, logger: str = "agent"):
        """Write a log entry into the active trace buffer."""
        if self._trace_id:
            self._client.log(self._trace_id, level, message, logger)

    def on_chain_start(self, serialized: dict, inputs: Any, *, run_id: UUID,
                       parent_run_id: Optional[UUID] = None, **kwargs):
        name = _chain_name(serialized, kwargs)
        tid = self._ensure_trace(str(run_id), inputs)
        self._log("DEBUG", f"▶ chain started: {name}", "chain")
        span = SpanContext(
            span_id=new_span_id(),
            trace_id=tid,
            parent_span_id=_parent_span_id(self._run_to_span, parent_run_id),
            name=name,
            span_type="chain",
            input_preview=_preview(inputs),
        )
        self._run_to_span[str(run_id)] = span
        push_span(span)

    def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs):
        span = self._run_to_span.pop(str(run_id), None)
        if span:
            self._log("DEBUG", f"✓ chain finished: {span.name}", "chain")
            span.finish(output_preview=_preview(outputs))
            self._client.add_span(span.trace_id, span)
            pop_span()
            # If this is the root chain (no parent), finish the trace
            if span.parent_span_id is None:
                self._client.finish_trace(
                    span.trace_id,
                    output_preview=_preview(outputs),
                    status="ok",
                )

    def on_chain_error(self, error: Exception, *, run_id: UUID, **kwargs):
        span = self._run_to_span.pop(str(run_id), None)
        if span:
            self._log("ERROR", f"✗ chain error in {span.name}: {error}", "chain")
            span.finish(error=str(error))
            self._client.add_span(span.trace_id, span)
            pop_span()
            if span.parent_span_id is None:
                self._client.finish_trace(span.trace_id, status="error")

    # ── LLM ──────────────────────────────────────────────────────────────────

    def on_llm_start(self, serialized: dict, prompts: list, *, run_id: UUID,
                     parent_run_id: Optional[UUID] = None, **kwargs):
        model = (serialized or {}).get("id", ["unknown"])[-1] if serialized else "unknown"
        self._log("DEBUG", f"LLM call → {model} ({len(prompts)} prompt(s))", "llm")
        tid = self._get_trace_id(parent_run_id)
        if not tid:
            return
        span = SpanContext(
            span_id=new_span_id(),
            trace_id=tid,
            parent_span_id=_parent_span_id(self._run_to_span, parent_run_id),
            name=f"llm:{model}",
            span_type="llm",
            model_name=model,
            input_preview=_preview(prompts),
        )
        self._run_to_span[str(run_id)] = span
        push_span(span)

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs):
        span = self._run_to_span.pop(str(run_id), None)
        if not span:
            return
        out = ""
        tokens_in = tokens_out = None
        if _LANGCHAIN_AVAILABLE and isinstance(response, LLMResult):
            if response.generations:
                gen = response.generations[0][0]
                out = getattr(gen, "text", str(gen))
            if response.llm_output:
                usage = response.llm_output.get("token_usage", {})
                tokens_in = usage.get("prompt_tokens")
                tokens_out = usage.get("completion_tokens")
        span.tokens_input = tokens_in
        span.tokens_output = tokens_out
        tok_msg = f"{tokens_in or 0} in / {tokens_out or 0} out tokens" if tokens_in is not None else ""
        self._log("INFO", f"LLM response received{' · ' + tok_msg if tok_msg else ''}", "llm")
        span.finish(output_preview=out[:500] if out else None)
        self._client.add_span(span.trace_id, span)
        pop_span()

    def on_llm_error(self, error: Exception, *, run_id: UUID, **kwargs):
        self._log("ERROR", f"LLM error: {error}", "llm")
        span = self._run_to_span.pop(str(run_id), None)
        if span:
            span.finish(error=str(error))
            self._client.add_span(span.trace_id, span)
            pop_span()

    # ── Tool ─────────────────────────────────────────────────────────────────

    def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID,
                      parent_run_id: Optional[UUID] = None, **kwargs):
        tid = self._get_trace_id(parent_run_id)
        if not tid:
            return
        name = (serialized or {}).get("name") or kwargs.get("name") or "tool"
        preview = (input_str or "")[:120]
        self._log("INFO", f"Tool call: {name}({preview}{'…' if len(input_str or '') > 120 else ''})", "tool")
        span = SpanContext(
            span_id=new_span_id(),
            trace_id=tid,
            parent_span_id=_parent_span_id(self._run_to_span, parent_run_id),
            name=f"tool:{name}",
            span_type="tool",
            input_preview=input_str[:500] if input_str else None,
        )
        self._run_to_span[str(run_id)] = span
        push_span(span)

    def on_tool_end(self, output: str, *, run_id: UUID, **kwargs):
        span = self._run_to_span.pop(str(run_id), None)
        if span:
            out_preview = str(output or "")[:120]
            self._log("INFO", f"Tool result from {span.name}: {out_preview}{'…' if len(str(output or '')) > 120 else ''}", "tool")
            span.finish(output_preview=str(output)[:500])
            self._client.add_span(span.trace_id, span)
            pop_span()

    def on_tool_error(self, error: Exception, *, run_id: UUID, **kwargs):
        self._log("ERROR", f"Tool error: {error}", "tool")
        span = self._run_to_span.pop(str(run_id), None)
        if span:
            span.finish(error=str(error))
            self._client.add_span(span.trace_id, span)
            pop_span()

    # ── Retriever ─────────────────────────────────────────────────────────────

    def on_retriever_start(self, serialized: dict, query: str, *, run_id: UUID,
                           parent_run_id: Optional[UUID] = None, **kwargs):
        tid = self._get_trace_id(parent_run_id)
        if not tid:
            return
        span = SpanContext(
            span_id=new_span_id(),
            trace_id=tid,
            parent_span_id=_parent_span_id(self._run_to_span, parent_run_id),
            name="retriever",
            span_type="retriever",
            input_preview=query[:500],
        )
        self._run_to_span[str(run_id)] = span
        push_span(span)

    def on_retriever_end(self, documents: Any, *, run_id: UUID, **kwargs):
        span = self._run_to_span.pop(str(run_id), None)
        if span:
            preview = f"{len(documents)} docs" if hasattr(documents, '__len__') else str(documents)[:200]
            span.finish(output_preview=preview)
            self._client.add_span(span.trace_id, span)
            pop_span()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _ensure_trace(self, run_id: str, inputs: Any) -> str:
        if self._trace_id:
            return self._trace_id
        # New top-level trace
        tid = new_trace_id()
        self._trace_id = tid
        self._client.start_trace(input_preview=_preview(inputs), trace_id=tid)
        return tid

    def _get_trace_id(self, parent_run_id: Optional[UUID]) -> Optional[str]:
        if self._trace_id:
            return self._trace_id
        if parent_run_id:
            parent_span = self._run_to_span.get(str(parent_run_id))
            if parent_span:
                return parent_span.trace_id
        return None


# ── Module-level helpers ───────────────────────────────────────────────────────

def _chain_name(serialized: dict, kwargs: dict) -> str:
    tags = kwargs.get("tags") or []
    name = (serialized or {}).get("id", ["chain"])[-1] if serialized else (
        kwargs.get("name") or "chain"
    )
    if tags:
        name = f"{name}[{','.join(str(t) for t in tags[:2])}]"
    return name


def _preview(obj: Any, max_len: int = 300) -> Optional[str]:
    if obj is None:
        return None
    try:
        import json
        s = json.dumps(obj, default=str)
    except Exception:
        s = str(obj)
    return s[:max_len]


def _parent_span_id(run_map: dict, parent_run_id: Optional[UUID]) -> Optional[str]:
    if not parent_run_id:
        return None
    parent = run_map.get(str(parent_run_id))
    return parent.span_id if parent else None
