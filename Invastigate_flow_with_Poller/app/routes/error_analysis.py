from fastapi import APIRouter, HTTPException

from app.agents.error_analysis_agent import ErrorAnalysisAgent
from app.models.error_analysis import ErrorAnalysisRequest, ErrorAnalysisResponse
from app.models.common import ErrorResponse
from app.core import logger

router = APIRouter(prefix="/api/v1", tags=["error-analysis"])

_agent = ErrorAnalysisAgent()


@router.post(
    "/error-analysis",
    response_model=ErrorAnalysisResponse,
    summary="Deep-dive error analysis on correlated failure components",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "LLM or internal error"},
    },
)
async def analyze_errors(request: ErrorAnalysisRequest) -> ErrorAnalysisResponse:
    """
    Accepts the correlation output from the Correlation Agent,
    routes to the correct data source(s) based on analysis_target,
    and returns a detailed error analysis with categorized errors,
    patterns, impacts, and propagation paths.

    Routing:
      - analysis_target == Agent     → Langfuse (AI agent traces)
      - analysis_target == InfraLogs → Prometheus (infra metrics)
      - analysis_target == Unknown   → Both Langfuse + Prometheus
    """
    logger.info(
        "POST /error-analysis — agent=%s target=%s error_type=%s trace_id=%s",
        request.agent_name,
        request.correlation.analysis_target.value,
        request.incident.error_type.value,
        request.trace_id,
    )

    try:
        return await _agent.analyze(request)
    except ValueError as exc:
        logger.error("Error analysis failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during error analysis")
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
