#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_RUNTIME_DIR="${ROOT_DIR}/.runtime"
if [ -n "${DEMO_RUNTIME_DIR:-}" ]; then
  RUNTIME_DIR="${DEMO_RUNTIME_DIR}"
elif [ -e "${DEFAULT_RUNTIME_DIR}" ] && [ ! -w "${DEFAULT_RUNTIME_DIR}" ]; then
  RUNTIME_DIR="${ROOT_DIR}/.runtime-${USER:-$(id -un 2>/dev/null || echo user)}"
else
  RUNTIME_DIR="${DEFAULT_RUNTIME_DIR}"
fi
QUIET=0
if [ "${1:-}" = "--quiet" ]; then
  QUIET=1
fi

info() {
  if [ "${QUIET}" != "1" ]; then
    printf '[demo-stop] %s\n' "$*"
  fi
}
warn() {
  printf '[demo-stop] WARN: %s\n' "$*" >&2
}

stop_pid_file() {
  local name="$1"
  local pid_file="${RUNTIME_DIR}/${name}.pid"

  if [ ! -f "${pid_file}" ]; then
    info "${name}: no PID file"
    return
  fi

  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [ -z "${pid}" ] || ! kill -0 "${pid}" 2>/dev/null; then
    info "${name}: not running"
    rm -f "${pid_file}"
    return
  fi

  info "Stopping ${name} (PID ${pid})"
  kill -TERM "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true

  local i
  for ((i = 1; i <= 20; i++)); do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${pid_file}"
      info "${name}: stopped"
      return
    fi
    sleep 1
  done

  warn "${name}: still running after graceful stop; forcing"
  kill -KILL "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null || true
  rm -f "${pid_file}"
}

stop_matching_processes() {
  local label="$1"
  local pattern="$2"

  if ! command -v pgrep >/dev/null 2>&1; then
    return
  fi

  local pids
  pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
  if [ -z "${pids}" ]; then
    return
  fi

  info "Stopping leftover ${label} processes"
  # shellcheck disable=SC2086
  kill -TERM ${pids} 2>/dev/null || true
  sleep 2
  pids="$(pgrep -f "${pattern}" 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    # shellcheck disable=SC2086
    kill -KILL ${pids} 2>/dev/null || true
  fi
}

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
