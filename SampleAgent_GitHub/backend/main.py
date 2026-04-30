import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
    title="SampleAgent",
    description="Research RAG assistant powered by literature retrieval, PageRank, FAISS, and OpenAI",
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

app.include_router(auth_router)
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


class QueryRequest(BaseModel):
    query: str
    max_articles: int = 30
    top_k: int = 5


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

    try:
        result = rag_pipeline.query(
            req.query.strip(), req.max_articles, req.top_k, trace_ctx=ctx
        )
    except Exception as e:
        err_msg = str(e)
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
    return {
        "status": "ok",
        "pipeline_ready": rag_pipeline is not None,
        "langfuse_enabled": tracer.enabled,
        "autoscale_guardrails": {
            "target_cpu_utilization_pct": settings.AUTOSCALE_TARGET_CPU_UTILIZATION,
            "min_replicas": settings.AUTOSCALE_MIN_REPLICAS,
            "max_replicas": settings.AUTOSCALE_MAX_REPLICAS,
            "scale_up_cooldown_seconds": settings.AUTOSCALE_SCALE_UP_COOLDOWN_SECONDS,
            "scale_down_cooldown_seconds": settings.AUTOSCALE_SCALE_DOWN_COOLDOWN_SECONDS,
        },
    }
