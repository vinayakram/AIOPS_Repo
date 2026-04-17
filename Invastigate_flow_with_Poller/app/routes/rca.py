from fastapi import APIRouter, HTTPException

from app.agents.rca_agent import RCAAgent
from app.models.rca import RCARequest, RCAResponse
from app.models.common import ErrorResponse
from app.core import logger

router = APIRouter(prefix="/api/v1", tags=["rca"])

_agent = RCAAgent()


@router.post(
    "/rca",
    response_model=RCAResponse,
    summary="Root cause analysis on errors identified by the Error Analysis Agent",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "LLM or internal error"},
    },
)
async def root_cause_analysis(request: RCARequest) -> RCAResponse:
    """
    Accepts the Error Analysis output, routes to the correct data
    source(s) based on rca_target, and returns a detailed root cause
    analysis with causal chain, contributing factors, failure timeline,
    and blast radius.

    Routing:
      - rca_target == Agent     → Langfuse (AI agent traces)
      - rca_target == InfraLogs → Prometheus (infra metrics)
      - rca_target == Unknown   → Both Langfuse + Prometheus
    """
    logger.info(
        "POST /rca — agent=%s rca_target=%s error_type=%s errors=%d trace_id=%s",
        request.agent_name,
        request.rca_target.value,
        request.incident.error_type.value,
        len(request.error_analysis.errors),
        request.trace_id,
    )

    try:
        return await _agent.analyze_root_cause(request)
    except ValueError as exc:
        logger.error("RCA failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during RCA")
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
