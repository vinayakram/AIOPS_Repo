#!/usr/bin/env bash
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${DEMO_DIR}/.." && pwd)"

APP_URL="${APP_URL:-http://localhost:8002}"
AIOPS_URL="${AIOPS_URL:-http://localhost:7000}"
RCA_URL="${RCA_URL:-http://localhost:8000}"
REMEDIATION_URL="${REMEDIATION_URL:-http://localhost:8005}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
CONTAINER_NAME="${CONTAINER_NAME:-medical-rag-pod}"
DISPLAY_CONTAINER_NAME="${DISPLAY_CONTAINER_NAME:-sample-agent-pod}"
CADVISOR_INSTANCE="${CADVISOR_INSTANCE:-127.0.0.1:8080}"
DEMO_USER="${DEMO_USER:-admin}"
DEMO_PASSWORD="${DEMO_PASSWORD:-admin}"
LOAD_SECONDS="${LOAD_SECONDS:-90}"
BREACHES_REQUIRED="${BREACHES_REQUIRED:-3}"
POLL_SECONDS="${POLL_SECONDS:-2}"
ISSUE_WAIT_SECONDS="${ISSUE_WAIT_SECONDS:-150}"
RCA_WAIT_SECONDS="${RCA_WAIT_SECONDS:-300}"
REMEDIATION_WAIT_SECONDS="${REMEDIATION_WAIT_SECONDS:-600}"
PRESSURE_WORKERS="${PRESSURE_WORKERS:-2}"
NORMAL_QUERY="${NORMAL_QUERY:-What are common symptoms of hypertension?}"

BLUE=$'\033[34m'
CYAN=$'\033[36m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
MAGENTA=$'\033[35m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RESET=$'\033[0m'

TRACE_ID=""
ISSUE_ID=""
ISSUE_TRACE_ID=""

usage() {
  cat <<EOF
Usage: $(basename "$0")

Interactive presenter script for:
  1. Existing environment pre-check + normal query telemetry
  2. Concurrent user simulation
  3. AIopsTelemetry ticket
  4. RCA
  5. Remediation plan, implementation, review, and PR

Recommended:
  LOAD_SECONDS=90 BREACHES_REQUIRED=3 ./$(basename "$0") 2>&1 | tee demo_presenter_output.log
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

out() { printf "%b\n" "$*" >&2; }
ok() { out "  ${GREEN}[OK]${RESET} $*"; }
warn() { out "  ${YELLOW}[WARN]${RESET} $*"; }
fail() { out "  ${RED}[FAIL]${RESET} $*"; }
info() { out "  ${CYAN}-->${RESET} $*"; }
dim() { out "  ${DIM}$*${RESET}"; }

mask_demo_text() {
  sed \
    -e "s/${CONTAINER_NAME}/${DISPLAY_CONTAINER_NAME}/g" \
    -e 's/medical-rag/sample-agent/g' \
    -e 's/medical_rag/sample_agent/g' \
    -e 's/MedicalRAG/SampleAgent/g' \
    -e 's/MedicalAgent/SampleAgent/g' \
    -e 's/medical/sample/g'
}

banner() {
  clear 2>/dev/null || true
  out "${BOLD}${MAGENTA}"
  out "======================================================================"
  out " SampleAgent AIOps Presenter Demo"
  out " Existing environment -> Simulation -> Ticket -> RCA -> Remediation"
  out " 既存環境 -> シミュレーション -> チケット -> RCA -> 修復"
  out "======================================================================"
  out "${RESET}"
}

step_header() {
  local n="$1"
  local en="$2"
  local ja="$3"
  out ""
  out "${BOLD}${BLUE}----------------------------------------------------------------------${RESET}"
  out "${BOLD}${BLUE}Step ${n}: ${en}${RESET}"
  out "${DIM}${ja}${RESET}"
  out "${BOLD}${BLUE}----------------------------------------------------------------------${RESET}"
}

confirm_next() {
  local prompt="${1:-Proceed to next step?}"
  local ja="${2:-次のステップへ進みますか？}"
  local answer
  out ""
  out "${BOLD}${YELLOW}${prompt}${RESET}"
  out "${DIM}${ja}${RESET}"
  read -r -p "Press Enter/yes to continue, no to stop: " answer
  case "${answer,,}" in
    ""|y|yes) return 0 ;;
    n|no) out "Stopped by presenter."; exit 0 ;;
    *) warn "Unrecognized answer; continuing." ;;
  esac
}

loading_bar() {
  local msg="$1"
  local ja="$2"
  out ""
  out "${CYAN}${msg}${RESET}"
  out "${DIM}${ja}${RESET}"
  printf "  [" >&2
  local i
  for i in $(seq 1 24); do
    printf "#" >&2
    sleep 0.035
  done
  printf "] done\n" >&2
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing command: $1"
    exit 1
  fi
}

curl_json() {
  curl -fsS "$@"
}

json_get() {
  python3 -c 'import json,sys
path=sys.argv[1].split(".")
try:
    data=json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
for part in path:
    if not part:
        continue
    if isinstance(data, list):
        data=data[int(part)]
    else:
        data=data.get(part)
    if data is None:
        break
print("" if data is None else data)' "$1"
}

json_pretty_compact() {
  python3 -c 'import json,sys
raw=sys.stdin.read()
try:
    data=json.loads(raw)
    print(json.dumps(data, indent=2)[:3000])
except Exception:
    print((raw or "{\"error\":\"empty response\"}")[:3000])'
}

extract_env_value() {
  local file="$1"
  local key="$2"
  [ -f "${file}" ] || return 0
  python3 -c 'import sys
path,key=sys.argv[1],sys.argv[2]
for raw in open(path, encoding="utf-8", errors="ignore"):
    line=raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k,v=line.split("=",1)
    if k.strip()==key:
        print(v.strip().strip(chr(34)).strip(chr(39)))
        break' "${file}" "${key}"
}

wait_url() {
  local label="$1"
  local url="$2"
  local attempts="${3:-10}"
  local i
  for i in $(seq 1 "${attempts}"); do
    if curl -fsS -L --max-time 8 "${url}" >/dev/null 2>&1; then
      ok "${label} reachable"
      return 0
    fi
    sleep 2
  done
  fail "${label} not reachable (${url})"
  return 1
}

wait_app_available() {
  local timeout="${1:-90}"
  local deadline=$((SECONDS + timeout))
  local status
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(curl -sS -o /tmp/sample_presenter_app_body.txt -w '%{http_code}' "${APP_URL}/" || true)"
    if [ "${status}" = "200" ]; then
      return 0
    fi
    sleep 5
  done
  return 1
}

login() {
  curl_json \
    -X POST "${APP_URL}/auth/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=${DEMO_USER}" \
    --data-urlencode "password=${DEMO_PASSWORD}"
}

query_agent() {
  local token="$1"
  local payload
  local response_file="/tmp/sample_presenter_query.json"
  local status
  payload="$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1], "max_articles": 5, "top_k": 3, "scenario": "presenter_normal"}))' "${NORMAL_QUERY}")"
  status="$(curl -sS \
    -X POST "${APP_URL}/api/query" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${token}" \
    --max-time 120 \
    -o "${response_file}" \
    -w '%{http_code}' \
    -d "${payload}" || true)"
  if [ "${status}" = "503" ]; then
    return 75
  fi
  if [ "${status}" -lt 200 ] || [ "${status}" -ge 300 ]; then
    cat "${response_file}" 2>/dev/null || true
    return 1
  fi
  cat "${response_file}"
}

query_agent_with_retry() {
  local token="$1"
  local attempt response rc
  for attempt in 1 2 3; do
    wait_app_available 120 || return 1
    set +e
    response="$(query_agent "${token}")"
    rc=$?
    set -e
    if [ "${rc}" -eq 0 ]; then
      printf "%s" "${response}"
      return 0
    fi
    if [ "${rc}" -eq 75 ]; then
      warn "Normal query hit pod guard. Waiting before retry ${attempt}/3."
      sleep 10
      continue
    fi
    printf "%s" "${response}"
    return "${rc}"
  done
  return 75
}

prom_query() {
  local query="$1"
  curl -fsS -G "${PROMETHEUS_URL}/api/v1/query" --data-urlencode "query=${query}" 2>/dev/null || true
}

prom_scalar() {
  python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
    result=data.get("data",{}).get("result",[])
    if not result:
        print("no data")
    else:
        vals=[]
        for row in result[:5]:
            val=row.get("value",["",""])[1]
            try:
                vals.append(float(val))
            except Exception:
                pass
        print("no data" if not vals else "{:.2f}%".format(max(vals)))
except Exception:
    print("no data")'
}

is_no_data() {
  [ -z "${1:-}" ] || [ "${1:-}" = "no data" ]
}

metric_value_from_app() {
  local metric_name="$1"
  curl -fsS "${APP_URL}/metrics" 2>/dev/null | awk -v metric="${metric_name}" '
    $1 == metric || index($1, metric "{") == 1 { print $2; found=1; exit }
    END { if (!found) exit 1 }
  ' || true
}

percent_or_empty() {
  python3 -c 'import sys
try:
    print("{:.2f}%".format(float(sys.argv[1])))
except Exception:
    print("")' "${1:-}"
}

docker_cpu_percent() {
  docker stats "${CONTAINER_NAME}" --no-stream --format '{{.CPUPerc}}' 2>/dev/null | head -n 1 || true
}

numeric_ge() {
  python3 -c 'import sys
try:
    raise SystemExit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)
except Exception:
    raise SystemExit(1)' "${1:-}" "${2:-}"
}

http_success() {
  [[ "${1:-}" =~ ^2[0-9][0-9]$ ]]
}

pod_cpu() {
  local value app_value docker_value
  value="$(prom_query "100 * sum(rate(container_cpu_usage_seconds_total{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"}[30s])) / max(container_spec_cpu_quota{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"} / container_spec_cpu_period{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"})" | prom_scalar)"
  if ! is_no_data "${value}"; then
    printf "%s (source: external Prometheus cAdvisor)\n" "${value}"
    return
  fi

  app_value="$(metric_value_from_app "sample_agent_pod_cpu_utilisation_percent")"
  app_value="$(percent_or_empty "${app_value}")"
  if [ -n "${app_value}" ]; then
    printf "%s (source: SampleAgent /metrics fallback)\n" "${app_value}"
    return
  fi

  docker_value="$(docker_cpu_percent)"
  if [ -n "${docker_value}" ]; then
    printf "%s (source: docker stats fallback)\n" "${docker_value}"
    return
  fi

  printf "0.00%% (source: fallback unavailable; metric endpoint unreachable)\n"
}

system_cpu() {
  local value
  value="$(prom_query '100 * avg(1 - rate(node_cpu_seconds_total{mode="idle"}[30s]))' | prom_scalar)"
  if ! is_no_data "${value}"; then
    printf "%s (source: external Prometheus node exporter)\n" "${value}"
    return
  fi
  value="$(awk '{print $1}' /proc/loadavg 2>/dev/null || true)"
  if [ -n "${value}" ]; then
    printf "loadavg=%s (source: VM /proc/loadavg fallback)\n" "${value}"
    return
  fi
  printf "0.00%% (source: system fallback unavailable)\n"
}

pod_cpu_guard_metric() {
  local threshold raw_value rendered last_value deadline
  threshold="$(metric_value_from_app "sample_agent_pod_cpu_threshold_percent")"
  threshold="${threshold:-90}"
  deadline=$((SECONDS + 10))

  while [ "${SECONDS}" -lt "${deadline}" ]; do
    raw_value="$(metric_value_from_app "sample_agent_pod_cpu_utilisation_percent")"
    if [ -n "${raw_value}" ]; then
      last_value="${raw_value}"
      if numeric_ge "${raw_value}" "${threshold}"; then
        rendered="$(percent_or_empty "${raw_value}")"
        printf "%s (source: SampleAgent /metrics guard metric; threshold=%s%%)\n" "${rendered}" "${threshold}"
        return
      fi
    fi
    sleep 1
  done

  if [ -n "${last_value:-}" ]; then
    rendered="$(percent_or_empty "${last_value}")"
    printf "%s (source: SampleAgent /metrics guard metric after breach; threshold=%s%%)\n" "${rendered}" "${threshold}"
    return
  fi

  pod_cpu
}

langfuse_reachable() {
  local host
  host="$(extract_env_value "${ROOT_DIR}/MedicalAgent/.env" "LANGFUSE_HOST")"
  host="${host:-https://cloud.langfuse.com}"
  curl -fsS -L --max-time 8 "${host}" >/dev/null 2>&1
}

langfuse_trace_log() {
  local trace_id="$1"
  local host pk sk data
  host="$(extract_env_value "${ROOT_DIR}/MedicalAgent/.env" "LANGFUSE_HOST")"
  pk="$(extract_env_value "${ROOT_DIR}/MedicalAgent/.env" "LANGFUSE_PUBLIC_KEY")"
  sk="$(extract_env_value "${ROOT_DIR}/MedicalAgent/.env" "LANGFUSE_SECRET_KEY")"
  host="${host:-https://cloud.langfuse.com}"
  if [ -z "${pk}" ] || [ -z "${sk}" ]; then
    printf "Langfuse trace URL: %s/trace/%s\n" "${host%/}" "${trace_id}"
    return 0
  fi
  data="$(curl -fsS -u "${pk}:${sk}" "${host%/}/api/public/traces/${trace_id}" 2>/dev/null || true)"
  if [ -z "${data}" ]; then
    printf "Langfuse trace URL: %s/trace/%s\n" "${host%/}" "${trace_id}"
    return 0
  fi
  printf "%s" "${data}" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    name=d.get("name") or "sample-agent"
    level=d.get("level") or d.get("status") or "INFO"
    latency=d.get("latency") or d.get("duration") or d.get("durationMs") or d.get("totalCost")
    message="Trace {} captured".format(name)
    if latency is not None:
        message += ", latency={}".format(latency)
    if d.get("input"):
        message += ", input={}".format(str(d.get("input"))[:140])
    print("{}: {}".format(level, message))
except Exception:
    print("INFO: Langfuse trace captured")'
}

latest_aiops_error_trace() {
  curl_json "${AIOPS_URL}/api/traces?app_name=sample-agent&status=error&limit=1" 2>/dev/null | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
    t=(d.get("traces") or [{}])[0]
    print("{} | {} | {}".format(t.get("id",""), t.get("output_preview",""), t.get("started_at","")))
except Exception:
    print("No AIops error trace available yet")'
}

start_pressure_worker() {
  docker exec -d "${CONTAINER_NAME}" python -c "import time; end=time.time()+${LOAD_SECONDS}; x=0
while time.time()<end:
    x=(x*3+1)%1000003"
}

simulate_pressure() {
  local observed=0 attempt worker status body_file="/tmp/sample_presenter_pressure_body.txt"
  for attempt in $(seq 1 "${BREACHES_REQUIRED}"); do
    loading_bar "polling concurrent user attempt ${attempt}/${BREACHES_REQUIRED}..." "concurrent user attempt ${attempt}/${BREACHES_REQUIRED} を確認中..."
    for worker in $(seq 1 "${PRESSURE_WORKERS}"); do
      start_pressure_worker
    done
    local deadline=$((SECONDS + LOAD_SECONDS + 15))
    while [ "${SECONDS}" -lt "${deadline}" ]; do
      status="$(curl -sS -o "${body_file}" -w '%{http_code}' "${APP_URL}/" || true)"
      if [ "${status}" = "503" ] && grep -qi "application is not reachable" "${body_file}" 2>/dev/null; then
        observed=$((observed + 1))
        ok "Threshold breach observed ${observed}/${BREACHES_REQUIRED}"
        break
      fi
      sleep "${POLL_SECONDS}"
    done
    info "langfuse log - $(latest_aiops_error_trace | mask_demo_text)"
    info "prometheus log - pod CPU utilisation: $(pod_cpu_guard_metric | mask_demo_text), system CPU utilisation: $(system_cpu)"
    sleep 2
  done
  [ "${observed}" -ge "${BREACHES_REQUIRED}" ]
}

wait_for_ticket() {
  local deadline=$((SECONDS + ISSUE_WAIT_SECONDS))
  local issues
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    issues="$(curl_json "${AIOPS_URL}/api/issues?app_name=sample-agent&status=OPEN&limit=20" || true)"
    if printf "%s" "${issues}" | grep -q "nfr_pod_resource_threshold_breach"; then
      printf "%s" "${issues}" | python3 -c 'import json,sys
d=json.load(sys.stdin)
items=[i for i in d.get("issues", []) if i.get("issue_type")=="nfr_pod_resource_threshold_breach"]
i=items[0]
print(i.get("id",""))
print(i.get("trace_id",""))
print(i.get("title",""))
print(i.get("severity",""))
print(i.get("description",""))
print(i.get("created_at",""))'
      return 0
    fi
    sleep 3
  done
  return 1
}

trigger_rca() {
  curl_json -X POST "${AIOPS_URL}/api/analysis/issues/${ISSUE_ID}?force=true"
}

wait_for_rca() {
  local deadline=$((SECONDS + RCA_WAIT_SECONDS))
  local data
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    data="$(curl_json "${AIOPS_URL}/api/analysis/issues/${ISSUE_ID}" || true)"
    if printf "%s" "${data}" | grep -q '"status"[[:space:]]*:[[:space:]]*"done"'; then
      printf "%s" "${data}"
      return 0
    fi
    sleep 5
  done
  return 1
}

print_rca() {
  python3 -c 'import json,sys
d=json.load(sys.stdin)
print("  likely_cause: " + (d.get("likely_cause") or "")[:700])
print("  evidence:")
for line in (d.get("evidence") or "").splitlines()[:8]:
    print("    - " + line.strip())
print("  recommended_action: " + (d.get("recommended_action") or "")[:700])
rca=d.get("rca_data") or {}
if rca:
    print("  correlation source: Invastigate RCA external pipeline")'
}

poll_rem_status() {
  local data direct pr_url
  data="$(curl_json "${AIOPS_URL}/api/remediation/issues/${ISSUE_ID}/status" 2>/dev/null || true)"
  if [ -n "${data}" ]; then
    pr_url="$(printf "%s" "${data}" | json_get pr_url)"
    if [ -z "${pr_url}" ]; then
      direct="$(curl_json "${REMEDIATION_URL}/api/issues/AIOPS-${ISSUE_ID}/status" 2>/dev/null || true)"
      if [ -n "${direct}" ] && [ -n "$(printf "%s" "${direct}" | json_get status)" ]; then
        printf "%s" "${direct}"
        return 0
      fi
    fi
    printf "%s" "${data}"
    return 0
  fi
  curl_json "${REMEDIATION_URL}/api/issues/AIOPS-${ISSUE_ID}/status" 2>/dev/null || true
}

poll_impl_summary() {
  local data
  data="$(curl_json "${AIOPS_URL}/api/remediation/issues/${ISSUE_ID}/implementation/summary" 2>/dev/null || true)"
  if [ -n "${data}" ]; then
    printf "%s" "${data}"
    return 0
  fi
  curl_json "${REMEDIATION_URL}/api/issues/AIOPS-${ISSUE_ID}/implementation/summary" 2>/dev/null || true
}

wait_rem_status() {
  local label="$1"
  local terminal_regex="$2"
  local deadline=$((SECONDS + REMEDIATION_WAIT_SECONDS))
  local data status phase
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    data="$(poll_rem_status)"
    status="$(printf "%s" "${data}" | json_get status)"
    phase="$(printf "%s" "${data}" | json_get job_phase)"
    loading_bar "polling ${label}: ${status:-unknown} ${phase:-}" "${label} をポーリング中: ${status:-unknown} ${phase:-}"
    if printf "%s" "${status}" | grep -Eq "${terminal_regex}"; then
      printf "%s" "${data}"
      return 0
    fi
    if [ "${status}" = "FAILED" ]; then
      printf "%s" "${data}"
      return 2
    fi
    sleep 5
  done
  return 1
}

wait_implementation_ready() {
  local deadline=$((SECONDS + REMEDIATION_WAIT_SECONDS))
  local data status phase
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    data="$(poll_impl_summary)"
    status="$(printf "%s" "${data}" | json_get status)"
    phase="$(printf "%s" "${data}" | json_get job_phase)"
    loading_bar "polling implementation: ${status:-unknown} ${phase:-}" "Implementation をポーリング中: ${status:-unknown} ${phase:-}"
    if printf "%s" "${status}" | grep -Eq 'REVIEW_PENDING|IMPLEMENTATION_READY|REVIEW_APPROVED|PR_CREATED'; then
      printf "%s" "${data}"
      return 0
    fi
    if [ "${status}" = "FAILED" ]; then
      printf "%s" "${data}"
      return 2
    fi
    sleep 5
  done
  return 1
}

post_remediation_with_fallback() {
  local telemetry_path="$1"
  local direct_path="$2"
  local body="${3:-}"
  local tmp status direct_tmp direct_status
  tmp="$(mktemp)"
  direct_tmp="$(mktemp)"

  if [ -n "${body}" ]; then
    status="$(curl -sS -X POST "${AIOPS_URL}${telemetry_path}" -H "Content-Type: application/json" -d "${body}" -o "${tmp}" -w '%{http_code}' || true)"
  else
    status="$(curl -sS -X POST "${AIOPS_URL}${telemetry_path}" -o "${tmp}" -w '%{http_code}' || true)"
  fi

  if http_success "${status}"; then
    cat "${tmp}"
    rm -f "${tmp}" "${direct_tmp}"
    return 0
  fi

  warn "Telemetry remediation proxy returned HTTP ${status}; falling back to remediation service directly."
  if [ -n "${body}" ]; then
    direct_status="$(curl -sS -X POST "${REMEDIATION_URL}${direct_path}" -H "Content-Type: application/json" -d "${body}" -o "${direct_tmp}" -w '%{http_code}' || true)"
  else
    direct_status="$(curl -sS -X POST "${REMEDIATION_URL}${direct_path}" -o "${direct_tmp}" -w '%{http_code}' || true)"
  fi

  if http_success "${direct_status}"; then
    cat "${direct_tmp}"
    rm -f "${tmp}" "${direct_tmp}"
    return 0
  fi

  python3 -c 'import json,sys
print(json.dumps({
  "error": "remediation call failed",
  "telemetry_status": sys.argv[1],
  "telemetry_body": open(sys.argv[2], errors="ignore").read()[:500],
  "direct_status": sys.argv[3],
  "direct_body": open(sys.argv[4], errors="ignore").read()[:500],
}, indent=2))' "${status}" "${tmp}" "${direct_status}" "${direct_tmp}"
  rm -f "${tmp}" "${direct_tmp}"
  return 1
}

get_remediation_with_fallback() {
  local telemetry_path="$1"
  local direct_path="$2"
  local tmp status direct_tmp direct_status
  tmp="$(mktemp)"
  direct_tmp="$(mktemp)"

  status="$(curl -sS "${AIOPS_URL}${telemetry_path}" -o "${tmp}" -w '%{http_code}' || true)"
  if http_success "${status}"; then
    cat "${tmp}"
    rm -f "${tmp}" "${direct_tmp}"
    return 0
  fi

  warn "Telemetry remediation proxy returned HTTP ${status}; falling back to remediation service directly."
  direct_status="$(curl -sS "${REMEDIATION_URL}${direct_path}" -o "${direct_tmp}" -w '%{http_code}' || true)"
  if http_success "${direct_status}"; then
    cat "${direct_tmp}"
    rm -f "${tmp}" "${direct_tmp}"
    return 0
  fi

  python3 -c 'import json,sys
print(json.dumps({
  "error": "remediation lookup failed",
  "telemetry_status": sys.argv[1],
  "telemetry_body": open(sys.argv[2], errors="ignore").read()[:500],
  "direct_status": sys.argv[3],
  "direct_body": open(sys.argv[4], errors="ignore").read()[:500],
}, indent=2))' "${status}" "${tmp}" "${direct_status}" "${direct_tmp}"
  rm -f "${tmp}" "${direct_tmp}"
  return 1
}

wait_project_resolution_approved() {
  local deadline=$((SECONDS + REMEDIATION_WAIT_SECONDS))
  local resolution_json resolution_status job_phase
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    resolution_json="$(get_remediation_with_fallback \
      "/api/remediation/issues/${ISSUE_ID}/resolution" \
      "/api/issues/AIOPS-${ISSUE_ID}/resolution" || true)"
    resolution_status="$(printf "%s" "${resolution_json}" | json_get resolution_status)"
    job_phase="$(printf "%s" "${resolution_json}" | json_get job_phase)"
    loading_bar "polling project approval: ${resolution_status:-unknown} ${job_phase:-}" "Project approval をポーリング中: ${resolution_status:-unknown} ${job_phase:-}"
    if [ "${resolution_status}" = "approved" ]; then
      printf "%s" "${resolution_json}"
      return 0
    fi
    if [ "${resolution_status}" = "failed" ] || [ "${resolution_status}" = "rejected" ]; then
      printf "%s" "${resolution_json}"
      return 2
    fi
    sleep 5
  done
  return 1
}

review_plan_until_approved() {
  local plan_json="$1"
  local decision comments
  while true; do
    printf "%s" "${plan_json}" | print_plan_excerpt | mask_demo_text
    out ""
    out "${BOLD}${YELLOW}Review remediation plan: approve, revise, or stop?${RESET}"
    out "${DIM}Remediation plan を確認してください: approve / revise / stop${RESET}"
    read -r -p "Type approve, revise, or stop: " decision
    case "${decision,,}" in
      ""|approve|a|yes|y)
        return 0
        ;;
      revise|r)
        out "${BOLD}${YELLOW}Enter revision comments for the remediation plan.${RESET}"
        out "${DIM}Plan revision comments を入力してください。${RESET}"
        read -r -p "Comments: " comments
        if [ -z "${comments}" ]; then
          warn "Revision comments were empty; please choose again."
          continue
        fi
        local revise_body
        revise_body="$(python3 -c 'import json,sys; print(json.dumps({"review_comments": sys.argv[1]}))' "${comments}")"
        post_remediation_with_fallback \
          "/api/remediation/issues/${ISSUE_ID}/plan/revise" \
          "/api/issues/AIOPS-${ISSUE_ID}/plan/revise" \
          "${revise_body}" \
          | json_pretty_compact | mask_demo_text | sed 's/^/    /'
        while true; do
          sleep 5
          plan_json="$(get_remediation_with_fallback \
            "/api/remediation/issues/${ISSUE_ID}/plan" \
            "/api/issues/AIOPS-${ISSUE_ID}/plan" || true)"
          printf "%s" "${plan_json}" | grep -q '"active_job"[[:space:]]*:[[:space:]]*null' && break
          loading_bar "polling revised plan..." "修正版 plan をポーリング中..."
        done
        ;;
      stop|no|n)
        out "Stopped by presenter during plan review."
        exit 0
        ;;
      *)
        warn "Please type approve, revise, or stop."
        ;;
    esac
  done
}

print_plan_excerpt() {
  python3 -c 'import json,sys
d=json.load(sys.stdin)
text=d.get("plan_text") or ""
print("  plan_status: " + str(d.get("status","")))
print("  plan_excerpt:")
for line in text.splitlines()[:18]:
    print("    " + line[:180])'
}

print_impl_excerpt() {
  python3 -c 'import json,sys
d=json.load(sys.stdin)
print("  implementation_status: " + str(d.get("status","")))
cs=d.get("change_summary") or {}
if isinstance(cs, dict) and cs:
    print("  change_summary:")
    for k,v in list(cs.items())[:6]:
        print("    {}: {}".format(k, str(v)[:180]))
diff=d.get("git_diff_text") or ""
if diff:
    print("  diff_excerpt:")
    for line in diff.splitlines()[:30]:
        print("    " + line[:180])'
}

main() {
  banner
  need_cmd curl
  need_cmd python3
  need_cmd docker

  step_header 1 "Pre-check existing environment and show normal telemetry" "既存環境の事前確認と通常 telemetry の表示"
  loading_bar "polling environment..." "環境をポーリング中..."
  local host_ip docker_line token query_response lf_log
  host_ip="$(hostname -I 2>/dev/null | xargs || true)"
  docker_line="$(docker ps --filter "name=^/${CONTAINER_NAME}$" --format "container=${DISPLAY_CONTAINER_NAME} image={{.Image}} status={{.Status}} ports={{.Ports}}" | head -n 1)"
  info "environment/docker - VM=$(hostname) IP=${host_ip:-n/a}; ${docker_line:-container not found}"
  wait_url "SampleAgent" "${APP_URL}/api/health" 20
  wait_url "AIopsTelemetry" "${AIOPS_URL}/health" 20
  wait_url "Invastigate RCA" "${RCA_URL}/health" 20
  if langfuse_reachable; then ok "Langfuse reachable"; else fail "Langfuse not reachable"; fi
  wait_url "Prometheus" "${PROMETHEUS_URL}/-/ready" 10 || wait_url "Prometheus" "${PROMETHEUS_URL}/api/v1/targets" 3
  wait_url "Grafana dashboard" "${GRAFANA_URL}" 8 || warn "Grafana dashboard may require login, but URL was checked."

  loading_bar "polling normal query..." "通常クエリをポーリング中..."
  token="$(login | json_get access_token)"
  [ -n "${token}" ] || { fail "Could not login to SampleAgent"; exit 1; }
  query_response="$(query_agent_with_retry "${token}")"
  TRACE_ID="$(printf "%s" "${query_response}" | json_get trace_id)"
  lf_log="$(langfuse_trace_log "${TRACE_ID}")"
  info "langfuse log - ${lf_log}"
  info "prometheus log - pod CPU utilisation: $(pod_cpu | mask_demo_text), system CPU utilisation: $(system_cpu)"
  confirm_next "Proceed to concurrent user simulation?" "Concurrent user simulation に進みますか？"

  step_header 2 "Simulate concurrent user load and CPU increase" "同時ユーザー負荷による CPU 上昇をシミュレーション"
  if simulate_pressure; then
    ok "Concurrent user simulation completed with ${BREACHES_REQUIRED} threshold breaches."
  else
    warn "Concurrent user simulation completed, but fewer than ${BREACHES_REQUIRED} breaches were observed."
  fi
  confirm_next "Proceed to ticket validation?" "ticket 確認に進みますか？"

  step_header 3 "Raise and show AIopsTelemetry ticket after three breaches" "3 回のしきい値超過後に AIopsTelemetry ticket を表示"
  loading_bar "polling telemetry..." "Telemetry をポーリング中..."
  local ticket_lines
  ticket_lines="$(wait_for_ticket)" || { fail "No NFR-33 ticket found."; exit 1; }
  ISSUE_ID="$(printf "%s\n" "${ticket_lines}" | sed -n '1p')"
  ISSUE_TRACE_ID="$(printf "%s\n" "${ticket_lines}" | sed -n '2p')"
  ok "Ticket raised in AIopsTelemetry"
  info "ticket_id: ${ISSUE_ID}"
  info "trace_id: ${ISSUE_TRACE_ID}"
  info "title: $(printf "%s\n" "${ticket_lines}" | sed -n '3p' | mask_demo_text)"
  info "severity: $(printf "%s\n" "${ticket_lines}" | sed -n '4p')"
  info "error: $(printf "%s\n" "${ticket_lines}" | sed -n '5p' | mask_demo_text)"
  info "created_at: $(printf "%s\n" "${ticket_lines}" | sed -n '6p')"
  confirm_next "Proceed to RCA?" "RCA に進みますか？"

  step_header 4 "Run RCA for the error" "エラーに対して RCA を実行"
  loading_bar "polling RCA start..." "RCA 開始をポーリング中..."
  trigger_rca | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  loading_bar "polling RCA..." "RCA をポーリング中..."
  local rca_json
  rca_json="$(wait_for_rca)" || { fail "RCA did not complete in time."; exit 1; }
  printf "%s" "${rca_json}" | print_rca | mask_demo_text
  confirm_next "Proceed to remediation workflow?" "Remediation workflow に進みますか？"

  step_header 5 "Remediation: plan, implementation, review, and PR" "Remediation: plan 作成、実装、review、PR"
  loading_bar "polling remediation start..." "Remediation 開始をポーリング中..."
  curl_json -X POST "${AIOPS_URL}/api/remediation/issues/${ISSUE_ID}/start" \
    -H "Content-Type: application/json" \
    -d '{"requested_by":"demo-presenter","acceptance_criteria":["Raise the pod CPU threshold configuration for the demo workload.","Keep existing external Prometheus/cAdvisor compatibility intact.","Prepare a remediation branch and pull request for review."]}' \
    | json_pretty_compact | mask_demo_text | sed 's/^/    /'

  loading_bar "polling project resolution..." "Project resolution をポーリング中..."
  curl_json "${AIOPS_URL}/api/remediation/issues/${ISSUE_ID}/resolution" | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  confirm_next "Approve project resolution and generate remediation plan?" "Project resolution を承認して plan を作成しますか？"

  post_remediation_with_fallback \
    "/api/remediation/issues/${ISSUE_ID}/project/approve" \
    "/api/issues/AIOPS-${ISSUE_ID}/project/approve" \
    '{}' \
    | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  local resolution_ready_json
  resolution_ready_json="$(wait_project_resolution_approved)" || {
    fail "Project resolution was not approved in time; plan generation cannot start safely."
    printf "%s" "${resolution_ready_json:-}" | json_pretty_compact | mask_demo_text | sed 's/^/    /' || true
    exit 1
  }
  printf "%s" "${resolution_ready_json}" | json_pretty_compact | mask_demo_text | sed 's/^/    /'

  loading_bar "polling plan generation..." "Plan generation をポーリング中..."
  post_remediation_with_fallback \
    "/api/remediation/issues/${ISSUE_ID}/plan/start" \
    "/api/issues/AIOPS-${ISSUE_ID}/plan" \
    "" \
    | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  local plan_json
  while true; do
    sleep 5
    plan_json="$(get_remediation_with_fallback \
      "/api/remediation/issues/${ISSUE_ID}/plan" \
      "/api/issues/AIOPS-${ISSUE_ID}/plan" || true)"
    printf "%s" "${plan_json}" | grep -q '"active_job"[[:space:]]*:[[:space:]]*null' && break
    loading_bar "polling plan..." "Plan をポーリング中..."
  done
  review_plan_until_approved "${plan_json}"
  confirm_next "Approve this remediation plan and start implementation?" "この remediation plan を承認して実装を開始しますか？"

  post_remediation_with_fallback \
    "/api/remediation/issues/${ISSUE_ID}/plan/approve" \
    "/api/issues/AIOPS-${ISSUE_ID}/plan/approve" \
    "" \
    | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  loading_bar "polling implementation..." "Implementation をポーリング中..."
  local impl_json
  impl_json="$(wait_implementation_ready)" || {
    warn "Implementation polling ended without expected terminal status; showing current summary."
    impl_json="$(poll_impl_summary)"
  }
  printf "%s" "${impl_json}" | print_impl_excerpt | mask_demo_text
  confirm_next "Approve implementation review and create PR?" "Implementation review を承認して PR を作成しますか？"

  post_remediation_with_fallback \
    "/api/remediation/issues/${ISSUE_ID}/review/approve" \
    "/api/issues/AIOPS-${ISSUE_ID}/review/approve" \
    '{"review_notes":"Approved during guided demo."}' \
    | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  post_remediation_with_fallback \
    "/api/remediation/issues/${ISSUE_ID}/pr" \
    "/api/issues/AIOPS-${ISSUE_ID}/pr" \
    "" \
    | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  loading_bar "polling PR creation..." "PR 作成をポーリング中..."
  local pr_json
  pr_json="$(wait_rem_status "PR creation" 'PR_CREATED|REVIEW_APPROVED')" || true
  printf "%s" "${pr_json}" | json_pretty_compact | mask_demo_text | sed 's/^/    /'
  out ""
  local pr_status pr_url pr_number
  pr_status="$(printf "%s" "${pr_json}" | json_get status)"
  pr_url="$(printf "%s" "${pr_json}" | json_get pr_url)"
  pr_number="$(printf "%s" "${pr_json}" | json_get pr_number)"
  if [ "${pr_status}" = "PR_CREATED" ] && [ -n "${pr_number}" ]; then
    ok "Remediation flow completed. Plan generated, implementation prepared, review approved, and GitHub PR #${pr_number} created."
    info "PR URL: ${pr_url}"
    out "${DIM}Remediation flow completed successfully. / Remediation flow が正常に完了しました。${RESET}"
  elif [ -n "${pr_url}" ]; then
    warn "Branch handoff completed, but GitHub did not create a pull request automatically."
    info "Compare URL: ${pr_url}"
    out "${DIM}Open the compare URL to create the PR, or rerun after confirming the implementation produced a real diff. / compare URL から PR を作成してください。${RESET}"
  else
    warn "Review was approved, but no PR URL was returned. Check remediation logs before presenting this as complete."
  fi
}

main "$@"
