from fastapi import APIRouter, HTTPException

from app.agents.recommendation_agent import RecommendationAgent
from app.models.recommendation import RecommendationRequest, RecommendationResponse
from app.models.common import ErrorResponse
from app.core import logger

router = APIRouter(prefix="/api/v1", tags=["recommendation"])

_agent = RecommendationAgent()


@router.post(
    "/recommend",
    response_model=RecommendationResponse,
    summary="Generate ranked solution recommendations from Error Analysis and RCA findings",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "LLM or internal error"},
    },
)
async def recommend_solutions(request: RecommendationRequest) -> RecommendationResponse:
    """
    Accepts the Error Analysis and RCA outputs, synthesizes the
    findings, and returns 1-4 ranked actionable solutions.

    Rank 1 = highest priority (directly fixes root cause).
    Only as many solutions as genuinely applicable are returned.
    """
    logger.info(
        "POST /recommend — agent=%s root_cause=%s (%s) errors=%d",
        request.agent_name,
        request.rca.root_cause.component,
        request.rca.root_cause.category.value,
        len(request.error_analysis.errors),
    )

    try:
        return await _agent.recommend(request)
    except ValueError as exc:
        logger.error("Recommendation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during recommendation")
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
