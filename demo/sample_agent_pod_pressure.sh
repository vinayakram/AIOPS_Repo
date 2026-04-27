#!/usr/bin/env bash
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${DEMO_DIR}/.." && pwd)"

APP_URL="${APP_URL:-http://localhost:8002}"
AIOPS_URL="${AIOPS_URL:-http://localhost:7000}"
RCA_URL="${RCA_URL:-http://localhost:8000}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:3000}"
CONTAINER_NAME="${CONTAINER_NAME:-medical-rag-pod}"
DISPLAY_CONTAINER_NAME="${DISPLAY_CONTAINER_NAME:-sample-agent-pod}"
CADVISOR_INSTANCE="${CADVISOR_INSTANCE:-127.0.0.1:8080}"

DEMO_USER="${DEMO_USER:-admin}"
DEMO_PASSWORD="${DEMO_PASSWORD:-admin}"
START_STACK="${START_STACK:-1}"
REBUILD_MEDICAL="${REBUILD_MEDICAL:-0}"
DEMO_LANG="${LANG_DEMO:-both}"
PAUSE_SECONDS="${PAUSE_SECONDS:-2}"
LOAD_SECONDS="${LOAD_SECONDS:-90}"
BREACHES_REQUIRED="${BREACHES_REQUIRED:-3}"
POLL_SECONDS="${POLL_SECONDS:-2}"
ISSUE_WAIT_SECONDS="${ISSUE_WAIT_SECONDS:-120}"
RCA_WAIT_SECONDS="${RCA_WAIT_SECONDS:-240}"

NORMAL_QUERIES=(
  "What are common symptoms of hypertension?"
  "Summarize treatment options for type 2 diabetes."
  "What lifestyle advice helps high cholesterol?"
)

BLUE=$'\033[34m'
CYAN=$'\033[36m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
MAGENTA=$'\033[35m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
RESET=$'\033[0m'

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Guided SampleAgent pod-pressure demo:
  VM/container proof -> normal query traces -> Prometheus metrics -> CPU pressure
  -> AIopsTelemetry ticket -> Invastigate RCA correlation.

Options:
  --no-start        Do not call demo/start.sh first
  --start          Call demo/start.sh first (default)
  --fast           Reduce pauses between narrated steps
  --lang en        English only
  --lang ja        Japanese only
  --lang both      English + Japanese (default)
  -h, --help       Show this help

Useful environment overrides:
  APP_URL=${APP_URL}
  AIOPS_URL=${AIOPS_URL}
  RCA_URL=${RCA_URL}
  PROMETHEUS_URL=${PROMETHEUS_URL}
  CONTAINER_NAME=<internal cAdvisor-compatible container name>
  LOAD_SECONDS=${LOAD_SECONDS}
  BREACHES_REQUIRED=${BREACHES_REQUIRED}

Recommended for Grafana polling every 30s:
  LOAD_SECONDS=90 BREACHES_REQUIRED=3 ./$(basename "$0")
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-start) START_STACK=0 ;;
    --start) START_STACK=1 ;;
    --fast) PAUSE_SECONDS=0.4 ;;
    --lang) shift; DEMO_LANG="${1:-both}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

say() {
  local en="$1"
  local ja="$2"
  case "${DEMO_LANG}" in
    en) printf "%b\n" "  ${en}" ;;
    ja) printf "%b\n" "  ${ja}" ;;
    both) printf "%b\n" "  ${en}" "  ${DIM}${ja}${RESET}" ;;
    *) printf "%b\n" "  ${en}" ;;
  esac
}

pause() {
  awk "BEGIN { exit !(${PAUSE_SECONDS} > 0) }" 2>/dev/null && sleep "${PAUSE_SECONDS}" || true
}

banner() {
  clear 2>/dev/null || true
  printf "%b\n" "${BOLD}${MAGENTA}"
  printf "%s\n" "======================================================================"
  printf "%s\n" " SampleAgent AIOps Demo"
  printf "%s\n" " VM-hosted Docker pod -> Langfuse/Prometheus -> Ticket -> RCA"
  printf "%s\n" "======================================================================"
  printf "%b\n" "${RESET}"
  say \
    "This single script walks the audience through the exact demo story." \
    "この単一スクリプトで、デモの流れを順番に見せます。"
  printf "\n"
}

step() {
  local n="$1"
  local en="$2"
  local ja="$3"
  printf "\n%b\n" "${BOLD}${BLUE}----------------------------------------------------------------------${RESET}"
  printf "%b\n" "${BOLD}${BLUE}Step ${n}: ${en}${RESET}"
  printf "%b\n" "${DIM}${ja}${RESET}"
  printf "%b\n" "${BOLD}${BLUE}----------------------------------------------------------------------${RESET}"
}

ok() { printf "%b\n" "  ${GREEN}[OK]${RESET} $*" >&2; }
warn() { printf "%b\n" "  ${YELLOW}[WARN]${RESET} $*" >&2; }
fail() { printf "%b\n" "  ${RED}[FAIL]${RESET} $*" >&2; }
info() { printf "%b\n" "  ${CYAN}->${RESET} $*" >&2; }
dim() { printf "%b\n" "  ${DIM}$*${RESET}" >&2; }

mask_demo_text() {
  sed \
    -e "s/${CONTAINER_NAME}/${DISPLAY_CONTAINER_NAME}/g" \
    -e 's/medical-rag/sample-agent/g' \
    -e 's/medical_rag/sample_agent/g' \
    -e 's/MedicalRAG/SampleAgent/g' \
    -e 's/MedicalAgent/SampleAgent/g' \
    -e 's/medical/sample/g'
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
data=json.load(sys.stdin)
for part in path:
    if part == "":
        continue
    if isinstance(data, list):
        data=data[int(part)]
    else:
        data=data.get(part)
    if data is None:
        break
print("" if data is None else data)' "$1"
}

json_pretty() {
  python3 -m json.tool 2>/dev/null || cat
}

wait_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-30}"
  local i
  for i in $(seq 1 "${attempts}"); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      ok "${name} reachable: ${url}"
      return 0
    fi
    sleep 2
  done
  fail "${name} not reachable: ${url}"
  return 1
}

prom_query() {
  local query="$1"
  curl -fsS -G "${PROMETHEUS_URL}/api/v1/query" --data-urlencode "query=${query}" 2>/dev/null || true
}

prom_value() {
  python3 -c 'import json,sys
try:
    data=json.load(sys.stdin)
    result=data.get("data",{}).get("result",[])
    if not result:
        print("no data")
    else:
        for row in result[:5]:
            metric=row.get("metric",{})
            value=row.get("value",["",""])[1]
            labels=",".join(f"{k}={v}" for k,v in sorted(metric.items()) if k in {"job","instance","name","app","status"})
            print(f"{labels or metric} => {value}")
except Exception as exc:
    print(f"parse error: {exc}")'
}

login() {
  curl_json \
    -X POST "${APP_URL}/auth/login" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "username=${DEMO_USER}" \
    --data-urlencode "password=${DEMO_PASSWORD}"
}

query_sample_agent() {
  local token="$1"
  local query="$2"
  local payload
  local response_file="/tmp/sample_agent_demo_query_response.json"
  local status
  payload="$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1], "max_articles": 5, "top_k": 3, "scenario": "normal_demo"}))' "${query}")"
  status="$(curl -sS \
    -X POST "${APP_URL}/api/query" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${token}" \
    -H "X-AIOPS-DEMO: guided_pod_pressure" \
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

wait_for_app_available() {
  local timeout="${1:-90}"
  local deadline=$((SECONDS + timeout))
  local status
  local body_file="/tmp/sample_agent_demo_available_body.txt"
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(curl -sS -o "${body_file}" -w '%{http_code}' "${APP_URL}/" || true)"
    if [ "${status}" = "200" ]; then
      ok "SampleAgent is accepting normal traffic."
      return 0
    fi
    if [ "${status}" = "503" ]; then
      warn "SampleAgent is still in pod-guard mode; waiting for CPU pressure to settle."
    else
      warn "SampleAgent returned HTTP ${status}; waiting before query."
    fi
    sleep 5
  done
  return 1
}

run_query_with_recovery() {
  local token="$1"
  local query="$2"
  local attempts="${3:-3}"
  local attempt response rc
  for attempt in $(seq 1 "${attempts}"); do
    wait_for_app_available 90 >&2 || return 1
    set +e
    response="$(query_sample_agent "${token}" "${query}")"
    rc=$?
    set -e
    if [ "${rc}" -eq 0 ]; then
      printf "%s" "${response}"
      return 0
    fi
    if [ "${rc}" -eq 75 ]; then
      warn "Query hit HTTP 503 during attempt ${attempt}/${attempts}; waiting and retrying."
      sleep 10
      continue
    fi
    printf "%s" "${response}"
    return "${rc}"
  done
  return 75
}

print_trace_summary() {
  local response="$1"
  local trace_id
  local langfuse_url
  local total_fetched
  trace_id="$(printf "%s" "${response}" | json_get trace_id)"
  langfuse_url="$(printf "%s" "${response}" | json_get langfuse_url)"
  total_fetched="$(printf "%s" "${response}" | json_get total_fetched)"
  LAST_TRACE_ID="${trace_id}"
  ok "Sample query completed. trace_id=${trace_id}"
  [ -n "${langfuse_url}" ] && info "Langfuse trace URL: ${langfuse_url}" || warn "Langfuse URL not returned; local trace log will still be shown."
  info "Articles fetched: ${total_fetched:-n/a}"
}

show_local_trace_log() {
  local token="$1"
  local trace_id="$2"
  local traces
  traces="$(curl_json -H "Authorization: Bearer ${token}" "${APP_URL}/api/traces?limit=10" || true)"
  if [ -z "${traces}" ]; then
    warn "Could not read SampleAgent local trace dashboard API."
    return
  fi
  printf "%s" "${traces}" | python3 -c 'import json,sys
trace_id=sys.argv[1]
data=json.load(sys.stdin)
for t in data.get("traces", []):
    if t.get("trace_id") == trace_id:
        print("  Local trace log / Langfuse-adjacent evidence:")
        print("    query: {}".format(t.get("query")))
        print("    duration_ms: {}".format(t.get("total_duration_ms")))
        print("    articles_fetched: {}".format(t.get("articles_fetched")))
        print("    langfuse_url: {}".format(t.get("langfuse_url")))
        print("    steps:")
        for s in t.get("steps", []):
            print("      - {}: {} ms".format(s.get("name"), s.get("duration_ms")))
        break
else:
    print("  Trace not found yet in local dashboard API; it may still be flushing.")' "${trace_id}"
}

show_prometheus_evidence() {
  local title="$1"
  printf "\n%b\n" "${BOLD}${CYAN}${title}${RESET}"
  local queries=(
    "up{job=\"cadvisor\",instance=\"${CADVISOR_INSTANCE}\"}"
    "sum(rate(container_cpu_usage_seconds_total{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"}[30s])) * 100"
    "100 * sum(rate(container_cpu_usage_seconds_total{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"}[30s])) / max(container_spec_cpu_quota{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"} / container_spec_cpu_period{instance=\"${CADVISOR_INSTANCE}\",name=\"${CONTAINER_NAME}\"})"
    "increase(sample_agent_pod_threshold_breaches_total[5m])"
    "sum(rate(sample_agent_query_requests_total{app=\"sample-agent\"}[5m]))"
  )
  local labels=(
    "cAdvisor target health"
    "Container CPU as host-core percent"
    "Container CPU normalized to Docker quota"
    "SampleAgent pod threshold breach count"
    "SampleAgent query throughput"
  )
  local i
  for i in "${!queries[@]}"; do
    info "${labels[$i]}"
    printf "%s\n" "PromQL: ${queries[$i]}" | mask_demo_text | sed "s/^/    ${DIM}/;s/$/${RESET}/"
    prom_query "${queries[$i]}" | prom_value | mask_demo_text | sed 's/^/    /'
  done
}

start_cpu_load() {
  info "Starting bounded CPU loop inside ${DISPLAY_CONTAINER_NAME} for ${LOAD_SECONDS}s"
  docker exec -d "${CONTAINER_NAME}" python -c "import time; end=time.time()+${LOAD_SECONDS}; x=0
while time.time()<end:
    x=(x*3+1)%1000003"
}

wait_for_breach_response() {
  local deadline=$((SECONDS + LOAD_SECONDS + 15))
  local status
  local body_file="/tmp/sample_agent_demo_pressure_body.txt"
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(curl -sS -o "${body_file}" -w '%{http_code}' "${APP_URL}/" || true)"
    local body
    body="$(tr '\n' ' ' <"${body_file}" 2>/dev/null || true)"
    if [ "${status}" = "503" ] && printf "%s" "${body}" | grep -qi "application is not reachable"; then
      ok "Observed guarded response: HTTP 503 ${body}"
      return 0
    fi
    sleep "${POLL_SECONDS}"
  done
  warn "No 503 observed during this pressure window."
  return 1
}

wait_for_ticket() {
  local deadline=$((SECONDS + ISSUE_WAIT_SECONDS))
  local issues
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    issues="$(curl_json "${AIOPS_URL}/api/issues?app_name=sample-agent&status=OPEN&limit=20" || true)"
    if printf "%s" "${issues}" | grep -q "nfr_pod_resource_threshold_breach"; then
      printf "%s" "${issues}" | python3 -c 'import json,sys
data=json.load(sys.stdin)
matches=[i for i in data.get("issues", []) if i.get("issue_type")=="nfr_pod_resource_threshold_breach"]
if not matches:
    sys.exit(1)
i=matches[0]
print(i.get("id",""))
print(i.get("trace_id",""))
print(i.get("title",""))
print(i.get("severity",""))
print(i.get("created_at",""))'
      return 0
    fi
    sleep 3
  done
  return 1
}

trigger_rca() {
  local issue_id="$1"
  curl_json -X POST "${AIOPS_URL}/api/analysis/issues/${issue_id}?force=true"
}

wait_for_rca() {
  local issue_id="$1"
  local deadline=$((SECONDS + RCA_WAIT_SECONDS))
  local analysis
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    analysis="$(curl_json "${AIOPS_URL}/api/analysis/issues/${issue_id}" || true)"
    if printf "%s" "${analysis}" | grep -q '"status"[[:space:]]*:[[:space:]]*"done"'; then
      printf "%s" "${analysis}"
      return 0
    fi
    if printf "%s" "${analysis}" | grep -q '"status"[[:space:]]*:[[:space:]]*"failed"'; then
      printf "%s" "${analysis}"
      return 2
    fi
    sleep 5
  done
  return 1
}

print_rca_summary() {
  python3 -c 'import json,sys
data=json.load(sys.stdin)
print("  RCA status:", data.get("status"))
print("  Likely cause:", (data.get("likely_cause") or "")[:500])
print("  Evidence:")
for line in (data.get("evidence") or "").splitlines()[:8]:
    print("    " + line)
print("  Recommended action:", (data.get("recommended_action") or "")[:500])
rca=data.get("rca_data") or {}
if rca:
    steps=rca.get("pipeline_steps") or []
    if steps:
        print("  RCA pipeline steps:")
        for step in steps:
            name=step.get("agent") or step.get("name") or step.get("step") or "agent"
            status=step.get("status") or step.get("state") or "completed"
            print(f"    - {name}: {status}")
    fetched=rca.get("fetched_logs") or {}
    if fetched:
        print("  Evidence fetched by RCA:")
        for agent, sources in fetched.items():
            if isinstance(sources, dict):
                counts=", ".join(f"{src}={len(rows) if isinstance(rows, list) else 1}" for src, rows in sources.items())
                print(f"    - {agent}: {counts}")'
}

main() {
  banner
  need_cmd curl
  need_cmd python3
  need_cmd docker

  step 1 "Show SampleAgent hosted in VM and Docker" "VM 上で SampleAgent が Docker container として動いていることを示します。"
  if [ "${START_STACK}" = "1" ]; then
    say "Starting or refreshing the demo stack first." "最初にデモスタックを起動・更新します。"
    REBUILD_MEDICAL="${REBUILD_MEDICAL}" "${DEMO_DIR}/start.sh"
  else
    say "Skipping stack startup because --no-start was provided." "--no-start のためスタック起動はスキップします。"
  fi
  pause

  info "VM hostname: $(hostname)"
  info "VM IP addresses: $(hostname -I 2>/dev/null | xargs || true)"
  if docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
    ok "Docker container exists: ${DISPLAY_CONTAINER_NAME}"
    docker ps --filter "name=^/${CONTAINER_NAME}$" --format '  container={{.Names}} image={{.Image}} status={{.Status}} ports={{.Ports}}' | mask_demo_text
    docker inspect "${CONTAINER_NAME}" --format '  docker limits: cpus={{.HostConfig.NanoCpus}} memory={{.HostConfig.Memory}} bytes'
  else
    fail "Container ${DISPLAY_CONTAINER_NAME} not found."
    exit 1
  fi

  step 2 "Health-check app, telemetry, RCA, and Prometheus" "アプリ、Telemetry、RCA、Prometheus の疎通を確認します。"
  wait_url "SampleAgent" "${APP_URL}/api/health" 45
  wait_url "AIopsTelemetry" "${AIOPS_URL}/health" 30
  wait_url "Invastigate RCA" "${RCA_URL}/health" 30
  wait_url "External Prometheus" "${PROMETHEUS_URL}/-/ready" 15 || wait_url "External Prometheus API" "${PROMETHEUS_URL}/api/v1/targets" 5
  info "Grafana dashboard: ${GRAFANA_URL}"
  pause

  step 3 "Run normal sample queries and show Langfuse-style trace logs" "通常クエリを実行し、Langfuse/trace ログを見せます。"
  local login_response token trace_id last_response
  login_response="$(login)"
  token="$(printf "%s" "${login_response}" | json_get access_token)"
  if [ -z "${token}" ]; then
    fail "Could not login to SampleAgent as ${DEMO_USER}."
    exit 1
  fi
  ok "Logged in to SampleAgent as ${DEMO_USER}"

  for q in "${NORMAL_QUERIES[@]}"; do
    info "Query: ${q}"
    last_response="$(run_query_with_recovery "${token}" "${q}")"
    print_trace_summary "${last_response}"
    trace_id="${LAST_TRACE_ID}"
    show_local_trace_log "${token}" "${trace_id}"
    pause
  done

  step 4 "Show external Prometheus metrics before pressure" "負荷をかける前の外部 Prometheus metrics を表示します。"
  show_prometheus_evidence "Prometheus snapshot before CPU pressure"
  pause

  step 5 "Increase pressure on the SampleAgent pod" "SampleAgent pod に CPU pressure をかけ、可用性ガードを発火させます。"
  say \
    "Business story: a burst of expensive research requests causes CPU saturation, so the pod guard returns a clear availability failure before the service becomes unreliable." \
    "ビジネスストーリー: 高コストなリサーチ要求が急増し CPU が飽和。サービスが不安定になる前に pod guard が明確な可用性エラーを返します。"
  local observed=0
  local attempt
  for attempt in $(seq 1 "${BREACHES_REQUIRED}"); do
    printf "\n%b\n" "${BOLD}Pressure attempt ${attempt}/${BREACHES_REQUIRED}${RESET}"
    start_cpu_load
    if wait_for_breach_response; then
      observed=$((observed + 1))
    fi
    show_prometheus_evidence "Prometheus snapshot during/after pressure attempt ${attempt}"
    sleep 2
  done
  ok "Observed ${observed}/${BREACHES_REQUIRED} guarded breach responses."
  if [ "${observed}" -lt "${BREACHES_REQUIRED}" ]; then
    warn "The guard did not breach every time. For demos, increase LOAD_SECONDS or lower the demo-only threshold."
  fi
  pause

  step 6 "Show the AIopsTelemetry ticket" "AIopsTelemetry に起票された ticket を表示します。"
  local ticket_lines issue_id issue_trace title severity created_at
  if ! ticket_lines="$(wait_for_ticket)"; then
    fail "No open NFR-33 pod resource ticket appeared within ${ISSUE_WAIT_SECONDS}s."
    exit 1
  fi
  issue_id="$(printf "%s\n" "${ticket_lines}" | sed -n '1p')"
  issue_trace="$(printf "%s\n" "${ticket_lines}" | sed -n '2p')"
  title="$(printf "%s\n" "${ticket_lines}" | sed -n '3p')"
  severity="$(printf "%s\n" "${ticket_lines}" | sed -n '4p')"
  created_at="$(printf "%s\n" "${ticket_lines}" | sed -n '5p')"
  ok "Ticket raised: issue_id=${issue_id}"
  info "Title: ${title}"
  info "Severity: ${severity}"
  info "Trace: ${issue_trace}"
  info "Created: ${created_at}"
  curl_json "${AIOPS_URL}/api/issues/${issue_id}" | json_pretty | sed 's/^/    /'
  pause

  step 7 "Run RCA correlation through Invastigate" "Invastigate RCA が Langfuse と Prometheus の証拠を相関します。"
  info "Triggering RCA via AIopsTelemetry: POST ${AIOPS_URL}/api/analysis/issues/${issue_id}?force=true"
  trigger_rca "${issue_id}" | json_pretty | sed 's/^/    /'
  info "Waiting for RCA completion. This can take a few minutes if the LLM is involved."
  local rca_json rca_status
  set +e
  rca_json="$(wait_for_rca "${issue_id}")"
  rca_status=$?
  set -e
  if [ "${rca_status}" -eq 0 ]; then
    ok "RCA completed."
    printf "%s" "${rca_json}" | print_rca_summary
  elif [ "${rca_status}" -eq 2 ]; then
    warn "RCA returned failed status; showing details."
    printf "%s" "${rca_json}" | json_pretty | sed 's/^/    /'
  else
    fail "RCA did not complete within ${RCA_WAIT_SECONDS}s."
    exit 1
  fi

  step 8 "Demo close" "デモのまとめです。"
  say \
    "End-to-end flow complete: VM/container proof, normal traces, external Prometheus metrics, CPU pressure, AIopsTelemetry ticket, and RCA correlation." \
    "エンドツーエンド完了: VM/container 確認、通常 trace、外部 Prometheus metrics、CPU pressure、AIopsTelemetry ticket、RCA 相関まで確認しました。"
  printf "\n%b\n" "${BOLD}${GREEN}Demo finished successfully.${RESET}"
}

main "$@"
