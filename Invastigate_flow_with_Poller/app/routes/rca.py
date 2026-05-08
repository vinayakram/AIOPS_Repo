"""
FastAPI route handler for the standalone RCA Agent endpoint at POST /api/v1/rca.
Accepts an RCARequest containing the error analysis output and rca_target routing signal.
Delegates to the RCAAgent which fetches fresh logs and returns a detailed root cause analysis.
HTTP 500 is returned for LLM errors or validation failures; 422 for request schema violations.

POST /api/v1/rcaのスタンドアロンRCAエージェントエンドポイントのFastAPIルートハンドラ。
エラー分析出力とrca_targetルーティングシグナルを含むRCARequestを受け付ける。
最新ログを取得して詳細な根本原因分析を返すRCAAgentに処理を委譲する。
LLMエラーまたは検証失敗にはHTTP 500を返し、リクエストスキーマ違反には422を返す。
"""
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
