from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    PUBMED_API_KEY: Optional[str] = None
    PUBMED_EMAIL: str = "user@example.com"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5-nano"
    LOW_RATE_LIMIT_OPENAI_API_KEY: str = ""
    LOW_RATE_LIMIT_OPENAI_ENDPOINT: str = "https://synapt-development-instance.openai.azure.com/"
    LOW_RATE_LIMIT_OPENAI_API_VERSION: str = "2024-02-15-preview"
    LOW_RATE_LIMIT_OPENAI_MODEL: str = "gpt-4o-mini"
    LOW_RATE_LIMIT_OPENAI_DEPLOYMENT: str = "low-ratelimit-gpt-4o-mini"
    LOW_RATE_LIMIT_REQUESTS_PER_MINUTE: int = 10
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-opus-4-6"
    SECRET_KEY: str = "change-this-secret-key-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    PAGERANK_ALPHA: float = 0.4  # Weight for PageRank vs similarity score
    SPECIAL_CHARACTER_DEMO_ERROR: bool = True
    SPECIAL_CHARACTER_DEMO_CHARS: str = "@#$%^&*"
    # Langfuse observability (optional)
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    # AIops Telemetry server (optional)
    AIOPS_SERVER_URL: str = "http://localhost:7000"
    AIOPS_ENABLED: bool = True
    POD_CPU_THRESHOLD_ENABLED: bool = True
    POD_CPU_THRESHOLD_PERCENT: float = 90.0
    POD_MEMORY_THRESHOLD_ENABLED: bool = True
    POD_MEMORY_THRESHOLD_PERCENT: float = 90.0
    POD_THRESHOLD_TELEMETRY_MIN_INTERVAL_SECONDS: float = 1.0

    class Config:
        env_file = ".env"


settings = Settings()
