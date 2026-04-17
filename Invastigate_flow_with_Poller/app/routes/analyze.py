from fastapi import APIRouter, HTTPException, Query

from app.agents.orchestrator import Orchestrator
from app.models.orchestrator import InvestigationRequest
from app.models.common import ErrorResponse
from app.services.trace_store import TraceStore
from app.core.logging import logger

router = APIRouter(prefix="/api/v1", tags=["frontend"])

_orchestrator = Orchestrator()
_store = TraceStore()


@router.post(
    "/analyze",
    summary="Frontend entry point — returns cached result or runs the full pipeline",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Pipeline or internal error"},
    },
)
async def analyze(
    request: InvestigationRequest,
    force: bool = Query(False, description="Bypass cached trace result and run the current backend pipeline"),
):
    """
    Smart frontend API — the single endpoint the UI should call.

    Logic:
      1. Check if trace_id already exists in the DB
         (i.e. the poller already processed it, or a previous request did)
      2. If YES  → return the stored result directly (no LLM calls, instant)
      3. If NO   → run the full 5-agent orchestration pipeline, store it,
                    and return the result

    This means:
      - If the poller already processed this trace_id → instant response from DB
      - If the frontend sends the same trace_id twice → second call is instant
      - If it's a brand new trace_id → pipeline runs, result is stored for future
    """
    logger.info(
        "POST /analyze — trace_id=%s agent=%s ts=%s force=%s",
        request.trace_id, request.agent_name, request.timestamp, force,
    )

    # ── Check DB first ────────────────────────────────────────────────
    existing = None
    if not force:
        try:
            existing = await _store.get_trace(request.trace_id)
        except Exception as exc:
            logger.warning("DB check failed for trace_id=%s: %s — running pipeline", request.trace_id, exc)
            existing = None
    else:
        logger.info(
            "POST /analyze — force=true for trace_id=%s — bypassing cached result",
            request.trace_id,
        )

    if existing is not None:
        logger.info(
            "POST /analyze — trace_id=%s found in DB (status=%s) — returning cached result",
            request.trace_id, existing.get("status", "unknown"),
        )
        return {
            "source": "cache",
            "message": f"Trace '{request.trace_id}' already processed — returning stored result",
            "data": existing,
        }

    # ── Not in DB → run the full pipeline ─────────────────────────────
    logger.info(
        "POST /analyze — trace_id=%s not in DB — starting orchestration pipeline",
        request.trace_id,
    )

    try:
        result = await _orchestrator.investigate(request)
        return {
            "source": "pipeline",
            "message": f"Pipeline executed for trace '{request.trace_id}'",
            "data": result.model_dump(mode="json"),
        }
    except Exception as exc:
        logger.exception("Pipeline failed for trace_id=%s", request.trace_id)
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")
