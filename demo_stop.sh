#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QUIET=0
if [ "${1:-}" = "--quiet" ]; then
  QUIET=1
fi
LOG_PREFIX="demo-stop"
source "${ROOT_DIR}/scripts/demo_lib.sh"
init_demo_runtime

stop_pid_file "invastigate-monitor-ui"
stop_pid_file "sample-agent-steady-load"
stop_pid_file "aiops-preview-launcher"
stop_pid_file "aiops-preview"
stop_pid_file "aiops-remediation"
stop_pid_file "invastigate-rca"
stop_pid_file "aiops-telemetry"

stop_matching_processes "monitor UI" "${ROOT_DIR}/Invastigate_flow_with_Poller/monitor-ui/node_modules/.bin/vite"
stop_matching_processes "SampleAgent steady load" "${ROOT_DIR}/MedicalAgent/scripts/run_steady_background_load.py"
stop_matching_processes "AIOps preview launcher" "${ROOT_DIR}/aiops_preview_launcher.py"
stop_matching_processes "AIOps preview page" "python3 -m http.server 8088 --bind"
stop_matching_processes "AIOPS remediation" "${ROOT_DIR}/AIOPS.*app.web:app"
stop_matching_processes "Invastigate RCA" "${ROOT_DIR}/Invastigate_flow_with_Poller.*app.main:app"
stop_matching_processes "AIopsTelemetry" "${ROOT_DIR}/AIopsTelemetry.*run.py"

if command -v docker >/dev/null 2>&1; then
  info "Stopping sample-agent Docker stack"
  (cd "${ROOT_DIR}/MedicalAgent" && docker compose down --remove-orphans) || warn "MedicalAgent docker compose down failed"
else
  warn "docker not found; skipping MedicalAgent compose stack"
fi

info "Demo applications stopped."
