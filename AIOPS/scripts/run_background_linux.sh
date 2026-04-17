#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/.runtime"
LOG_FILE="${RUNTIME_DIR}/codex-remediation.log"
PID_FILE="${RUNTIME_DIR}/codex-remediation.pid"

mkdir -p "${RUNTIME_DIR}"

if [ -f "${PID_FILE}" ]; then
  PID="$(cat "${PID_FILE}")"
  if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
    echo "Service is already running with PID ${PID}"
    echo "Log file: ${LOG_FILE}"
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

cd "${ROOT_DIR}"

if [ -f "${ROOT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

if [ -f "${ROOT_DIR}/.pyenv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.pyenv/bin/activate"
fi

APP_HOST="${APP_HOST:-0.0.0.0}"
APP_PORT="${APP_PORT:-8000}"

nohup python -m uvicorn app.web:app --host "${APP_HOST}" --port "${APP_PORT}" > "${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"

echo "Service started in background."
echo "PID: $(cat "${PID_FILE}")"
echo "Log file: ${LOG_FILE}"
