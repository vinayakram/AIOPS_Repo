#!/usr/bin/env bash
set -euo pipefail

SAMPLE_URL="${SAMPLE_URL:-http://localhost:8002}"
DEPENDENT_URL="${DEPENDENT_URL:-http://localhost:8010}"
LOAD_REQUESTS="${LOAD_REQUESTS:-18}"
LOAD_WORK_MS="${LOAD_WORK_MS:-1500}"
DEPENDENT_ATTEMPTS="${DEPENDENT_ATTEMPTS:-12}"
SLEEP_SECONDS="${SLEEP_SECONDS:-0.4}"

echo "Starting cascade scenario"
echo "  sample-agent:    ${SAMPLE_URL}"
echo "  dependent-agent: ${DEPENDENT_URL}"
echo "  load requests:   ${LOAD_REQUESTS} x ${LOAD_WORK_MS}ms"
echo "  checks:          ${DEPENDENT_ATTEMPTS}"

for i in $(seq 1 "${LOAD_REQUESTS}"); do
  curl -sS -X POST "${SAMPLE_URL}/api/demo/background-load" \
    -H 'Content-Type: application/json' \
    -d "{\"work_ms\":${LOAD_WORK_MS}}" >/dev/null &
done

sleep 1

for i in $(seq 1 "${DEPENDENT_ATTEMPTS}"); do
  echo "dependent check ${i}/${DEPENDENT_ATTEMPTS}"
  curl -sS -X POST "${DEPENDENT_URL}/api/run-cascade?fail_on_upstream_error=false" || true
  echo
  sleep "${SLEEP_SECONDS}"
done

wait || true

echo "Cascade scenario complete"
echo "Prometheus:"
echo "  sample-agent threshold breaches:"
echo "  dependent-agent upstream failures:"
echo "Langfuse:"
echo "  look for traces tagged triage-agent, cascade, mcp-evidence"
