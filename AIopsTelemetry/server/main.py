import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from server.database.engine import init_db
from server.engine import escalation_engine, process_manager
# metrics_collector disabled on feature/rca-external-service — host-OS psutil data
# is not a valid data source for this branch (only medical-rag trace data is used)
# from server.engine import metrics_collector
from server.api import ingest, traces, issues, escalations, metrics, health, agent, langfuse_traces
from server.api import autofix as autofix_api
from server.api import analysis as analysis_api
from server.api import incidents as incidents_api
from server.api import remediation as remediation_api

_escalation_task = None
_metrics_task = None

# ── Register known agent processes ───────────────────────────────────────────
_DOCS = Path(__file__).resolve().parents[2]  # Documents folder

def _register_agents():
    python = sys.executable

    ws_folder = str(_DOCS / "WebSearchAgent")
    ws_server = str(_DOCS / "WebSearchAgent" / "server.py")
    process_manager.register(
        "web-search-agent",
        cmd=[python, ws_server],
        cwd=ws_folder,
    )

    med_folder = str(_DOCS / "MedicalAgent")
    med_server = str(_DOCS / "MedicalAgent" / "run.py")
    process_manager.register(
        "medical-agent",
        cmd=[python, med_server],
        cwd=med_folder,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    init_db()
    _register_agents()
    global _escalation_task, _metrics_task
    _escalation_task = asyncio.create_task(escalation_engine.start())
    # metrics_collector disabled — only medical-rag trace data is the intended source
    # _metrics_task = asyncio.create_task(metrics_collector.start())
    yield
    # Shutdown
    escalation_engine.stop()
    # metrics_collector.stop()
    for task in (_escalation_task, _metrics_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="AIops Telemetry Server",
    version="1.0.0",
    lifespan=lifespan,
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(ingest.router, prefix="/api")
app.include_router(traces.router, prefix="/api")
app.include_router(issues.router, prefix="/api")
app.include_router(escalations.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(langfuse_traces.router, prefix="/api")
app.include_router(autofix_api.router, prefix="/api")
app.include_router(analysis_api.router, prefix="/api")
app.include_router(incidents_api.router, prefix="/api")
app.include_router(remediation_api.router, prefix="/api")

# ── Dashboard SPA ─────────────────────────────────────────────────────────────
_DASHBOARD_DIR = os.path.join(os.path.dirname(__file__), "dashboard")
if os.path.isdir(_DASHBOARD_DIR):
    app.mount("/static", StaticFiles(directory=_DASHBOARD_DIR), name="static")


@app.get("/")
@app.get("/dashboard")
async def serve_dashboard():
    index = os.path.join(_DASHBOARD_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return {"message": "AIops Telemetry Server", "docs": "/docs"}


@app.get("/light")
async def serve_light_dashboard():
    light = os.path.join(_DASHBOARD_DIR, "light.html")
    if os.path.isfile(light):
        return FileResponse(light)
    return {"message": "Light theme not found"}


@app.get("/light_j")
async def serve_light_j_dashboard():
    light_j = os.path.join(_DASHBOARD_DIR, "light_j.html")
    if os.path.isfile(light_j):
        return FileResponse(light_j)
    return {"message": "Japanese light theme not found"}
