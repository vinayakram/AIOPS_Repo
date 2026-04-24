from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    PUBMED_API_KEY: Optional[str] = None
    PUBMED_EMAIL: str = "user@example.com"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5-nano"
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
    CPU_THRESHOLD_PERCENT: int = 95
    MEMORY_THRESHOLD_PERCENT: int = 95

    class Config:
        env_file = ".env"


settings = Settings()
