from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 7000
    DATABASE_URL: str = "sqlite:///./aiops.db"
    API_KEY: Optional[str] = None           # If set, require X-AIops-Key header on ingest

    # Issue detection thresholds
    HIGH_LATENCY_MULTIPLIER: float = 3.0   # X * p95 triggers high_latency issue
    MIN_TRACES_FOR_LATENCY_BASELINE: int = 10

    # Escalation engine
    ESCALATION_INTERVAL_SECONDS: int = 30
    WEBHOOK_TIMEOUT_SECONDS: float = 10.0
    WEBHOOK_MAX_RETRIES: int = 3

    # Ingest
    MAX_INGEST_BATCH_SIZE: int = 500

    # Codebase modifier agent (OpenAI)
    OPENAI_API_KEY: Optional[str] = None

    # External RCA microservice (Invastigate_flow_with_Poller)
    RCA_SERVICE_URL: str = "http://localhost:8000"

    # External remediation service (AIOPS)
    AIOPS_REMEDIATION_URL: str = "http://localhost:8005"

    # Langfuse — issue reporting
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # NFR thresholds (override via env if needed)
    NFR_RESPONSE_TIME_TARGET_MS: float = 5000.0   # baseline for rules 7/7a/19/23
    NFR_CHECK_WINDOW_MINUTES: int = 10             # rolling window for rate checks

    class Config:
        env_file = ".env"
        env_prefix = "AIOPS_"
        extra = "ignore"   # ignore non-AIOPS_ env vars (e.g. ANTHROPIC_API_KEY)


settings = Settings()
