from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Langfuse
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    # Prometheus
    prometheus_url: str = "http://localhost:9090"
    prometheus_query_window: str = "5m"
    prometheus_buffer_seconds: int = 300  # buffer added before trace_start and after trace_end

    # App
    app_env: str = "development"
    log_level: str = "INFO"
    app_name: str = "Multi-Agent Observability System"
    app_version: str = "0.1.0"

    # Database
    db_path: str = "investigations.db"

    # AIOps Poller
    aiops_server_url: str = "http://localhost:9090"
    aiops_poll_endpoint: str = "/api/v1/incidents"
    aiops_poll_interval_seconds: int = 20
    aiops_poll_enabled: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
