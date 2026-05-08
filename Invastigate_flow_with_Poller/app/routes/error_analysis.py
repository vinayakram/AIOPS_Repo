"""
FastAPI route handler for the standalone Error Analysis Agent endpoint at POST /api/v1/error-analysis.
Accepts an ErrorAnalysisRequest containing the correlation output and normalized incident.
Delegates to the ErrorAnalysisAgent which routes to Langfuse, Prometheus, or both based on analysis_target.
HTTP 500 is returned for LLM errors or validation failures; 422 for request schema violations.

POST /api/v1/error-analysisのスタンドアロンエラー分析エージェントエンドポイントのFastAPIルートハンドラ。
相関出力と正規化済みインシデントを含むErrorAnalysisRequestを受け付ける。
analysis_targetに基づいてLangfuse、Prometheus、または両方にルーティングするErrorAnalysisAgentに処理を委譲する。
LLMエラーまたは検証失敗にはHTTP 500を返し、リクエストスキーマ違反には422を返す。
"""
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
