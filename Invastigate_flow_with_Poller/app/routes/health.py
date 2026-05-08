"""
FastAPI route handler for the health check endpoint at GET /health.
Returns the application name and version from settings to confirm the service is running.
Used by load balancers, container orchestration platforms, and monitoring tools for liveness checks.
No authentication is required; this endpoint is always publicly accessible.

GET /healthのヘルスチェックエンドポイントのFastAPIルートハンドラ。
サービスが稼働中であることを確認するためにアプリケーション名とバージョンを設定から返す。
稼働確認のためにロードバランサー、コンテナオーケストレーションプラットフォーム、監視ツールが使用する。
認証は不要で、このエンドポイントは常に公開アクセス可能である。
"""
from fastapi import APIRouter

from app.core.config import get_settings
from app.models.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        service=settings.app_name,
    )
