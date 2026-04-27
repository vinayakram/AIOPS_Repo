#!/usr/bin/env bash

init_demo_runtime() {
  local default_runtime_dir="${ROOT_DIR}/.runtime"
  if [ -n "${DEMO_RUNTIME_DIR:-}" ]; then
    RUNTIME_DIR="${DEMO_RUNTIME_DIR}"
  elif [ -e "${default_runtime_dir}" ] && [ ! -w "${default_runtime_dir}" ]; then
    RUNTIME_DIR="${ROOT_DIR}/.runtime-${USER:-$(id -un 2>/dev/null || echo user)}"
  else
    RUNTIME_DIR="${default_runtime_dir}"
  fi
  LOG_DIR="${RUNTIME_DIR}/logs"
  mkdir -p "${LOG_DIR}"
}

info() {
  if [ "${QUIET:-0}" != "1" ]; then
    printf '[%s] %s\n' "${LOG_PREFIX:-demo}" "$*"
  fi
}

warn() {
  printf '[%s] WARN: %s\n' "${LOG_PREFIX:-demo}" "$*" >&2
}

die() {
  printf '[%s] ERROR: %s\n' "${LOG_PREFIX:-demo}" "$*" >&2
  exit 1
}

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
