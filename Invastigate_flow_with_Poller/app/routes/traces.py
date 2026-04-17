from fastapi import APIRouter, HTTPException, Query

from app.services.trace_store import TraceStore
from app.core.logging import logger
from app.core.database import init_db, get_db

router = APIRouter(prefix="/api/v1", tags=["traces"])

_store = TraceStore()


async def _ensure_db() -> None:
    """Initialize DB if it hasn't been initialized yet (safety net for race conditions)."""
    try:
        await get_db()
    except RuntimeError:
        logger.warning("DB not initialized on request — running init_db()")
        await init_db()


@router.get(
    "/traces/{trace_id}",
    summary="Get all agent inputs and outputs for a trace",
)
async def get_trace(trace_id: str):
    """
    Returns the full stored record for a trace_id including
    every agent's input and output:

      - normalization_input / normalization_output
      - correlation_input / correlation_output
      - error_analysis_input / error_analysis_output
      - rca_input / rca_output
      - recommendation_input / recommendation_output
    """
    logger.info("GET /traces/%s", trace_id)
    await _ensure_db()
    result = await _store.get_trace(trace_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found")
    return result


@router.get(
    "/traces",
    summary="List all stored traces",
)
async def list_traces(
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """List stored traces with pagination (summary only — no agent IO)."""
    await _ensure_db()
    try:
        results = await _store.list_traces(limit=limit, offset=offset)
        return {"traces": results, "count": len(results)}
    except Exception as exc:
        logger.error("list_traces failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list traces: {exc}")
