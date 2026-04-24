"""
Mock AIOps Engine Server

Simulates an external AIOps platform that holds incident records.
The poller fetches from this server every 20 seconds.

Usage:
    python mock_aiops_server.py

Runs on port 9090 by default (matching AIOPS_SERVER_URL in .env).

Endpoints:
    GET  /api/v1/incidents          → Returns all incidents
    POST /api/v1/incidents          → Add a new incident
    POST /api/v1/incidents/reset    → Clear all incidents
    GET  /api/v1/incidents/status   → Show current incident count

The server comes pre-loaded with 2 sample incidents.
Add more via POST or edit the SEED_INCIDENTS list below.
"""

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Mock AIOps Engine", version="1.0.0")

# ── Seed Data ─────────────────────────────────────────────────────────

SEED_INCIDENTS = [
    {
        "trace_id": "trace-med-rag-001",
        "timestamp": "2026-04-10T04:19:55.204Z",
        "agent_name": "sample-agent",
    },
    {
        "trace_id": "trace-retrieval-dns-002",
        "timestamp": "2026-04-10T04:18:30.000Z",
        "agent_name": "retrieval-agent",
    },
]

# In-memory store
_incidents: list[dict] = list(SEED_INCIDENTS)


# ── Models ────────────────────────────────────────────────────────────

class IncidentInput(BaseModel):
    trace_id: str
    timestamp: str
    agent_name: str


# ── Endpoints ─────────────────────────────────────────────────────────

@app.get("/api/v1/incidents")
async def get_incidents():
    """
    Returns all incidents. This is what the poller fetches every 20s.

    Response format:
    {
        "incidents": [
            {"trace_id": "...", "timestamp": "...", "agent_name": "..."},
            ...
        ]
    }
    """
    return {"incidents": _incidents}


@app.post("/api/v1/incidents")
async def add_incident(incident: IncidentInput):
    """
    Add a new incident to the mock server.
    The poller will pick it up on the next poll cycle.

    Example:
        curl -X POST http://localhost:9090/api/v1/incidents \
            -H "Content-Type: application/json" \
            -d '{"trace_id":"trace-new-003","timestamp":"2026-04-10T05:00:00Z","agent_name":"planner-v1"}'
    """
    entry = incident.model_dump()

    # Check for duplicate
    for existing in _incidents:
        if existing["trace_id"] == entry["trace_id"]:
            return {"status": "duplicate", "message": f"trace_id '{entry['trace_id']}' already exists"}

    _incidents.append(entry)
    return {
        "status": "added",
        "trace_id": entry["trace_id"],
        "total_incidents": len(_incidents),
    }


@app.post("/api/v1/incidents/reset")
async def reset_incidents():
    """Clear all incidents and reload seed data."""
    _incidents.clear()
    _incidents.extend(SEED_INCIDENTS)
    return {"status": "reset", "total_incidents": len(_incidents)}


@app.get("/api/v1/incidents/status")
async def incidents_status():
    """Show current state."""
    return {
        "total_incidents": len(_incidents),
        "trace_ids": [i["trace_id"] for i in _incidents],
    }


if __name__ == "__main__":
    print("=" * 60)
    print("  Mock AIOps Engine — http://localhost:9090")
    print("=" * 60)
    print(f"  Pre-loaded with {len(SEED_INCIDENTS)} incidents:")
    for inc in SEED_INCIDENTS:
        print(f"    • {inc['trace_id']} ({inc['agent_name']})")
    print()
    print("  The observability poller will fetch from:")
    print("    GET http://localhost:9090/api/v1/incidents")
    print()
    print("  Add new incidents at runtime:")
    print('    curl -X POST http://localhost:9090/api/v1/incidents \\')
    print('      -H "Content-Type: application/json" \\')
    print('      -d \'{"trace_id":"trace-new-003","timestamp":"2026-04-10T05:00:00Z","agent_name":"planner-v1"}\'')
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=9090)
