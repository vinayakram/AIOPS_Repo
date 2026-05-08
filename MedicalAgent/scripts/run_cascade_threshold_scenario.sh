#!/usr/bin/env bash
# Orchestration script for the MedicalAgent cascade threshold AIOps demo scenario.
# Drives CPU load bursts against the sample-agent until a pod resource guardrail breach
# is observed, then fires dependent-agent cascade checks and waits for AIopsTelemetry
# to raise a triage-agent issue.
#
# MedicalAgentカスケードしきい値AIOpsデモシナリオのオーケストレーションスクリプト。
# podリソースガードレール超過が観測されるまでsample-agentにCPU負荷バーストを送出し、
# その後dependent-agentカスケードチェックを実行してAIopsTelemetryがtriage-agent
# イシューを起票するまで待機する。
set -euo pipefail

SAMPLE_URL="${SAMPLE_URL:-http://localhost:8002}"
DEPENDENT_URL="${DEPENDENT_URL:-http://localhost:8010}"
LOAD_BURSTS="${LOAD_BURSTS:-4}"
LOAD_REQUESTS="${LOAD_REQUESTS:-24}"
LOAD_WORK_MS="${LOAD_WORK_MS:-2200}"
BREACH_WAIT_SECONDS="${BREACH_WAIT_SECONDS:-12}"
DEPENDENT_ATTEMPTS="${DEPENDENT_ATTEMPTS:-18}"
UPSTREAM_ERRORS_NEEDED="${UPSTREAM_ERRORS_NEEDED:-3}"
AIOPS_URL="${AIOPS_URL:-http://localhost:7000}"
AIOPS_WAIT_SECONDS="${AIOPS_WAIT_SECONDS:-45}"
SLEEP_SECONDS="${SLEEP_SECONDS:-0.4}"

sample_breached=0
upstream_errors=0

sample_health() {
  curl -fsS "${SAMPLE_URL}/api/health"
}

breach_active() {
  sample_health | grep -q '"breached":true'
}

start_load_burst() {
  for _i in $(seq 1 "${LOAD_REQUESTS}"); do
    curl -sS -X POST "${SAMPLE_URL}/api/demo/background-load" \
      -H 'Content-Type: application/json' \
      -d "{\"work_ms\":${LOAD_WORK_MS}}" >/dev/null &
  done
}

echo "Starting cascade scenario"
echo "  sample-agent:    ${SAMPLE_URL}"
echo "  dependent-agent: ${DEPENDENT_URL}"
echo "  load bursts:     ${LOAD_BURSTS}"
echo "  load requests:   ${LOAD_REQUESTS} x ${LOAD_WORK_MS}ms"
echo "  checks:          ${DEPENDENT_ATTEMPTS}"
echo "  aiops url:       ${AIOPS_URL}"

for burst in $(seq 1 "${LOAD_BURSTS}"); do
  echo "load burst ${burst}/${LOAD_BURSTS}"
  start_load_burst
  for _wait in $(seq 1 "${BREACH_WAIT_SECONDS}"); do
    if breach_active; then
      sample_breached=1
      break
    fi
    sleep 1
  done
  if [ "${sample_breached}" -eq 1 ]; then
    break
  fi
done

if [ "${sample_breached}" -eq 1 ]; then
  echo "sample-agent guardrail breach observed; starting dependent-agent checks"
else
  echo "sample-agent breach not yet observed; continuing with dependent-agent checks anyway"
fi

for i in $(seq 1 "${DEPENDENT_ATTEMPTS}"); do
  echo "dependent check ${i}/${DEPENDENT_ATTEMPTS}"
  body="$(curl -sS -X POST "${DEPENDENT_URL}/api/run-cascade?fail_on_upstream_error=false" || true)"
  echo "${body}"
  if printf '%s' "${body}" | grep -q '"result":"upstream_'; then
    upstream_errors=$((upstream_errors + 1))
  fi
  if [ "${upstream_errors}" -ge "${UPSTREAM_ERRORS_NEEDED}" ]; then
    echo "observed ${upstream_errors} upstream cascade failures; stopping active checks"
    break
  fi
  sleep "${SLEEP_SECONDS}"
done

wait || true

echo
echo "Waiting for AIopsTelemetry to list a real triage-agent issue..."
for _wait in $(seq 1 "${AIOPS_WAIT_SECONDS}"); do
  issue_json="$(curl -sS "${AIOPS_URL}/api/issues?app_name=triage-agent&lang=en" || true)"
  if printf '%s' "${issue_json}" | grep -q '"total":[1-9]'; then
    echo "${issue_json}"
    break
  fi
  sleep 1
done

echo "Cascade scenario complete"
echo "Prometheus:"
echo "  sample-agent threshold breaches:"
echo "  dependent-agent upstream failures:"
echo "Langfuse:"
echo "  look for traces tagged triage-agent, cascade, mcp-evidence"
