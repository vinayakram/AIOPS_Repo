import json
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth.routes import router as auth_router, get_current_user
from .database.models import init_db, seed_default_user, User, TraceLog, get_db
from .rag.pipeline import RAGPipeline
from .tracing.langfuse_client import tracer
from .tracing.aiops_client import send_trace as aiops_send_trace
from .config import settings
from . import state
from .monitoring import metrics_response, observe_http_request, observe_query, track_query
from .pod_guard import pod_resource_guard

rag_pipeline: RAGPipeline = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global rag_pipeline
    init_db()
    seed_default_user()
    print("Initializing RAG pipeline...")
    rag_pipeline = RAGPipeline()
    print("Application ready! Visit http://localhost:8000")
    yield
    print("Shutting down.")


app = FastAPI(
    title="MedicalRAG",
    description="Medical research RAG — PubMed + PageRank + FAISS + OpenAI",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def pod_resource_guard_middleware(request, call_next):
    if request.url.path not in {"/metrics", "/api/health"}:
        state_snapshot = pod_resource_guard.check()
        if state_snapshot.breached:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "application is not reachable",
                    "reason": state_snapshot.reason,
                    "cpu_percent": state_snapshot.cpu_percent,
                    "cpu_threshold_percent": state_snapshot.cpu_threshold_percent,
                    "memory_percent": state_snapshot.memory_percent,
                    "memory_threshold_percent": state_snapshot.memory_threshold_percent,
                },
            )
    return await call_next(request)


@app.middleware("http")
async def prometheus_metrics_middleware(request, call_next):
    started_at = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        observe_http_request(
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_seconds=time.perf_counter() - started_at,
        )


app.include_router(auth_router)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


class QueryRequest(BaseModel):
    query: str
    max_articles: int = 30
    top_k: int = 5
    scenario: str | None = None


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def login_page():
    return FileResponse("frontend/index.html")


@app.get("/register")
async def register_page():
    return FileResponse("frontend/register.html")


@app.get("/chat")
async def chat_page():
    return FileResponse("frontend/chat.html")


@app.get("/dashboard")
async def dashboard_page():
    return FileResponse("frontend/dashboard.html")


@app.get("/demo-scenarios")
async def demo_scenarios_page():
    return FileResponse("frontend/scenarios.html")


# ── Query API ─────────────────────────────────────────────────────────────────

@app.post("/api/query")
async def query_endpoint(
    req: QueryRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not rag_pipeline:
        raise HTTPException(status_code=503, detail="RAG pipeline not ready")
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    # Create Langfuse trace
    ctx = tracer.new_trace(query=req.query.strip(), user_id=current_user.username)
    query_started_at = time.perf_counter()

    try:
        with track_query():
            result = rag_pipeline.query(
                req.query.strip(),
                req.max_articles,
                req.top_k,
                trace_ctx=ctx,
                scenario=req.scenario,
            )
    except Exception as e:
        err_msg = str(e)
        observe_query(query_started_at, "error")
        # Close Langfuse trace with ERROR level so sync derives correct status
        tracer.finish_trace(ctx, {"answer": ""}, error=err_msg)
        # Send error trace to AIops (non-blocking)
        aiops_send_trace(ctx, {"answer": ""}, user_id=current_user.username, error=err_msg)
        try:
            langfuse_url = None
            if tracer.enabled:
                host = settings.LANGFUSE_HOST.rstrip("/")
                langfuse_url = f"{host}/trace/{ctx.trace_id}"
            log = TraceLog(
                trace_id=ctx.trace_id,
                user_id=current_user.username,
                query=req.query.strip(),
                total_duration_ms=round(ctx.total_duration_ms, 1),
                articles_fetched=0,
                pagerank_method="error",
                top_k=req.top_k,
                steps_json=json.dumps(ctx.steps_summary()),
                answer_preview=f"ERROR: {err_msg}"[:300],
                langfuse_url=langfuse_url,
            )
            db.add(log)
            db.commit()
        except Exception as log_error:
            print(f"[Trace] Failed to save error trace log: {log_error}")
        raise HTTPException(status_code=500, detail=err_msg)

    observe_query(query_started_at, "success", result.get("total_fetched", 0))

    # Finish Langfuse trace
    tracer.finish_trace(ctx, result)

    # Forward to AIops Telemetry server (non-blocking)
    aiops_send_trace(ctx, result, user_id=current_user.username)

    # Build Langfuse URL if enabled
    langfuse_url = None
    if tracer.enabled:
        host = settings.LANGFUSE_HOST.rstrip("/")
        langfuse_url = f"{host}/trace/{ctx.trace_id}"

    # Persist trace to SQLite
    try:
        log = TraceLog(
            trace_id=ctx.trace_id,
            user_id=current_user.username,
            query=req.query.strip(),
            total_duration_ms=round(ctx.total_duration_ms, 1),
            articles_fetched=result.get("total_fetched", 0),
            pagerank_method=result.get("pagerank_method", "n/a"),
            top_k=req.top_k,
            steps_json=json.dumps(ctx.steps_summary()),
            answer_preview=result.get("answer", "")[:300],
            langfuse_url=langfuse_url,
        )
        db.add(log)
        db.commit()
    except Exception as e:
        print(f"[Trace] Failed to save trace log: {e}")

    result["trace_id"] = ctx.trace_id
    if langfuse_url:
        result["langfuse_url"] = langfuse_url
    return result


# ── Dashboard API ─────────────────────────────────────────────────────────────

@app.get("/api/traces")
async def list_traces(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return recent trace logs for the dashboard."""
    rows = (
        db.query(TraceLog)
        .order_by(TraceLog.created_at.desc())
        .limit(limit)
        .all()
    )
    traces = []
    for r in rows:
        try:
            steps = json.loads(r.steps_json or "[]")
        except Exception:
            steps = []
        traces.append({
            "trace_id": r.trace_id,
            "user_id": r.user_id,
            "query": r.query,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "total_duration_ms": r.total_duration_ms,
            "articles_fetched": r.articles_fetched,
            "pagerank_method": r.pagerank_method,
            "top_k": r.top_k,
            "steps": steps,
            "answer_preview": r.answer_preview,
            "langfuse_url": r.langfuse_url,
        })
    return {"traces": traces, "count": len(traces)}


@app.get("/api/traces/stats")
async def trace_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregate stats for the dashboard header."""
    rows = db.query(TraceLog).all()
    if not rows:
        return {
            "total_queries": 0,
            "avg_duration_ms": 0,
            "avg_articles": 0,
            "citation_pct": 0,
            "langfuse_enabled": tracer.enabled,
            "langfuse_host": settings.LANGFUSE_HOST if tracer.enabled else None,
        }
    total = len(rows)
    avg_dur = sum(r.total_duration_ms or 0 for r in rows) / total
    avg_art = sum(r.articles_fetched or 0 for r in rows) / total
    citation = sum(1 for r in rows if r.pagerank_method == "citation")
    return {
        "total_queries": total,
        "avg_duration_ms": round(avg_dur, 1),
        "avg_articles": round(avg_art, 1),
        "citation_pct": round(citation / total * 100, 1),
        "langfuse_enabled": tracer.enabled,
        "langfuse_host": settings.LANGFUSE_HOST if tracer.enabled else None,
    }


@app.get("/api/admin/llm-access")
async def get_llm_access(current_user: User = Depends(get_current_user)):
    """Return the current LLM access state."""
    return {"llm_enabled": state.llm_enabled}


@app.post("/api/admin/llm-access")
async def set_llm_access(enabled: bool, current_user: User = Depends(get_current_user)):
    """Toggle LLM access on or off (demo helper)."""
    state.llm_enabled = enabled
    state.reset_llm_disabled_attempts()
    return {"llm_enabled": state.llm_enabled}


@app.get("/api/health")
async def health():
    resource_state = pod_resource_guard.check()
    return {
        "status": "degraded" if resource_state.breached else "ok",
        "pipeline_ready": rag_pipeline is not None,
        "langfuse_enabled": tracer.enabled,
        "pod_resource_guard": {
            "breached": resource_state.breached,
            "reason": resource_state.reason,
            "cpu_percent": resource_state.cpu_percent,
            "cpu_threshold_percent": resource_state.cpu_threshold_percent,
            "memory_percent": resource_state.memory_percent,
            "memory_threshold_percent": resource_state.memory_threshold_percent,
        },
    }


@app.get("/metrics")
async def metrics():
    return metrics_response()
