# AIOPS_Repo

AIOPS_Repo is a local proof-of-concept workspace for an agentic AIOps flow:
detect an operational issue, run root-cause analysis, recommend a remediation,
gate sensitive action behind human approval, and track the result.

For the product narrative, see [PRODUCT.md](PRODUCT.md).

## Repository Map

- `AIopsTelemetry/` - telemetry ingestion, issue detection, escalation rules, dashboards, and incident APIs.
- `Invastigate_flow_with_Poller/` - RCA and correlation service that polls incidents and runs the investigation agent flow.
- `AIOPS/` - remediation service that turns approved plans into code changes and pull requests.
- `MedicalAgent/` - sample RAG application used as the monitored workload.
- `MCPObservability/` - stdio MCP server exposing bounded Prometheus and Langfuse evidence tools.
- `SampleAgent_GitHub/` - GitHub-facing copy of the sample app used by remediation workflows.
- `demo/` - local demo launcher, preview page, presenter scripts, and shared demo helpers.

## Quick Start

From the repository root:

```bash
./demo/start.sh
```

This starts the local demo stack:

- Sample Agent on `http://localhost:8002`
- Triage Agent dependent workload on `http://localhost:8010`
- AIopsTelemetry on `http://localhost:7000`
- Japanese conversational workbench on `http://localhost:7000/conversation_j`
- RCA service on `http://localhost:8000`
- Remediation service on `http://localhost:8005`
- Monitor UI on `http://localhost:5173`
- Demo preview page on `http://localhost:8088/aiops_preview.html`
- Preview launcher health on `http://localhost:8765/health`

To stop everything:

```bash
./demo/stop.sh
```

## Useful Commands

Start faster without rebuilding the sample Docker image:

```bash
REBUILD_MEDICAL=0 ./demo/start.sh
```

Start with steady background load:

```bash
STEADY_LOAD_ENABLED=1 ./demo/start.sh
```

Run the pod-pressure scenario directly:

```bash
./demo/sample_agent_pod_pressure.sh
```

Run the cross-service cascade scenario, where `triage-agent` fails because its
upstream `sample-agent` is returning threshold-guard failures:

```bash
cd MedicalAgent
./scripts/run_cascade_threshold_scenario.sh
```

Run the observability MCP server for RCA agents:

```bash
python3 MCPObservability/server.py
```

Start the optional PostgreSQL + pgvector RCA knowledge store:

```bash
docker compose -f docker-compose.rca-kb.yml up -d
```

Then point AIopsTelemetry at it:

```env
AIOPS_DATABASE_URL=postgresql+psycopg://aiops:aiops@localhost:5432/aiops
```

Check the main services:

```bash
curl -fsS http://localhost:7000/health
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8005/
curl -fsS http://localhost:8002/api/health
curl -fsS http://localhost:8010/api/health
```

Open the conversational AIOps flow:

```bash
http://localhost:7000/conversation_j
```

If the telemetry service has not been restarted after a code change, the static fallback is:

```bash
http://localhost:7000/static/conversation_j.html
```

Logs are written under:

```bash
.runtime/logs
```

## Configuration

Copy each module's example environment file before local development:

```bash
cp AIopsTelemetry/.env.example AIopsTelemetry/.env
cp MedicalAgent/.env.example MedicalAgent/.env
cp SampleAgent_GitHub/.env.example SampleAgent_GitHub/.env
cp Invastigate_flow_with_Poller/.env.example Invastigate_flow_with_Poller/.env
```

Important settings:

- `OPENAI_API_KEY` / `OPENAI_MODEL` for sample-agent LLM calls.
- `AIOPS_OPENAI_API_KEY` for telemetry analysis/remediation support.
- `WEB_SEARCH_AGENT_DIR` and `SAMPLE_AGENT_DIR` when agent code is outside this repo.
- `CORS_ORIGINS` for service CORS policy; `*` is intended only for local development.

## Development Checks

Useful lightweight checks before committing:

```bash
python3 -m py_compile \
  AIopsTelemetry/server/main.py \
  AIopsTelemetry/server/engine/autofix_agent.py \
  MedicalAgent/backend/main.py \
  SampleAgent_GitHub/backend/main.py \
  Invastigate_flow_with_Poller/app/main.py

bash -n demo/start.sh demo/stop.sh demo/scripts/demo_lib.sh
```

Run module tests where environments are available:

```bash
python3 -m pytest AIopsTelemetry/tests
python3 -m pytest Invastigate_flow_with_Poller/tests
python3 -m pytest AIOPS/tests
```

## Git Hygiene

The repo intentionally ignores runtime and machine-local artifacts:

- `.env` files and credentials
- virtual environments
- sqlite databases
- logs and runtime output
- generated diagram renders
- managed remediation worktrees

Keep product docs in `PRODUCT.md` and operational setup in this README.
