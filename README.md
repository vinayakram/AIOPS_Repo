# AIOPS POC Demo Workspace

This repository contains the POCDEMO1 workspace for the AIOps remediation demo.
It groups the remediation service, telemetry service, investigation flow, and
sample medical RAG application used in the end-to-end demonstration.

## Projects

- `AIOPS/` - remediation POC service and UI.
- `AIopsTelemetry/` - telemetry SDK, issue detection service, and dashboard.
- `Invastigate_flow_with_Poller/` - investigation and polling flow service.
- `MedicalAgent/` - sample medical RAG application used for remediation demos.
- `SampleAgent_GitHub/` - GitHub-oriented copy of the sample RAG app used by remediation.
- `architecture/` - architecture diagrams and supporting documentation assets.

## Demo Runbook

Use the scripts in `demo/` for the AIOps demo. They start and stop the full
demo stack in a predictable order.

### 1. Start the demo services

From the workspace root:

```bash
cd /home/support/Documents/POCDEMO1
./demo/start.sh
```

What `demo/start.sh` starts by default:

- `MedicalAgent` Docker stack on port `8002`
- `AIopsTelemetry` on port `7000`
- `Invastigate_flow_with_Poller` RCA service on port `8000`
- `AIOPS` remediation service on port `8005`
- `Invastigate_flow_with_Poller/monitor-ui` Vite UI on port `5173`
- `demo/aiops_preview.html` static page on port `8088`
- `demo/aiops_preview_launcher.py` on port `8765`

The script first runs `./demo/stop.sh --quiet` to clean up an older demo run,
then starts the services and waits for the health URLs to respond.

### 2. Demo URLs

Default local URLs after startup:

- Sample Agent: `http://localhost:8002`
- Sample Agent health: `http://localhost:8002/api/health`
- AIopsTelemetry: `http://localhost:7000`
- AIopsTelemetry health: `http://localhost:7000/health`
- Invastigate RCA: `http://localhost:8000`
- Invastigate RCA health: `http://localhost:8000/health`
- AIOPS remediation: `http://localhost:8005`
- Monitor UI: `http://localhost:5173`
- Preview page local: `http://localhost:8088/aiops_preview.html`
- Preview launcher health: `http://localhost:8765/health`

Default public preview URL used by the script:

- Preview page public: `http://10.169.91.16:8088/aiops_preview.html`

### 3. Useful startup options

Skip Docker rebuild if you want a faster restart:

```bash
cd /home/support/Documents/POCDEMO1
REBUILD_MEDICAL=0 ./demo/start.sh
```

Enable steady background load during the demo:

```bash
cd /home/support/Documents/POCDEMO1
STEADY_LOAD_ENABLED=1 ./demo/start.sh
```

Logs are written under:

```bash
/home/support/Documents/POCDEMO1/.runtime/logs
```

### 4. Stop the demo services

From the workspace root:

```bash
cd /home/support/Documents/POCDEMO1
./demo/stop.sh
```

What `demo/stop.sh` stops:

- `invastigate-monitor-ui`
- `sample-agent-steady-load`
- `aiops-preview-launcher`
- `aiops-preview`
- `aiops-remediation`
- `invastigate-rca`
- `aiops-telemetry`
- `MedicalAgent` Docker stack via `docker compose down --remove-orphans`

The script uses PID files first, then falls back to matching leftover processes
started from this repo path.

### 5. Recommended stop verification

After stopping, verify the main demo endpoints are down:

```bash
curl -I http://localhost:7000/health
curl -I http://localhost:8000/health
curl -I http://localhost:8005/
curl -I http://localhost:8088/aiops_preview.html
curl -I http://localhost:8765/health
```

If the stop was successful, these should fail to connect.

## Security Notes

Runtime secrets and machine-specific files are intentionally excluded from Git:

- `.env` and `.env.*`
- local Git metadata and credentials
- virtual environments
- sqlite databases
- logs, caches, backup files, run artifacts, and zip exports

Use each project's `.env.example` as the template for local configuration.
