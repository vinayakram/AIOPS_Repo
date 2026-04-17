from fastapi import APIRouter, HTTPException

from app.agents.normalization_agent import NormalizationAgent
from app.models.normalization import NormalizationRequest, NormalizationResponse
from app.models.common import ErrorResponse
from app.core import logger

router = APIRouter(prefix="/api/v1", tags=["normalization"])

_agent = NormalizationAgent()


@router.post(
    "/normalize",
    response_model=NormalizationResponse,
    summary="Normalize raw logs into a structured incident",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "LLM or internal error"},
    },
)
async def normalize_logs(request: NormalizationRequest) -> NormalizationResponse:
    """
    Accepts raw log entries and returns a structured, normalized incident
    produced by the Normalization Agent (GPT-4o).
    """
    logger.info(
        "POST /normalize — agent=%s trace=%s ts=%s",
        request.agent_name, request.trace_id, request.timestamp,
    )

    try:
        return await _agent.normalize(request)
    except ValueError as exc:
        logger.error("Normalization failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during normalization")
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
