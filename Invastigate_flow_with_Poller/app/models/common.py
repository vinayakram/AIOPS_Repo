"""
Common Pydantic response models shared across all API routes.
Defines HealthResponse for the health check endpoint and ErrorResponse for standardized error payloads.
These models ensure consistent response schemas for non-agent-specific API responses.
Used by FastAPI's response_model parameter to generate OpenAPI documentation automatically.

すべてのAPIルートで共有されるCommon Pydantic レスポンスモデル。
ヘルスチェックエンドポイント用のHealthResponseと標準化されたエラーペイロード用のErrorResponseを定義する。
これらのモデルはエージェント固有でないAPIレスポンスの一貫したレスポンススキーマを保証する。
FastAPIのresponse_modelパラメータによるOpenAPIドキュメントの自動生成に使用される。
"""
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    service: str


class ErrorResponse(BaseModel):
    error: str = Field(..., description="Error type")
    detail: str = Field(..., description="Human-readable error message")
