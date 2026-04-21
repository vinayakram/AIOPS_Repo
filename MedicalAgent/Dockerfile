FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDICAL_AGENT_RELOAD=0 \
    POD_CPU_THRESHOLD_ENABLED=true \
    POD_CPU_THRESHOLD_PERCENT=90 \
    POD_MEMORY_THRESHOLD_ENABLED=true \
    POD_MEMORY_THRESHOLD_PERCENT=90 \
    POD_THRESHOLD_TELEMETRY_MIN_INTERVAL_SECONDS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY run.py .

EXPOSE 8002

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8002/api/health || exit 1

CMD ["python", "run.py"]
