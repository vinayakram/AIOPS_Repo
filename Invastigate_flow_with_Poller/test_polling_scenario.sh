#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
# TESTING THE POLLING + DEDUP SCENARIO
# ═══════════════════════════════════════════════════════════════════════
#
# This script walks through the full test scenario step by step.
# Run each section manually — don't run the whole file at once.
#
# Prerequisites:
#   Terminal 1: Mock AIOps server
#   Terminal 2: Main observability app
#   Terminal 3: This test script (curl commands)
# ═══════════════════════════════════════════════════════════════════════


# ─────────────────────────────────────────────────────────────────────
# STEP 0: Create a .env file (if you don't have one)
# ─────────────────────────────────────────────────────────────────────

cat > .env << 'EOF'
OPENAI_API_KEY=changeme
OPENAI_MODEL=gpt-4o

LANGFUSE_PUBLIC_KEY=pk-lf-your-key
LANGFUSE_SECRET_KEY=changeme
LANGFUSE_HOST=https://cloud.langfuse.com

PROMETHEUS_URL=http://localhost:9091

# AIOps Poller — points to the mock server
AIOPS_SERVER_URL=http://localhost:9090
AIOPS_POLL_ENDPOINT=/api/v1/incidents
AIOPS_POLL_INTERVAL_SECONDS=20
AIOPS_POLL_ENABLED=true

DB_PATH=investigations.db
EOF


# ─────────────────────────────────────────────────────────────────────
# STEP 1: Start the Mock AIOps server (Terminal 1)
# ─────────────────────────────────────────────────────────────────────

# Terminal 1:
python mock_aiops_server.py

# Expected output:
#   Mock AIOps Engine — http://localhost:9090
#   Pre-loaded with 2 incidents:
#     • trace-med-rag-001 (medical-rag)
#     • trace-retrieval-dns-002 (retrieval-agent)


# ─────────────────────────────────────────────────────────────────────
# STEP 2: Start the main observability app (Terminal 2)
# ─────────────────────────────────────────────────────────────────────

# Terminal 2:
uvicorn app.main:app --reload --port 8000

# Expected logs:
#   AIOps Poller | loaded 0 existing trace_ids from DB
#   AIOps Poller started | url=http://localhost:9090/api/v1/incidents interval=20s
#   AIOps Poller | poll #1 — fetching incidents
#   AIOps Poller | fetched 2 incidents
#   AIOps Poller | NEW incident — processing trace_id=trace-med-rag-001 ...
#   Pipeline [trace-med-rag-001] | Step 1/5 — Normalization
#   ... (pipeline runs for trace-med-rag-001)
#   AIOps Poller | NEW incident — processing trace_id=trace-retrieval-dns-002 ...
#   ... (pipeline runs for trace-retrieval-dns-002)


# ─────────────────────────────────────────────────────────────────────
# STEP 3: Check poller status (Terminal 3)
# ─────────────────────────────────────────────────────────────────────

# After ~20 seconds, check the poller stats:
curl -s http://localhost:8000/api/v1/poller/status | python3 -m json.tool

# Expected output:
# {
#     "running": true,
#     "poll_interval_seconds": 20,
#     "aiops_server": "http://localhost:9090/api/v1/incidents",
#     "total_polled": 1,
#     "total_processed": 2,      ← Both incidents were processed
#     "total_skipped": 0,
#     "total_errors": 0,
#     "known_trace_ids": 2,      ← 2 trace_ids known
#     "last_poll_time": "2026-..."
# }


# ─────────────────────────────────────────────────────────────────────
# STEP 4: Wait for the SECOND poll cycle (~20s later)
# ─────────────────────────────────────────────────────────────────────

# The poller polls again. Check logs in Terminal 2:
#   AIOps Poller | poll #2 — fetching incidents
#   AIOps Poller | fetched 2 incidents
#   AIOps Poller | skip (in-memory) trace_id=trace-med-rag-001    ← SKIPPED!
#   AIOps Poller | skip (in-memory) trace_id=trace-retrieval-dns-002  ← SKIPPED!

# Check stats again:
curl -s http://localhost:8000/api/v1/poller/status | python3 -m json.tool

# Expected:
# {
#     "total_polled": 2,         ← Polled twice
#     "total_processed": 2,      ← Still only 2 processed (no reprocessing!)
#     "total_skipped": 2,        ← 2 skipped on second poll
#     "known_trace_ids": 2
# }


# ─────────────────────────────────────────────────────────────────────
# STEP 5: Add a NEW incident to the mock server
# ─────────────────────────────────────────────────────────────────────

curl -X POST http://localhost:9090/api/v1/incidents \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "trace-planner-mem-003",
    "timestamp": "2026-04-10T04:15:00.000Z",
    "agent_name": "planner-v1"
  }'

# Expected: {"status":"added","trace_id":"trace-planner-mem-003","total_incidents":3}

# Verify mock server has 3 incidents now:
curl -s http://localhost:9090/api/v1/incidents/status | python3 -m json.tool


# ─────────────────────────────────────────────────────────────────────
# STEP 6: Wait for next poll cycle — only the NEW one gets processed
# ─────────────────────────────────────────────────────────────────────

# Watch Terminal 2 logs:
#   AIOps Poller | poll #3 — fetching incidents
#   AIOps Poller | fetched 3 incidents
#   AIOps Poller | skip (in-memory) trace_id=trace-med-rag-001
#   AIOps Poller | skip (in-memory) trace_id=trace-retrieval-dns-002
#   AIOps Poller | NEW incident — processing trace_id=trace-planner-mem-003  ← ONLY NEW ONE!
#   Pipeline [trace-planner-mem-003] | Step 1/5 — Normalization
#   ...

# Check stats:
curl -s http://localhost:8000/api/v1/poller/status | python3 -m json.tool

# Expected:
# {
#     "total_polled": 3,
#     "total_processed": 3,      ← Now 3
#     "total_skipped": 4,        ← 2 (poll 2) + 2 (poll 3) = 4 skipped
#     "known_trace_ids": 3
# }


# ─────────────────────────────────────────────────────────────────────
# STEP 7: Verify stored results via traces API
# ─────────────────────────────────────────────────────────────────────

# List all stored traces:
curl -s http://localhost:8000/api/v1/traces | python3 -m json.tool

# Get full agent I/O for a specific trace:
curl -s http://localhost:8000/api/v1/traces/trace-med-rag-001 | python3 -m json.tool
curl -s http://localhost:8000/api/v1/traces/trace-planner-mem-003 | python3 -m json.tool


# ─────────────────────────────────────────────────────────────────────
# STEP 8: Test RESTART dedup (the key scenario!)
# ─────────────────────────────────────────────────────────────────────

# 1. Stop the main app (Ctrl+C in Terminal 2)
# 2. Restart it:
uvicorn app.main:app --reload --port 8000

# Watch the startup logs:
#   AIOps Poller | loaded 3 existing trace_ids from DB  ← Loaded from SQLite!
#   AIOps Poller started | ... known_traces=3
#   AIOps Poller | poll #1 — fetching incidents
#   AIOps Poller | fetched 3 incidents
#   AIOps Poller | skip (in-memory) trace_id=trace-med-rag-001      ← SKIPPED (loaded from DB)
#   AIOps Poller | skip (in-memory) trace_id=trace-retrieval-dns-002  ← SKIPPED
#   AIOps Poller | skip (in-memory) trace_id=trace-planner-mem-003    ← SKIPPED

# ALL 3 were skipped even after restart! The DB-backed dedup works.


# ─────────────────────────────────────────────────────────────────────
# STEP 9: Add another incident AFTER restart — only it gets processed
# ─────────────────────────────────────────────────────────────────────

curl -X POST http://localhost:9090/api/v1/incidents \
  -H "Content-Type: application/json" \
  -d '{
    "trace_id": "trace-summarizer-004",
    "timestamp": "2026-04-10T06:00:00.000Z",
    "agent_name": "summarizer-v2"
  }'

# Wait for next poll cycle. Logs:
#   AIOps Poller | skip (in-memory) trace_id=trace-med-rag-001
#   AIOps Poller | skip (in-memory) trace_id=trace-retrieval-dns-002
#   AIOps Poller | skip (in-memory) trace_id=trace-planner-mem-003
#   AIOps Poller | NEW incident — processing trace_id=trace-summarizer-004  ← Only new one!


# ─────────────────────────────────────────────────────────────────────
# STEP 10: Manual poller control
# ─────────────────────────────────────────────────────────────────────

# Stop poller:
curl -X POST http://localhost:8000/api/v1/poller/stop
# {"status":"stopped","message":"Poller has been stopped"}

# Verify it stopped:
curl -s http://localhost:8000/api/v1/poller/status | python3 -m json.tool
# "running": false

# Start it again:
curl -X POST http://localhost:8000/api/v1/poller/start
# {"status":"started","message":"Poller is now running"}
