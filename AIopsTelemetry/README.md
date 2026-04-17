# AIops Telemetry

> Purpose-built observability and automated-remediation platform for AI agent applications.

AIops Telemetry collects distributed traces from LangGraph / LangChain agents, detects issues using 21 NFR-based rules, generates LLM-powered root-cause analysis correlated with real system metrics, and can autonomously fix broken agent code using Claude Code — all from a single self-hosted server with a dark-mode dashboard.

---

## Features

| Feature | Description |
|---|---|
| **Trace ingestion** | REST API accepts traces, spans, and logs from any instrumented agent |
| **21 NFR detectors** | Latency, error rate, CPU/memory/disk, LLM failures, token spikes, output-body errors |
| **System metrics** | CPU, memory, disk I/O, network sampled every 10 s; 2-hour rolling window |
| **LLM root-cause analysis** | Claude Sonnet → GPT-4o → rule-based fallback; correlated with system metrics at time of failure |
| **Escalation rules engine** | Configurable rules fire webhooks, auto-escalate issues, or log events |
| **AutoFix** | One-click Claude Code CLI fix in the agent source folder; agent auto-restarts on success |
| **Zero-code instrumentation** | GPT-4o modifier agent injects the AIops SDK into any Python agent codebase |
| **Dashboard SPA** | Vanilla-JS single-page app — Overview, Traces, Issues, Metrics, Escalations, Agents |

---

## Architecture

```
Agent Apps (MedicalAgent, WebSearchAgent, …)
       │  aiops_sdk (callback handler / manual SDK)
       │  POST /api/ingest/trace
       ▼
AIops Telemetry Server :7000
  ├── FastAPI REST API
  ├── SQLite database  (Trace, Span, Issue, EscalationRule, SystemMetric, IssueAnalysis, …)
  ├── Escalation Engine  (every 30 s — detect issues + evaluate rules)
  ├── Metrics Collector  (every 10 s — CPU / mem / disk / net via psutil)
  ├── Reason Analyzer    (async LLM root-cause per new issue)
  ├── AutoFix Agent      (Claude Code CLI in agent source dir)
  └── Dashboard SPA      (index.html served by FastAPI)
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (optional — rule-based fallback works without them)

### Install

```bash
git clone https://github.com/kannan-prodapt/AIopsTelemetry.git
cd AIopsTelemetry

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .                # install SDK in editable mode

cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY, OPENAI_API_KEY (optional)
```

### Run

```bash
uvicorn server.main:app --host 0.0.0.0 --port 7000 --reload
```

Open **http://localhost:7000** — the dashboard loads immediately.

---

## SDK Usage

### Option A — LangChain / LangGraph (recommended)

```python
from aiops_sdk import AIopsCallbackHandler

result = graph.invoke(
    {"messages": [HumanMessage(content=query)]},
    config={"callbacks": [AIopsCallbackHandler()]},
)
```

### Option B — Manual instrumentation

```python
from aiops_sdk import AIopsClient, AIopsConfig

client = AIopsClient.configure(AIopsConfig(app_name="my-agent"))
trace_id = client.start_trace(input_preview=query)
# … do work …
client.finish_trace(trace_id, output_preview=result, status="ok")
```

### Environment variables for SDK

```env
AIOPS_SERVER_URL=http://localhost:7000   # default
AIOPS_APP_NAME=my-agent
AIOPS_API_KEY=                           # optional; matches server AIOPS_API_KEY
```

---

## NFR Rule Catalogue

| Rule | Condition | Severity |
|---|---|---|
| NFR-2 | 3 consecutive trace failures | critical |
| NFR-7 / 7a | Avg response time ≥ 5 s / 10 s | high / critical |
| NFR-8 / 8a | HTTP error rate ≥ 1% / 5% | high / critical |
| NFR-9 | Exception count doubled week-over-week | medium |
| NFR-11 / 11a | CPU ≥ 80% / 95% | high / critical |
| NFR-12 / 13 | Memory ≥ 80% / 90% | high / critical |
| NFR-14 / 14a | Disk ≥ 80% / 90% | medium / high |
| NFR-19 | Execution time +20% above baseline | high |
| NFR-22 / 22a | 5 / 10 consecutive LLM failures | high / critical |
| NFR-24 / 24a | GenAI failure rate ≥ 3% / 10% | high / critical |
| NFR-25 / 25a | Timeout rate ≥ 3% / 10% | high / critical |
| NFR-26 | Token count +50% week-over-week | medium |
| NFR-29 | Error message in output body (status=ok) | medium–critical |

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Server health check |
| `POST` | `/api/ingest/trace` | Ingest a single trace with spans and logs |
| `POST` | `/api/ingest/batch` | Ingest up to 500 traces |
| `GET` | `/api/traces` | List traces (filter: app_name, status) |
| `GET` | `/api/issues` | List issues (filter: app_name, status, severity) |
| `POST` | `/api/issues/{id}/autofix` | Trigger AutoFix via Claude Code CLI |
| `POST` | `/api/analysis/issues/{id}` | Trigger LLM root-cause analysis |
| `GET` | `/api/analysis/issues/{id}` | Retrieve analysis result |
| `GET` | `/api/escalations/rules` | List escalation rules |
| `POST` | `/api/escalations/rules` | Create escalation rule |
| `GET` | `/api/metrics/latency` | p50 / p95 / p99 latency per span |
| `GET` | `/api/analysis/metrics/recent` | Recent system metric snapshots |

Full API docs: **http://localhost:7000/docs**

---

## Configuration

All settings in `.env` — prefix `AIOPS_`:

```env
AIOPS_PORT=7000
AIOPS_DATABASE_URL=sqlite:///./aiops.db
AIOPS_API_KEY=                              # optional ingest auth

AIOPS_NFR_RESPONSE_TIME_TARGET_MS=5000.0
AIOPS_NFR_CHECK_WINDOW_MINUTES=10
AIOPS_ESCALATION_INTERVAL_SECONDS=30

ANTHROPIC_API_KEY=                          # for LLM root-cause analysis
OPENAI_API_KEY=                             # fallback / modifier agent
```

---

## Development

```bash
# Run tests
pytest

# With coverage
pytest --cov=server --cov=aiops_sdk --cov-report=term-missing

# Lint / format / types
ruff check .
black --check .
mypy server/ aiops_sdk/
```

Coverage targets: SDK 100%, engine ≥ 90%, API ≥ 85%, database 100%.

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for the TDD workflow and [docs/BRANCHING.md](docs/BRANCHING.md) for branch naming conventions.

---

## Documentation

| Document | Description |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | Full technical design — architecture, schema, data flows, extension points |
| [docs/BUSINESS_IMPACT.md](docs/BUSINESS_IMPACT.md) | Customer pain points and business value |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | TDD workflow, test tiers, code quality rules |
| [docs/BRANCHING.md](docs/BRANCHING.md) | Branch strategy, commit conventions, PR checklist |

---

## Related Projects

- [WebSearchAgent](https://github.com/kannan-prodapt/WebSearchAgent) — LangGraph web search agent instrumented with this SDK
- [MedicalAgent](https://github.com/kannan-prodapt/MedicalAgent) — PubMed RAG pipeline instrumented with this SDK

---

## License

MIT
