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
LOG_DIR="${RUNTIME_DIR}/logs"
mkdir -p "${LOG_DIR}"

HOST="${HOST:-0.0.0.0}"
AI_OPS_PORT="${AI_OPS_PORT:-7000}"
RCA_PORT="${RCA_PORT:-8000}"
REMEDIATION_PORT="${REMEDIATION_PORT:-8005}"
MONITOR_UI_PORT="${MONITOR_UI_PORT:-5173}"
PREVIEW_PORT="${PREVIEW_PORT:-8088}"
PREVIEW_LAUNCHER_PORT="${PREVIEW_LAUNCHER_PORT:-8765}"
MEDICAL_URL="${MEDICAL_URL:-http://localhost:8002}"
AIOPS_URL="${AIOPS_URL:-http://localhost:${AI_OPS_PORT}}"
RCA_URL="${RCA_URL:-http://localhost:${RCA_PORT}}"
REMEDIATION_URL="${REMEDIATION_URL:-http://localhost:${REMEDIATION_PORT}}"
MONITOR_UI_URL="${MONITOR_UI_URL:-http://localhost:${MONITOR_UI_PORT}}"
PREVIEW_PUBLIC_HOST="${PREVIEW_PUBLIC_HOST:-10.169.91.16}"
PREVIEW_URL="${PREVIEW_URL:-http://${PREVIEW_PUBLIC_HOST}:${PREVIEW_PORT}/aiops_preview.html}"
PREVIEW_HEALTH_URL="${PREVIEW_HEALTH_URL:-http://localhost:${PREVIEW_PORT}/aiops_preview.html}"
PREVIEW_LAUNCHER_URL="${PREVIEW_LAUNCHER_URL:-http://localhost:${PREVIEW_LAUNCHER_PORT}/health}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9092}"
REBUILD_MEDICAL="${REBUILD_MEDICAL:-1}"
STEADY_LOAD_ENABLED="${STEADY_LOAD_ENABLED:-0}"
STEADY_LOAD_USERS="${STEADY_LOAD_USERS:-1}"
STEADY_LOAD_WORK_MS="${STEADY_LOAD_WORK_MS:-300}"
STEADY_LOAD_PAUSE_MS="${STEADY_LOAD_PAUSE_MS:-800}"

info() { printf '[demo-start] %s\n' "$*"; }
warn() { printf '[demo-start] WARN: %s\n' "$*" >&2; }
die() { printf '[demo-start] ERROR: %s\n' "$*" >&2; exit 1; }

need_file() {
  [ -e "$1" ] || die "Required path not found: $1"
}

is_running() {
  local pid_file="$1"
  [ -f "${pid_file}" ] || return 1
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  [ -n "${pid}" ] && kill -0 "${pid}" 2>/dev/null
}

start_background() {
  local name="$1"
  local workdir="$2"
  local pid_file="${RUNTIME_DIR}/${name}.pid"
  local log_file="${LOG_DIR}/${name}.log"
  shift 2

  need_file "${workdir}"
  if is_running "${pid_file}"; then
    info "${name} already running with PID $(cat "${pid_file}")"
    return
  fi
  rm -f "${pid_file}"

  info "Starting ${name}; log: ${log_file}"
  (
    cd "${workdir}"
    nohup setsid "$@" > "${log_file}" 2>&1 &
    echo $! > "${pid_file}"
  )
  sleep 1
  if ! is_running "${pid_file}"; then
    warn "${name} did not stay running. Last log lines:"
    tail -n 20 "${log_file}" 2>/dev/null || true
    return 1
  fi
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-30}"

  if ! command -v curl >/dev/null 2>&1; then
    warn "curl not found; skipping ${name} health check (${url})"
    return
  fi

  local i
  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      info "${name} is reachable at ${url}"
      return
    fi
    sleep 2
  done
  warn "${name} did not respond at ${url} after $((attempts * 2))s"
}

url_reachable() {
  local url="$1"
  command -v curl >/dev/null 2>&1 && curl -fsS "${url}" >/dev/null 2>&1
}

if [ -x "${ROOT_DIR}/demo_stop.sh" ]; then
  info "Stopping any previously started demo services first"
  DEMO_RUNTIME_DIR="${RUNTIME_DIR}" "${ROOT_DIR}/demo_stop.sh" --quiet || true
fi

info "Starting sample-agent Docker stack"
if command -v docker >/dev/null 2>&1; then
  if [ "${REBUILD_MEDICAL}" = "0" ]; then
    (cd "${ROOT_DIR}/MedicalAgent" && docker compose up -d)
  else
    (cd "${ROOT_DIR}/MedicalAgent" && docker compose up -d --build)
  fi
else
  warn "docker not found; skipping MedicalAgent compose stack"
fi

if url_reachable "${AIOPS_URL}/health"; then
  info "aiops-telemetry already reachable at ${AIOPS_URL}; skipping start"
else
  start_background \
    "aiops-telemetry" \
    "${ROOT_DIR}/AIopsTelemetry" \
    "${ROOT_DIR}/AIopsTelemetry/.venv/bin/python" run.py --host "${HOST}" --port "${AI_OPS_PORT}"
fi

if url_reachable "${RCA_URL}/health"; then
  info "invastigate-rca already reachable at ${RCA_URL}; skipping start"
else
  start_background \
    "invastigate-rca" \
    "${ROOT_DIR}/Invastigate_flow_with_Poller" \
    env \
      PROMETHEUS_URL="${PROMETHEUS_URL}" \
      AIOPS_SERVER_URL="${AIOPS_URL}" \
      AIOPS_POLL_ENDPOINT="/api/v1/incidents" \
      "${ROOT_DIR}/Invastigate_flow_with_Poller/.venv/bin/uvicorn" app.main:app --host "${HOST}" --port "${RCA_PORT}"
fi

if url_reachable "${REMEDIATION_URL}/"; then
  info "aiops-remediation already reachable at ${REMEDIATION_URL}; skipping start"
else
  start_background \
    "aiops-remediation" \
    "${ROOT_DIR}/AIOPS" \
    env \
      APP_HOST="${HOST}" \
      APP_PORT="${REMEDIATION_PORT}" \
      "${ROOT_DIR}/AIOPS/.venv-linux/bin/python" -m uvicorn app.web:app --host "${HOST}" --port "${REMEDIATION_PORT}"
fi

if [ -d "${ROOT_DIR}/Invastigate_flow_with_Poller/monitor-ui/node_modules" ]; then
  if url_reachable "${MONITOR_UI_URL}"; then
    info "invastigate-monitor-ui already reachable at ${MONITOR_UI_URL}; skipping start"
  else
    start_background \
      "invastigate-monitor-ui" \
      "${ROOT_DIR}/Invastigate_flow_with_Poller/monitor-ui" \
      npm run dev -- --host "${HOST}" --port "${MONITOR_UI_PORT}"
  fi
else
  warn "monitor-ui/node_modules not found; skipping monitor UI. Run npm install in Invastigate_flow_with_Poller/monitor-ui if needed."
fi

if url_reachable "${PREVIEW_HEALTH_URL}"; then
  info "aiops-preview already reachable at ${PREVIEW_HEALTH_URL}; skipping start"
else
  start_background \
    "aiops-preview" \
    "${ROOT_DIR}" \
    python3 -m http.server "${PREVIEW_PORT}" --bind "${HOST}"
fi

if url_reachable "${PREVIEW_LAUNCHER_URL}"; then
  info "aiops-preview-launcher already reachable at ${PREVIEW_LAUNCHER_URL}; skipping start"
else
  start_background \
    "aiops-preview-launcher" \
    "${ROOT_DIR}" \
    python3 aiops_preview_launcher.py
fi

wait_for_url "sample-agent" "${MEDICAL_URL}/api/health" 45

if [ "${STEADY_LOAD_ENABLED}" = "1" ]; then
  start_background \
    "sample-agent-steady-load" \
    "${ROOT_DIR}/MedicalAgent" \
    python3 scripts/run_steady_background_load.py \
      --base-url "${MEDICAL_URL}" \
      --users "${STEADY_LOAD_USERS}" \
      --work-ms "${STEADY_LOAD_WORK_MS}" \
      --pause-ms "${STEADY_LOAD_PAUSE_MS}" \
      --startup-delay 2
fi

wait_for_url "AIopsTelemetry" "${AIOPS_URL}/health" 30
wait_for_url "Invastigate RCA" "${RCA_URL}/health" 30
wait_for_url "AIOPS remediation" "${REMEDIATION_URL}/" 30
wait_for_url "Invastigate monitor UI" "${MONITOR_UI_URL}" 15
wait_for_url "AIOps preview" "${PREVIEW_HEALTH_URL}" 15
wait_for_url "AIOps preview launcher" "${PREVIEW_LAUNCHER_URL}" 15

cat <<EOF

Demo applications started.
  sample-agent        ${MEDICAL_URL}
  AIopsTelemetry      ${AIOPS_URL}
  Invastigate RCA     ${RCA_URL}
  AIOPS remediation   ${REMEDIATION_URL}
  Monitor UI          ${MONITOR_UI_URL}
  Preview page        ${PREVIEW_URL}
  Preview launcher    ${PREVIEW_LAUNCHER_URL}
  Steady load         users=${STEADY_LOAD_USERS} work_ms=${STEADY_LOAD_WORK_MS} pause_ms=${STEADY_LOAD_PAUSE_MS}

Logs: ${LOG_DIR}
Stop: ${ROOT_DIR}/demo_stop.sh
EOF
