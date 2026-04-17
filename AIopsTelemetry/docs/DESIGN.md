# AIops Telemetry — Detailed Design Document

**Document Type:** Technical Design
**Version:** 1.0
**Date:** March 2026
**Audience:** Software Engineers, Platform Engineers, SRE, Technical Architects

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture Diagram](#2-architecture-diagram)
3. [Layer-by-Layer Design](#3-layer-by-layer-design)
   - 3.1 SDK (aiops_sdk/)
   - 3.2 Ingest API
   - 3.3 Issue Detection Engine
   - 3.4 Metrics Collector
   - 3.5 Reason Analyzer (LLM)
   - 3.6 Escalation Engine
   - 3.7 AutoFix Agent
   - 3.8 Modifier Agent
   - 3.9 Dashboard SPA
4. [Database Schema](#4-database-schema)
5. [API Reference](#5-api-reference)
6. [NFR Rule Catalogue](#6-nfr-rule-catalogue)
7. [Configuration Reference](#7-configuration-reference)
8. [Data Flow Diagrams](#8-data-flow-diagrams)
9. [Security Model](#9-security-model)
10. [Deployment Guide](#10-deployment-guide)
11. [Extension Points](#11-extension-points)

---

## 1. System Overview

AIops Telemetry is a **single-process FastAPI application** that provides:

- **Telemetry ingestion** — accepts traces, spans, and logs from instrumented AI agents via a REST API
- **NFR-based issue detection** — a background loop evaluates 15+ non-functional-requirement rules against ingested data every 30 seconds
- **System metrics collection** — a second background loop samples CPU, memory, disk I/O, and network every 10 seconds
- **LLM root-cause analysis** — when an issue fires, Claude Sonnet or GPT-4o generates a structured explanation with correlated system metrics
- **Escalation rules engine** — configurable rules fire webhooks, update issue status, and log events
- **Automated code repair** — invokes Claude Code CLI in the agent's source directory to fix detected issues
- **Zero-code instrumentation** — a GPT-4o modifier agent reads and rewrites agent codebases to add telemetry
- **Dashboard SPA** — a vanilla-JS single-page application served by the same FastAPI process

**Tech stack summary:**

| Component | Technology |
|---|---|
| Web framework | FastAPI 0.115+ |
| ASGI server | Uvicorn (standard) |
| ORM | SQLAlchemy 2.0 |
| Database | SQLite (default); any SQLAlchemy-compatible DB |
| SDK HTTP client | requests (sync, SDK); httpx (async, server) |
| LLM clients | anthropic SDK ≥0.86, openai SDK ≥1.0 |
| System metrics | psutil |
| Config | pydantic-settings |
| Frontend | Vanilla HTML + CSS + JS (no framework) |

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Agent Applications                           │
│                                                                     │
│  ┌──────────────────┐   ┌──────────────────┐   ┌────────────────┐  │
│  │  MedicalAgent    │   │  WebSearchAgent  │   │  CustomAgent   │  │
│  │  (RAG pipeline)  │   │  (LangGraph)     │   │  (any app)     │  │
│  └────────┬─────────┘   └────────┬─────────┘   └───────┬────────┘  │
│           │                      │                      │           │
│           └──────── aiops_sdk ───┴──────────────────────┘           │
│                     (callback handler / manual SDK)                 │
└────────────────────────────┬────────────────────────────────────────┘
                             │  HTTP POST /api/ingest/trace
                             │  (TraceIn + SpanIn[] + LogIn[])
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    AIops Telemetry Server (port 7000)                │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                        FastAPI App                           │   │
│  │                                                              │   │
│  │  /api/ingest     /api/traces    /api/issues                  │   │
│  │  /api/escalations  /api/metrics  /api/analysis               │   │
│  │  /api/autofix    /api/agent     /health                      │   │
│  └──────────────────────────────┬───────────────────────────────┘   │
│                                 │                                   │
│  ┌──────────────────────────────▼───────────────────────────────┐   │
│  │                      SQLite Database                         │   │
│  │  traces | spans | trace_logs | issues | escalation_rules     │   │
│  │  escalation_logs | system_metrics | issue_analyses           │   │
│  └──────────────────────────────┬───────────────────────────────┘   │
│                                 │                                   │
│  ┌────────────────┐  ┌──────────▼───────┐  ┌─────────────────────┐ │
│  │ Metrics        │  │ Escalation       │  │ Reason Analyzer     │ │
│  │ Collector      │  │ Engine           │  │ (LLM root-cause)    │ │
│  │ (every 10s)    │  │ (every 30s)      │  │ (async, per issue)  │ │
│  └────────────────┘  └──────────────────┘  └─────────────────────┘ │
│                                                                     │
│  ┌─────────────────────┐   ┌──────────────────────────────────────┐ │
│  │ AutoFix Agent       │   │ Modifier Agent (GPT-4o)              │ │
│  │ (Claude Code CLI)   │   │ (codebase instrumentation)           │ │
│  └─────────────────────┘   └──────────────────────────────────────┘ │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              Dashboard SPA (index.html)                      │   │
│  │  Overview | Traces | Issues | Metrics | Escalations | Agents │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
         │                    │                       │
         ▼                    ▼                       ▼
   Langfuse Cloud       PagerDuty /           Claude Code CLI
   (external traces)    Slack webhook          (local process)
```

---

## 3. Layer-by-Layer Design

### 3.1 SDK — `aiops_sdk/`

The SDK is a **lightweight, thread-safe telemetry library** that applications import. It requires no infrastructure dependencies and adds minimal latency overhead.

#### Public API

```python
from aiops_sdk import AIopsConfig, AIopsClient, AIopsCallbackHandler, trace_span
```

| Symbol | Type | Purpose |
|---|---|---|
| `AIopsConfig` | Class | Configuration; reads from env or constructor |
| `AIopsClient` | Singleton | Manages trace buffers; provides `start_trace`, `add_span`, `finish_trace` |
| `AIopsCallbackHandler` | LangChain callback | Auto-instruments LangGraph / LangChain apps |
| `trace_span` | Decorator | Manual span annotation for arbitrary functions |

#### AIopsConfig

```python
@dataclass
class AIopsConfig:
    server_url: str    # AIOPS_SERVER_URL, default "http://localhost:7000"
    app_name: str      # AIOPS_APP_NAME, default "default"
    api_key: str       # AIOPS_API_KEY, optional
```

Constructs:
- `ingest_url = server_url + "/api/ingest/trace"`
- `batch_url  = server_url + "/api/ingest/batch"`
- `headers    = {"X-AIops-Key": api_key}` if key is set

#### TraceBuffer (internal)

```
TraceBuffer {
    trace_id: str
    app_name: str
    started_at: datetime
    ended_at: datetime | None
    status: "ok" | "error"
    input_preview: str | None    # truncated to 500 chars
    output_preview: str | None   # truncated to 500 chars
    spans: list[SpanContext]
    logs: list[dict]
    _lock: threading.Lock
}
```

#### SpanContext

```
SpanContext {
    span_id: str              # uuid4
    trace_id: str
    parent_span_id: str | None
    name: str
    span_type: "chain" | "llm" | "tool" | "retriever"
    status: "ok" | "error"
    started_at: datetime
    ended_at: datetime | None
    duration_ms: float | None
    input_preview: str | None
    output_preview: str | None
    error_message: str | None
    tokens_input: int | None    # from LLMResult usage metadata
    tokens_output: int | None
    model_name: str | None
}
```

#### Instrumentation Flow (Callback Handler)

```
LangGraph.invoke()
    │
    ├── on_chain_start(name, inputs, **kwargs)
    │       push SpanContext(type="chain") to ContextVar stack
    │       set parent_span_id = current stack top
    │
    ├── on_llm_start(name, messages)
    │       push SpanContext(type="llm")
    │       record model_name from serialized
    │
    ├── on_llm_end(response: LLMResult)
    │       pop span; set ended_at, tokens, output_preview
    │       add span to TraceBuffer
    │
    ├── on_tool_start / on_tool_end / on_tool_error
    │       push/pop SpanContext(type="tool")
    │
    ├── on_retriever_start / on_retriever_end
    │       push/pop SpanContext(type="retriever")
    │
    └── on_chain_end(outputs)
            pop root chain span
            if no parent span: finish_trace() → HTTP POST /api/ingest/trace
```

#### Flush Mechanism

`finish_trace()` serializes the `TraceBuffer` to a `TraceIn` payload and makes a **synchronous HTTP POST** using `requests`. This is intentional — by the time `finish_trace` is called, the LangGraph invocation has completed, so blocking is acceptable. Exceptions are caught and logged; the calling application never crashes due to telemetry failure.

---

### 3.2 Ingest API — `server/api/ingest.py`

Two endpoints accept telemetry from any SDK or custom integration.

#### POST `/api/ingest/trace`

Accepts:
```json
{
  "id": "trace-uuid",
  "app_name": "medical-agent",
  "status": "ok",
  "started_at": "2026-03-24T10:00:00Z",
  "ended_at": "2026-03-24T10:00:09.68Z",
  "total_duration_ms": 9680,
  "input_preview": "{\"query\": \"Alzheimer...\"}",
  "output_preview": "{\"answer_preview\": \"⚠️ Error...\"}",
  "spans": [ ... SpanIn[] ... ],
  "logs": [ ... LogIn[] ... ]
}
```

Behavior:
- Upserts trace (insert if new; update `status`, `ended_at`, `total_duration_ms`, `output_preview` if record exists)
- Upserts each span (same pattern)
- Inserts all logs (no deduplication)
- Returns `{"ok": true, "trace_id": "..."}`

#### POST `/api/ingest/batch`

Accepts `{"traces": [ ... TraceIn[] ... ]}`. Max batch size = `MAX_INGEST_BATCH_SIZE` (default 500). All upserts in one DB transaction.

#### Authentication

If `AIOPS_API_KEY` is set in config, all ingest requests must include the header `X-AIops-Key: <key>`. Returns `401` if missing or wrong.

---

### 3.3 Issue Detection Engine — `server/engine/issue_detector.py`

The central NFR detection module. Runs inside the escalation engine tick as a synchronous call.

#### Design Principles

1. **Deterministic** — given the same DB state, always produces the same issues
2. **Idempotent** — calling `detect_issues()` twice in a row never creates duplicate issues (enforced by fingerprint deduplication via `_ensure_issue()`)
3. **Non-destructive** — detectors only read from `Trace`, `Span`, and `SystemMetric`; they write only to `Issue`
4. **Graceful reopen** — if an issue with the same fingerprint was previously RESOLVED, it is reopened instead of rejected

#### Fingerprint Calculation

```python
fp_key = f"{app_name}:{rule_id or issue_type}:{span_name or ''}"
fingerprint = hashlib.sha256(fp_key.encode()).hexdigest()[:16]
```

This means the same logical issue (same app, same rule, same span) will always produce the same fingerprint. If that issue is open, `_ensure_issue()` returns `None` (no-op). If it was resolved, it is reopened with updated fields.

#### Detector Categories

```
detect_issues(db)
├── Section 1: Health & Availability
│   ├── _detect_consecutive_trace_failures(db)      # NFR-2/5
│   ├── _detect_http_error_rate(db, window)         # NFR-8/8a
│   ├── _detect_exception_count_spike(db)           # NFR-9
│   └── _detect_response_time_with_llm(db, window)  # NFR-7/7a
│
├── Section 2: Infrastructure
│   ├── _detect_cpu(db)                             # NFR-11/11a
│   ├── _detect_memory(db)                          # NFR-12/13
│   └── _detect_storage(db)                         # NFR-14/14a
│
├── Section 3: AI & System
│   ├── _detect_execution_time_drift(db)            # NFR-19
│   ├── _detect_consecutive_ai_failures(db)         # NFR-22/22a
│   ├── _detect_genai_failure_rate(db, window)      # NFR-24/24a
│   ├── _detect_timeout_rate(db, window)            # NFR-25/25a
│   └── _detect_token_spike(db)                     # NFR-26
│
├── Section 4: Application Output Errors
│   └── _detect_output_errors(db, window)           # NFR-29
│
└── Legacy
    ├── _detect_high_latency(db)
    └── _detect_error_spikes(db)
```

See [Section 6](#6-nfr-rule-catalogue) for the full catalogue.

---

### 3.4 Metrics Collector — `server/engine/metrics_collector.py`

A background async task that polls `psutil` every 10 seconds and persists to the `system_metrics` table.

#### Collection Loop

```python
async def start():
    global _running
    _running = True
    while _running:
        await _collect()
        await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)  # 10s
```

#### What Is Collected

| Field | Source | Notes |
|---|---|---|
| `cpu_percent` | `psutil.cpu_percent(interval=1)` | System-wide % |
| `cpu_per_core_json` | `psutil.cpu_percent(percpu=True)` | JSON array |
| `cpu_freq_mhz` | `psutil.cpu_freq().current` | MHz |
| `mem_total_mb` | `psutil.virtual_memory().total / 1MB` | |
| `mem_used_mb` | `psutil.virtual_memory().used / 1MB` | |
| `mem_available_mb` | `psutil.virtual_memory().available / 1MB` | |
| `mem_percent` | `psutil.virtual_memory().percent` | |
| `swap_used_mb` | `psutil.swap_memory().used / 1MB` | |
| `swap_percent` | `psutil.swap_memory().percent` | |
| `disk_read_bytes_sec` | delta from `psutil.disk_io_counters()` | bytes/sec |
| `disk_write_bytes_sec` | delta from `psutil.disk_io_counters()` | bytes/sec |
| `disk_read_iops` | delta read_count / elapsed | ops/sec |
| `disk_write_iops` | delta write_count / elapsed | ops/sec |
| `net_bytes_sent_sec` | delta from `psutil.net_io_counters()` | bytes/sec |
| `net_bytes_recv_sec` | delta from `psutil.net_io_counters()` | bytes/sec |
| `net_packets_sent_sec` | delta packets_sent / elapsed | packets/sec |
| `net_packets_recv_sec` | delta packets_recv / elapsed | packets/sec |
| `net_active_connections` | `len(psutil.net_connections())` | count |
| `process_count` | `len(psutil.pids())` | count |

#### Delta Calculation (I/O counters)

psutil returns **cumulative** I/O counters since system boot. The collector stores the previous sample:

```python
elapsed = (now - _prev_time).total_seconds()
disk_read_bytes_sec = (curr.read_bytes - prev.read_bytes) / elapsed
```

On the first collection, delta fields are set to 0 (no previous sample).

#### Retention

The collector keeps the last 720 snapshots (720 × 10s = 2 hours). Older rows are deleted after each write:

```python
count = db.query(SystemMetric).count()
if count > RETENTION_COUNT:  # 720
    oldest = db.query(SystemMetric).order_by(SystemMetric.collected_at).limit(count - RETENTION_COUNT).all()
    for row in oldest:
        db.delete(row)
```

#### Data Access API

| Function | Purpose |
|---|---|
| `get_recent_snapshots(n=18)` | Returns last n snapshots (last 3 minutes at 10s interval) |
| `get_snapshots_around(dt, window_seconds=120)` | Returns snapshots within ±window of given datetime |

---

### 3.5 Reason Analyzer — `server/engine/reason_analyzer.py`

Generates structured LLM root-cause analysis for issues using real system context.

#### Trigger

Automatically triggered for every newly detected issue inside the escalation engine tick:
```python
asyncio.create_task(reason_analyzer.analyze_issue(issue.id))
```

Also triggerable via `POST /api/analysis/issues/{issue_id}?force=true`.

#### Analysis Pipeline

```
analyze_issue(issue_id)
    │
    ├── 1. Fetch issue from DB
    ├── 2. Create or reset IssueAnalysis record (status='pending')
    │
    ├── 3. _build_context(issue, db)
    │       ├── Fetch metric snapshots ±180s around issue.created_at
    │       │     → get_snapshots_around(issue.created_at, window_seconds=360)
    │       ├── Fetch related trace (if issue.trace_id set)
    │       ├── Fetch error spans from that trace
    │       ├── _summarise_metrics(snapshots)
    │       │     → {field: {min, avg, max}} for all numeric fields
    │       └── Recent app error count (last 30 min)
    │
    ├── 4. Build LLM prompt
    │       Issue severity, type, title, description
    │       + Metric summary (min/avg/max per field)
    │       + Span names, durations, error messages
    │       + Instruction: respond as JSON with 4 keys
    │
    ├── 5. LLM call chain (with fallback)
    │       ├── Try Anthropic claude-sonnet-4-6
    │       │     → catch billing/quota errors → skip
    │       ├── Try OpenAI gpt-4o
    │       │     → catch quota errors → skip
    │       └── _rule_based_analysis(issue, context)
    │             → deterministic heuristic fallback
    │             → checks: cpu>85%, mem>85%, error_rate>20%,
    │                        net>10MB/s, conns>200, avg_dur>8000ms
    │
    └── 6. Persist to IssueAnalysis
            status='done', model_used, likely_cause,
            evidence, recommended_action, full_summary,
            context_snapshot_json
```

#### LLM Prompt Structure

```
You are an SRE assistant. Analyze this AI agent issue and return JSON.

Issue:
  Type: nfr_output_error  Severity: high
  Title: Application error returned in output by medical-agent
  Description: 1 recent trace returned an error inside the response body...

System Metrics (min/avg/max over last 6 min):
  cpu_percent: 12.3 / 15.7 / 18.2
  mem_percent: 67.0 / 68.5 / 70.1
  ...

Error Spans:
  - openai_generation (llm): 1410ms | Error: Error code 400 - credit balance too low

Respond ONLY with JSON:
{
  "likely_cause": "...",
  "evidence": "...",
  "recommended_action": "...",
  "confidence": "high|medium|low"
}
```

#### Rule-Based Fallback

When no LLM API is available, deterministic heuristics generate a structured response:

| Condition | Likely Cause |
|---|---|
| `cpu_avg > 85%` | CPU saturation — agent processes competing for CPU |
| `mem_avg > 85%` | Memory pressure — consider increasing container memory limit |
| `error_rate > 20%` | High error rate — check LLM API quota and credentials |
| `net_recv > 10 MB/s` | Elevated network I/O — possible upstream API slowness |
| `connections > 200` | Connection pool pressure — check DB or API connection limits |
| `avg_duration > 8000ms` | Slow LLM responses — check model latency or prompt size |

---

### 3.6 Escalation Engine — `server/engine/escalation_engine.py`

The heartbeat of the system. A background async task that runs every 30 seconds.

#### Tick Sequence

```
_tick()
    │
    ├── 1. detect_issues(db)
    │       → returns list[Issue] (newly created/reopened)
    │       → for each: lf_reporter.report_issue(issue)
    │       → for each: asyncio.create_task(reason_analyzer.analyze_issue(issue.id))
    │
    ├── 2. Load enabled EscalationRules from DB
    ├── 3. Load OPEN + ACKNOWLEDGED Issues from DB
    │
    └── 4. For each (issue, rule) pair:
            if rule.app_name and rule.app_name != issue.app_name: skip
            if _rule_matches(rule, issue):
                await _fire_action(db, rule, issue)

_rule_matches(rule, issue):
    "open_issue_age_gt"          → age_minutes > condition_value
    "severity_gte"               → severity_map[issue.severity] >= int(cv)
    "repeated_error_count_gte"   → issue.escalation_count >= int(cv)
    else: False

_fire_action(db, rule, issue):
    1. Check cooldown: skip if same rule+issue fired in last 1 hour
    2. Execute action:
       "log"             → logger.warning() + EscalationLog
       "escalate_issue"  → issue.status = ESCALATED + EscalationLog
       "webhook"         → fire_webhook(url, payload) + EscalationLog
```

#### Webhook Dispatcher

`fire_webhook()` uses `httpx.AsyncClient` with:
- Configurable timeout (`WEBHOOK_TIMEOUT_SECONDS`, default 10s)
- Up to `WEBHOOK_MAX_RETRIES` (default 3) attempts with exponential backoff (1s, 2s, 4s)
- Supports GET and POST; optional custom headers; configurable body_template

Webhook payload (POST):
```json
{
  "rule": "Escalate Critical Issues",
  "issue_id": 42,
  "app_name": "medical-agent",
  "title": "Application error returned in output",
  "severity": "high",
  "status": "OPEN",
  "created_at": "2026-03-24T10:00:00Z"
}
```

---

### 3.7 AutoFix Agent — `server/engine/autofix_agent.py`

Invokes the Claude Code CLI to autonomously fix code issues in registered agent folders.

#### Job Store

In-memory dict (not persisted across restarts):
```python
_jobs: dict[str, dict] = {
    "abc12345": {
        "job_id": "abc12345",
        "issue_id": 42,
        "app_name": "medical-agent",
        "status": "running",   # running | completed | failed
        "output": "...",        # accumulated stdout/stderr
        "started_at": datetime,
        "ended_at": datetime | None,
    }
}
```

#### App Folder Mapping

```python
_DOCS = Path(__file__).resolve().parents[3]  # Documents folder

APP_FOLDERS = {
    "web-search-agent": str(_DOCS / "WebSearchAgent"),
    "medical-agent":    str(_DOCS / "MedicalAgent"),
    "medical-rag":      str(_DOCS / "MedicalAgent"),
}
```

#### Fix Execution

```python
prompt = f"""
You are fixing a production issue in a Python AI agent.

Issue: {issue.title} (severity={issue.severity})
Description: {issue.description}
Affected trace: {issue.trace_id}
Affected span: {issue.span_name}

The code is in: {folder}

Please:
1. Read the relevant source files
2. Understand the root cause
3. Implement a minimal fix
4. Ensure the fix does not break other functionality
"""

proc = await asyncio.create_subprocess_exec(
    "claude", "--dangerously-skip-permissions", "-p", prompt,
    cwd=folder,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.STDOUT,
)
```

Streams `proc.stdout` to `job["output"]` in real-time. On exit code 0, calls `process_manager.restart(app_name)`.

#### Process Manager

`process_manager.py` maintains a registry of agent processes:

```python
_registry: dict[str, dict] = {
    "web-search-agent": {
        "cmd": ["python", "server.py"],
        "cwd": "/Documents/WebSearchAgent",
        "process": Popen | None,
    }
}
```

`restart(app_name)`:
1. `process.terminate()` → wait up to 5s → `process.kill()` if still alive
2. `await asyncio.sleep(2)`
3. `subprocess.Popen(cmd, cwd=cwd)` → store new process handle

---

### 3.8 Modifier Agent — `server/engine/modifier_agent.py`

An OpenAI GPT-4o powered agent that instruments arbitrary Python agent codebases.

#### Tool Set (function calling)

| Tool | Purpose |
|---|---|
| `list_files(path)` | List files in a directory |
| `read_file(path)` | Read file contents |
| `write_file(path, content)` | Write (overwrite) a file |
| `search_code(pattern, path)` | Grep-like search |

#### Instrumentation Strategy

The agent is given a system prompt explaining:
- What `AIopsCallbackHandler` is and how to use it
- How to detect LangChain/LangGraph patterns (`.invoke()`, `ChatOpenAI`, `AgentExecutor`)
- How to add the callback to existing invocation calls
- How to inject `AIopsConfig` with `app_name` from the detected codebase name

The agent explores the project tree, reads entry points and agent files, identifies where to inject the callback, and writes updated files. All steps stream as Server-Sent Events to the dashboard.

---

### 3.9 Dashboard SPA — `server/dashboard/index.html`

A single HTML file (~1,500 lines) with embedded CSS and JavaScript. Served as a `FileResponse` from FastAPI.

#### Tab Structure

```
Dashboard (/)
├── Overview
│   ├── Stat cards: traces, avg latency, error count/rate, open issues by severity
│   └── System metrics strip chart (CPU%, Memory%)
│
├── Traces
│   ├── Filter bar: app_name, status
│   ├── Table: id, app, status, duration, input, output
│   └── Expandable row: span flame graph, logs panel
│
├── Issues
│   ├── Filter bar: app_name, status, severity
│   ├── Table: #, App, Severity, Rule, Status, Title, Created, Actions, Analysis
│   ├── Action buttons: Ack | Escalate | Resolve | AutoFix
│   └── Analysis column: 🔍 Analyze → expandable panel
│         shows: likely_cause, evidence, recommended_action, model_used
│
├── Metrics
│   ├── Latency: p50/p95/p99 per span (bar chart)
│   ├── Errors: count and rate per span
│   └── System: real-time CPU, memory, disk, network charts
│
├── Escalations
│   ├── Rules CRUD form
│   └── Log table: issue_id, rule, action, status, detail, fired_at
│
└── Agents
    ├── Registered agents list with status badge
    ├── Instrument button → SSE stream
    └── AutoFix job list with live output
```

#### Polling Strategy

JavaScript polls backend endpoints every 2 seconds (on active tabs):
- `GET /api/traces?limit=50` — Traces tab
- `GET /api/issues` — Issues tab
- `GET /api/metrics/latency` + `/errors` — Metrics tab
- `GET /api/analysis/metrics/recent?n=18` — system metric charts

AutoFix polling: every 2 seconds on `GET /api/autofix/{job_id}` until `status != "running"`.

---

## 4. Database Schema

### Entity Relationship Diagram

```
Trace (1) ──────< Span (many)
Trace (1) ──────< TraceLog (many)
Issue (1) ─────── IssueAnalysis (1)
Issue (1) ──────< EscalationLog (many)
EscalationRule (1) ──< EscalationLog (many)
```

### Table: `traces`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | VARCHAR | PK | UUID from SDK |
| app_name | VARCHAR | NOT NULL | Application identifier |
| run_id | VARCHAR | NULL | LangGraph run_id |
| session_id | VARCHAR | NULL | |
| user_id | VARCHAR | NULL | |
| status | VARCHAR | NOT NULL, default "ok" | "ok" \| "error" |
| started_at | DATETIME | NOT NULL, default utcnow | |
| ended_at | DATETIME | NULL | |
| total_duration_ms | FLOAT | NULL | |
| input_preview | TEXT | NULL | Truncated to 500 chars |
| output_preview | TEXT | NULL | Truncated to 500 chars |
| metadata_json | TEXT | NULL | Arbitrary JSON |

### Table: `spans`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | VARCHAR | PK | UUID |
| trace_id | VARCHAR | FK → traces.id | |
| parent_span_id | VARCHAR | NULL | Self-referential |
| name | VARCHAR | NOT NULL | |
| span_type | VARCHAR | default "chain" | "chain" \| "llm" \| "tool" \| "retriever" |
| status | VARCHAR | default "ok" | "ok" \| "error" |
| started_at | DATETIME | NOT NULL | |
| ended_at | DATETIME | NULL | |
| duration_ms | FLOAT | NULL | |
| input_preview | TEXT | NULL | |
| output_preview | TEXT | NULL | |
| error_message | TEXT | NULL | |
| tokens_input | INTEGER | NULL | LLM only |
| tokens_output | INTEGER | NULL | LLM only |
| model_name | VARCHAR | NULL | e.g. "claude-sonnet-4-6" |
| metadata_json | TEXT | NULL | |

### Table: `trace_logs`

| Column | Type | Constraints |
|---|---|---|
| id | INTEGER | PK autoincrement |
| trace_id | VARCHAR | FK → traces.id |
| level | VARCHAR | NOT NULL ("DEBUG"\|"INFO"\|"WARNING"\|"ERROR") |
| logger | VARCHAR | NULL |
| message | TEXT | NOT NULL |
| timestamp | DATETIME | default utcnow |
| metadata_json | TEXT | NULL |

### Table: `issues`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| id | INTEGER | PK autoincrement | |
| app_name | VARCHAR | NOT NULL | |
| issue_type | VARCHAR | NOT NULL | "nfr_*", "high_latency", "error_spike" |
| severity | VARCHAR | NOT NULL | "low"\|"medium"\|"high"\|"critical" |
| status | VARCHAR | default "OPEN" | "OPEN"\|"ACKNOWLEDGED"\|"ESCALATED"\|"RESOLVED" |
| fingerprint | VARCHAR | UNIQUE NOT NULL | sha256[:16] for dedup |
| title | VARCHAR | NOT NULL | |
| description | TEXT | NULL | |
| span_name | VARCHAR | NULL | Which span triggered |
| trace_id | VARCHAR | NULL | Related trace |
| rule_id | VARCHAR | NULL | NFR rule ID |
| created_at | DATETIME | default utcnow | |
| updated_at | DATETIME | default utcnow | |
| acknowledged_at | DATETIME | NULL | |
| resolved_at | DATETIME | NULL | |
| escalation_count | INTEGER | default 0 | |
| metadata_json | TEXT | NULL | |

### Table: `escalation_rules`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| app_name | VARCHAR NULL | NULL = global rule |
| name | VARCHAR | Human label |
| enabled | BOOLEAN default True | |
| condition_type | VARCHAR | "severity_gte"\|"open_issue_age_gt"\|"repeated_error_count_gte"\|"duration_ms_gt"\|"error_rate_gt" |
| condition_value | FLOAT | Threshold |
| condition_span_name | VARCHAR NULL | Filter by span |
| action_type | VARCHAR | "log"\|"webhook"\|"escalate_issue" |
| action_config | TEXT NULL | JSON: {"url", "method", "headers", "body_template"} |
| created_at | DATETIME | |

### Table: `escalation_logs`

| Column | Type |
|---|---|
| id | INTEGER PK |
| issue_id | INTEGER FK → issues.id NULLABLE |
| rule_id | INTEGER FK → escalation_rules.id NULLABLE |
| action_type | VARCHAR |
| status | VARCHAR ("fired"\|"failed"\|"skipped") |
| detail | TEXT NULL |
| fired_at | DATETIME default utcnow |

### Table: `system_metrics`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| collected_at | DATETIME indexed | |
| cpu_percent | FLOAT | |
| cpu_per_core_json | TEXT | JSON array of per-core % |
| cpu_freq_mhz | FLOAT | |
| mem_total_mb | FLOAT | |
| mem_used_mb | FLOAT | |
| mem_available_mb | FLOAT | |
| mem_percent | FLOAT | |
| swap_used_mb | FLOAT | |
| swap_percent | FLOAT | |
| disk_read_bytes_sec | FLOAT | delta rate |
| disk_write_bytes_sec | FLOAT | delta rate |
| disk_read_iops | FLOAT | delta rate |
| disk_write_iops | FLOAT | delta rate |
| net_bytes_sent_sec | FLOAT | delta rate |
| net_bytes_recv_sec | FLOAT | delta rate |
| net_packets_sent_sec | FLOAT | delta rate |
| net_packets_recv_sec | FLOAT | delta rate |
| net_active_connections | INTEGER | |
| process_count | INTEGER | |

### Table: `issue_analyses`

| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| issue_id | INTEGER FK UNIQUE → issues.id | One analysis per issue |
| generated_at | DATETIME | |
| model_used | VARCHAR NULL | "claude-sonnet-4-6", "gpt-4o", "rule-based" |
| status | VARCHAR default "pending" | "pending"\|"done"\|"failed" |
| likely_cause | TEXT NULL | LLM output |
| evidence | TEXT NULL | LLM output |
| recommended_action | TEXT NULL | LLM output |
| full_summary | TEXT NULL | Raw LLM response |
| context_snapshot_json | TEXT NULL | Metrics + trace context at analysis time |

---

## 5. API Reference

### Authentication

Set `AIOPS_API_KEY` in `.env`. All ingest endpoints then require:
```
X-AIops-Key: <your-key>
```
Other endpoints (query, analysis, dashboard) do not currently require authentication (deploy behind a VPN or reverse proxy for production use).

### Endpoint Summary

#### Health
```
GET /health
→ 200 {"status": "ok"|"degraded", "db": true|false}
```

#### Ingest
```
POST /api/ingest/trace          Body: TraceIn
POST /api/ingest/batch          Body: BatchIn
```

#### Traces
```
GET  /api/traces                ?app_name=&status=&limit=50&offset=0
GET  /api/traces/stats
GET  /api/traces/{trace_id}
GET  /api/traces/{trace_id}/logs
```

#### Issues
```
GET    /api/issues              ?app_name=&status=&severity=&limit=50&offset=0
POST   /api/issues              Body: IssueCreate
GET    /api/issues/{id}
PATCH  /api/issues/{id}         Body: IssueUpdate
POST   /api/issues/{id}/acknowledge
POST   /api/issues/{id}/escalate
POST   /api/issues/{id}/resolve
POST   /api/issues/{id}/autofix
```

#### Escalations
```
GET    /api/escalations/rules   ?app_name=
POST   /api/escalations/rules
GET    /api/escalations/rules/{id}
PATCH  /api/escalations/rules/{id}
DELETE /api/escalations/rules/{id}
GET    /api/escalations/logs    ?issue_id=&limit=50
```

#### Metrics
```
GET  /api/metrics/latency       ?app_name=&span_name=
GET  /api/metrics/errors        ?app_name=
GET  /api/metrics/issues-summary
```

#### Analysis
```
POST /api/analysis/issues/{id}  ?force=false
GET  /api/analysis/issues/{id}
GET  /api/analysis/metrics/recent  ?n=18
```

#### Agent / AutoFix
```
POST /api/agent/instrument       Body: {project_path, app_name}  → SSE stream
GET  /api/issues/autofix/{job_id}
GET  /api/autofix-jobs
GET  /api/agent-statuses
```

---

## 6. NFR Rule Catalogue

| Rule ID | Name | Condition | Severity | Window |
|---|---|---|---|---|
| NFR-2 | Consecutive trace failures | Last 3 traces all `status=error` | critical | Last 3 |
| NFR-7 | Response time exceeds target | Avg duration ≥ target (5000ms) | high | 10 min |
| NFR-7a | Response time 2× target | Avg duration ≥ 2× target | critical | 10 min |
| NFR-8 | HTTP error rate ≥1% | error_traces/total ≥ 0.01 | high | 10 min, min 10 traces |
| NFR-8a | HTTP error rate ≥5% | error_traces/total ≥ 0.05 | critical | 10 min, min 10 traces |
| NFR-9 | Exception count doubled | This week errors ≥ 2× last week, min 5 | medium | 7 days |
| NFR-11 | CPU ≥80% | psutil.cpu_percent() ≥ 80 | high | Instantaneous |
| NFR-11a | CPU ≥95% | psutil.cpu_percent() ≥ 95 | critical | Instantaneous |
| NFR-12 | Memory ≥80% | psutil.virtual_memory().percent ≥ 80 | high | Instantaneous |
| NFR-13 | Memory pressure | psutil.virtual_memory().percent ≥ 90 | critical | Instantaneous |
| NFR-14 | Storage ≥80% | psutil.disk_usage('/').percent ≥ 80 | medium | Instantaneous |
| NFR-14a | Storage ≥90% | psutil.disk_usage('/').percent ≥ 90 | high | Instantaneous |
| NFR-19 | Execution time drift +20% | Recent avg > baseline avg × 1.2 | high | Split historical |
| NFR-22 | Consecutive LLM failures × 5 | Last 5 llm spans all error | high | Last 5 spans |
| NFR-22a | Consecutive LLM failures × 10 | Last 10 llm spans all error | critical | Last 10 spans |
| NFR-24 | GenAI failure rate ≥3% | llm_errors/total_llm ≥ 0.03 | high | 10 min, min 5 |
| NFR-24a | GenAI failure rate ≥10% | llm_errors/total_llm ≥ 0.10 | critical | 10 min, min 5 |
| NFR-25 | Timeout rate ≥3% | timeout spans/total ≥ 0.03 | high | 10 min, min 10 |
| NFR-25a | Timeout rate ≥10% | timeout spans/total ≥ 0.10 | critical | 10 min, min 10 |
| NFR-26 | Token spike +50% | This week avg tokens ≥ 1.5× last week | medium | 7 days |
| NFR-29 | Output body error | output_preview contains error patterns | medium–critical | 10 min |

**Severity scaling for NFR-29:**
- 1 trace: medium
- 2–4 traces: high
- ≥5 traces: critical

**Output error patterns scanned (NFR-29):**
`⚠️`, `error code:`, `invalid_request_error`, `invalid_api_key`, `credit balance is too low`, `quota exceeded`, `rate limit exceeded`, `error generating response`

---

## 7. Configuration Reference

All settings are loaded from `.env` (gitignored) by pydantic-settings. Variables are prefixed with `AIOPS_`.

```env
# Server
AIOPS_HOST=0.0.0.0
AIOPS_PORT=7000
AIOPS_DATABASE_URL=sqlite:///./aiops.db
AIOPS_API_KEY=                              # optional; set to require X-AIops-Key

# Detection thresholds
AIOPS_HIGH_LATENCY_MULTIPLIER=3.0
AIOPS_MIN_TRACES_FOR_LATENCY_BASELINE=10
AIOPS_NFR_RESPONSE_TIME_TARGET_MS=5000.0
AIOPS_NFR_CHECK_WINDOW_MINUTES=10

# Escalation engine
AIOPS_ESCALATION_INTERVAL_SECONDS=30
AIOPS_WEBHOOK_TIMEOUT_SECONDS=10.0
AIOPS_WEBHOOK_MAX_RETRIES=3

# Ingest limits
AIOPS_MAX_INGEST_BATCH_SIZE=500

# External services (all optional)
ANTHROPIC_API_KEY=                          # for reason_analyzer (Claude)
OPENAI_API_KEY=                             # for modifier_agent (GPT-4o) + reason_analyzer fallback
AIOPS_LANGFUSE_SECRET_KEY=
AIOPS_LANGFUSE_PUBLIC_KEY=
AIOPS_LANGFUSE_HOST=https://cloud.langfuse.com
```

---

## 8. Data Flow Diagrams

### 8.1 Trace Ingestion → Issue Detection Flow

```
Agent App
  │
  │  SDK.finish_trace()
  │  POST /api/ingest/trace
  ▼
Ingest API
  │  Upsert Trace, Spans, Logs → SQLite
  ▼
(background, every 30s)
Escalation Engine._tick()
  │
  ├── detect_issues(db)
  │       │
  │       ├── Reads: traces, spans (last N minutes)
  │       ├── Reads: psutil (CPU, memory, disk)
  │       └── Writes: Issue (if fingerprint not open)
  │
  ├── evaluate rules
  │       └── Writes: EscalationLog; fires webhook; updates Issue.status
  │
  └── for each new issue:
          asyncio.create_task(reason_analyzer.analyze_issue(id))
                  │
                  ├── Reads: system_metrics (±3 min window)
                  ├── Reads: spans (error spans in related trace)
                  ├── LLM API call (Claude / GPT-4o / rule-based)
                  └── Writes: IssueAnalysis (status='done')
```

### 8.2 AutoFix Flow

```
User → Dashboard → "AutoFix" button
  │
  │  POST /api/issues/{id}/autofix
  ▼
autofix_agent.start_autofix(issue_id)
  │  Returns 202 {"job_id": "abc12345", "status": "running"}
  │
  └── asyncio.create_task(_run_claude_fix(job))
            │
            ├── Build prompt from issue details
            ├── asyncio.create_subprocess_exec(
            │       "claude", "--dangerously-skip-permissions", "-p", prompt
            │       cwd=agent_folder
            │   )
            ├── Stream stdout → job["output"]
            └── On exit 0:
                    process_manager.restart(app_name)

Dashboard → polls GET /api/autofix/{job_id} every 2s
  → shows live output
  → on status='completed': shows "Agent restarted ✓"
```

### 8.3 Metrics Collection Flow

```
(background, every 10s)
metrics_collector._collect()
    │
    ├── psutil.cpu_percent()
    ├── psutil.virtual_memory()
    ├── psutil.disk_io_counters() → delta calculation
    ├── psutil.net_io_counters()  → delta calculation
    ├── psutil.net_connections()
    └── Write: SystemMetric row → SQLite

    → Prune: delete oldest rows if count > 720

Dashboard → polls GET /api/analysis/metrics/recent?n=18 every 2s
  → renders strip chart (CPU%, Memory%)
```

---

## 9. Security Model

### Current Implementation

| Surface | Protection |
|---|---|
| Ingest endpoints | Optional static API key (`X-AIops-Key`) |
| All other endpoints | No authentication (intended for internal use) |
| Database | Local SQLite file; not exposed over network |
| AutoFix | Executes Claude Code CLI with `--dangerously-skip-permissions` in agent folder |
| Modifier Agent | Reads and writes agent source files |

### Production Hardening Recommendations

1. **Deploy behind a reverse proxy** (nginx, Caddy, or a cloud load balancer) with TLS termination and IP allowlisting.
2. **Enable API key authentication** (`AIOPS_API_KEY`) and rotate regularly.
3. **Run AutoFix in a sandboxed environment** — consider a read-only mount for source files with a separate write volume for patches.
4. **Migrate to PostgreSQL** for multi-process deployments or high write throughput.
5. **Restrict dashboard access** via OAuth/SSO at the reverse proxy layer.
6. **Never commit `.env`** — use a secrets manager (Vault, AWS Secrets Manager) in production.

---

## 10. Deployment Guide

### Local Development

```bash
# 1. Create venv
python -m venv venv
source venv/Scripts/activate   # Windows
# source venv/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .   # SDK in editable mode

# 3. Configure
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY, OPENAI_API_KEY

# 4. Start server
python -m uvicorn server.main:app --host 0.0.0.0 --port 7000 --reload

# 5. Visit dashboard
open http://localhost:7000
```

### Running Tests

```bash
pytest                              # full suite
pytest --cov=server --cov=aiops_sdk --cov-report=term-missing
pytest tests/unit/
pytest tests/integration/
pytest tests/unit/engine/test_issue_detector.py -v
```

### Production (Docker — example)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install -e .
EXPOSE 7000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "7000"]
```

```yaml
# docker-compose.yml
services:
  aiops:
    build: .
    ports:
      - "7000:7000"
    volumes:
      - ./data:/app/data          # persist SQLite
      - ./.env:/app/.env
    environment:
      - AIOPS_DATABASE_URL=sqlite:///./data/aiops.db
    restart: unless-stopped
```

---

## 11. Extension Points

### Adding a New NFR Detector

1. Write a failing test in `tests/unit/engine/test_issue_detector.py`
2. Add a function `_detect_<name>(db: Session) -> list[Issue]` in `issue_detector.py`
3. Call it from `detect_issues()` with a comment: `# NFR-<number>`
4. Add the rule to the [NFR catalogue](#6-nfr-rule-catalogue)

### Adding a New Escalation Action Type

1. Add the action type string to the validation list in `server/api/escalations.py`
2. Add a handler branch in `_fire_action()` in `escalation_engine.py`
3. Update the dashboard escalation form to offer the new action type

### Adding a New Agent App

Register it in `server/main.py` `_register_agents()`:
```python
process_manager.register(
    "my-agent",
    cmd=[python, str(_DOCS / "MyAgent" / "run.py")],
    cwd=str(_DOCS / "MyAgent"),
)
```
Add it to `APP_FOLDERS` in `autofix_agent.py`:
```python
"my-agent": str(_DOCS / "MyAgent"),
```

### Custom LLM in Reason Analyzer

The LLM call chain is:
```
Anthropic claude-sonnet-4-6
    → OpenAI gpt-4o
        → rule-based fallback
```
To add a new model: add a `try/except` block between OpenAI and rule-based in `_run_analysis()`.

### Migrating from SQLite to PostgreSQL

Change `.env`:
```
AIOPS_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/aiops
```
Install: `pip install psycopg2-binary`
Drop the `connect_args={"check_same_thread": False}` guard in `engine.py` (SQLite-only).

---

*For business context and customer pain points, see [BUSINESS_IMPACT.md](./BUSINESS_IMPACT.md).*
