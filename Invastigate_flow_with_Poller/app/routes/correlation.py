"""
FastAPI route handler for the standalone Correlation Agent endpoint at POST /api/v1/correlate.
Accepts a CorrelationRequest containing the normalized incident and optional trace_id.
Delegates to the CorrelationAgent which fetches logs and returns a causal failure graph.
HTTP 500 is returned for LLM errors or validation failures; 422 for request schema violations.

POST /api/v1/correlateのスタンドアロン相関エージェントエンドポイントのFastAPIルートハンドラ。
正規化済みインシデントとオプションのtrace_idを含むCorrelationRequestを受け付ける。
ログを取得して因果障害グラフを返すCorrelationAgentに処理を委譲する。
LLMエラーまたは検証失敗にはHTTP 500を返し、リクエストスキーマ違反には422を返す。
"""
from fastapi import APIRouter, HTTPException

from app.agents.correlation_agent import CorrelationAgent
from app.models.correlation import CorrelationRequest, CorrelationResponse
from app.models.common import ErrorResponse
from app.core import logger

router = APIRouter(prefix="/api/v1", tags=["correlation"])

_agent = CorrelationAgent()


@router.post(
    "/correlate",
    response_model=CorrelationResponse,
    summary="Correlate logs across systems to build a causal failure graph",
    responses={
        422: {"model": ErrorResponse, "description": "Validation error"},
        500: {"model": ErrorResponse, "description": "LLM or internal error"},
    },
)
async def correlate_logs(request: CorrelationRequest) -> CorrelationResponse:
    """
    Accepts the normalized incident from the Normalization Agent,
    fetches logs from Langfuse and/or Prometheus, and returns a
    causal failure graph with timeline, peer components, and root cause.
    """
    logger.info(
        "POST /correlate — agent=%s error_type=%s trace_id=%s",
        request.agent_name,
        request.incident.error_type.value,
        request.trace_id,
    )

    try:
        return await _agent.correlate(request)
    except ValueError as exc:
        logger.error("Correlation failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during correlation")
        raise HTTPException(status_code=500, detail=f"Internal error: {exc}")
