#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-medical-rag-pod}"
APP_URL="${APP_URL:-http://localhost:8002}"
AIOPS_URL="${AIOPS_URL:-http://localhost:7000}"
BREACHES_REQUIRED="${BREACHES_REQUIRED:-3}"
LOAD_SECONDS="${LOAD_SECONDS:-12}"
POLL_SECONDS="${POLL_SECONDS:-2}"

echo "Sample Agent pod CPU threshold test"
echo "Container: ${CONTAINER_NAME}"
echo "App URL:   ${APP_URL}"
echo "AIops URL: ${AIOPS_URL}"
echo

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed or not on PATH"
  exit 1
fi

if ! docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "Container ${CONTAINER_NAME} not found. Start it with:"
  echo "  cd MedicalAgent && docker compose up -d --build"
  exit 1
fi

if ! docker ps --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "Container ${CONTAINER_NAME} is not running"
  exit 1
fi

echo "Warming resource sampler..."
curl -fsS "${APP_URL}/api/health" >/dev/null || true
sleep 1

breaches=0
for attempt in $(seq 1 "${BREACHES_REQUIRED}"); do
  echo
  echo "Breach attempt ${attempt}/${BREACHES_REQUIRED}: starting bounded CPU load inside the pod"
  docker exec -d "${CONTAINER_NAME}" python -c "import time; end=time.time()+${LOAD_SECONDS}; x=0
while time.time()<end:
    x=(x*3+1)%1000003"

  deadline=$((SECONDS + LOAD_SECONDS + 8))
  saw_breach=0
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(curl -sS -o /tmp/sample_agent_pod_threshold_body.txt -w '%{http_code}' "${APP_URL}/" || true)"
    body="$(tr '\n' ' ' </tmp/sample_agent_pod_threshold_body.txt 2>/dev/null || true)"
    if [ "${status}" = "503" ] && printf '%s' "${body}" | grep -qi "application is not reachable"; then
      saw_breach=1
      breaches=$((breaches + 1))
      echo "Observed breach ${breaches}: HTTP 503 ${body}"
      break
    fi
    sleep "${POLL_SECONDS}"
  done

  if [ "${saw_breach}" -ne 1 ]; then
    echo "No threshold breach observed during attempt ${attempt}"
  fi
  sleep 2
done

echo
echo "Observed ${breaches}/${BREACHES_REQUIRED} threshold breaches."
if [ "${breaches}" -lt "${BREACHES_REQUIRED}" ]; then
  echo "Threshold did not breach enough times. Lower POD_CPU_THRESHOLD_PERCENT or increase LOAD_SECONDS for the demo pod only."
  exit 2
fi

echo "Waiting for AIops detector to open the NFR-33 ticket..."
for _ in $(seq 1 20); do
  issues="$(curl -fsS "${AIOPS_URL}/api/issues?app_name=sample-agent&status=OPEN&limit=20" || true)"
  if printf '%s' "${issues}" | grep -q "nfr_pod_resource_threshold_breach"; then
    echo "AIops ticket raised:"
    printf '%s\n' "${issues}"
    exit 0
  fi
  sleep 3
done

echo "Breaches were emitted, but the AIops ticket was not visible yet. Check that AIopsTelemetry is running and escalation_engine is active."
exit 3
