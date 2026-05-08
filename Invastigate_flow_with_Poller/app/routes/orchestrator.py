"""
FastAPI route handler for the full pipeline orchestrator endpoint at POST /api/v1/investigate.
Accepts an InvestigationRequest and runs all five agents in sequence via the Orchestrator class.
Returns an InvestigationResponse with all agent outputs; partial results are returned if a step fails.
This is the low-level pipeline endpoint — prefer POST /api/v1/analyze for cache-aware frontend calls.

POST /api/v1/investigateの完全パイプラインオーケストレーターエンドポイントのFastAPIルートハンドラ。
InvestigationRequestを受け付け、Orchestratorクラスを介して5つのエージェントを順番に実行する。
すべてのエージェント出力を含むInvestigationResponseを返し、ステップ失敗時は部分結果を返す。
これはローレベルのパイプラインエンドポイントであり、フロントエンドはキャッシュ対応のPOST /api/v1/analyzeを推奨する。
"""
from fastapi import APIRouter, HTTPException

from app.agents.orchestrator import Orchestrator
from app.models.orchestrator import InvestigationRequest, InvestigationResponse
from app.models.common import ErrorResponse
from app.core import logger

router = APIRouter(prefix="/api/v1", tags=["orchestrator"])

_orchestrator = Orchestrator()


@router.post(
    "/investigate",
    response_model=InvestigationResponse,
    summary="Run the full 5-agent observability pipeline in a single call",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "Pipeline or internal error"},
    },
)
async def investigate(request: InvestigationRequest) -> InvestigationResponse:
    """
    Full pipeline orchestration — takes the same simple input as the
    Normalization Agent and chains all 5 agents automatically:

      Normalization → Correlation → Error Analysis → RCA → Recommendation

    Returns all agent outputs in a single response. If any step fails,
    subsequent steps are skipped and partial results are returned with
    pipeline_steps showing what succeeded and what failed.

    If no error is detected, the pipeline stops early after Normalization.
    """
    logger.info(
        "POST /investigate — agent=%s trace_id=%s ts=%s",
        request.agent_name, request.trace_id, request.timestamp,
    )

    try:
        return await _orchestrator.investigate(request)
    except Exception as exc:
        logger.exception("Unexpected error during investigation pipeline")
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}")
