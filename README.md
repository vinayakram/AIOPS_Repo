# AIOPS POC Demo Workspace

This repository contains the POCDEMO1 workspace for the AIOps remediation demo.
It groups the remediation service, telemetry service, investigation flow, and
sample medical RAG application used in the end-to-end demonstration.

## Projects

- `AIOPS/` - remediation POC service and UI.
- `AIopsTelemetry/` - telemetry SDK, issue detection service, and dashboard.
- `Invastigate_flow_with_Poller/` - investigation and polling flow service.
- `MedicalAgent/` - sample medical RAG application used for remediation demos.
- `MedicalAgent_GitHub/` - GitHub-oriented copy of the medical RAG sample.
- `architecture/` - architecture diagrams and supporting documentation assets.

## Security Notes

Runtime secrets and machine-specific files are intentionally excluded from Git:

- `.env` and `.env.*`
- local Git metadata and credentials
- virtual environments
- sqlite databases
- logs, caches, backup files, run artifacts, and zip exports

Use each project's `.env.example` as the template for local configuration.
