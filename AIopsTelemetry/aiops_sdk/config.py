import os


class AIopsConfig:
    """Read configuration from environment variables or explicit init."""

    def __init__(
        self,
        server_url: str = None,
        app_name: str = None,
        api_key: str = None,
        enabled: bool = True,
        flush_interval_seconds: float = 5.0,
    ):
        self.server_url = (server_url or os.getenv("AIOPS_SERVER_URL", "http://localhost:7000")).rstrip("/")
        self.app_name = app_name or os.getenv("AIOPS_APP_NAME", "default")
        self.api_key = api_key or os.getenv("AIOPS_API_KEY")
        self.enabled = enabled
        self.flush_interval_seconds = flush_interval_seconds

    @property
    def ingest_url(self) -> str:
        return f"{self.server_url}/api/ingest/trace"

    @property
    def batch_url(self) -> str:
        return f"{self.server_url}/api/ingest/batch"

    @property
    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-AIops-Key"] = self.api_key
        return h
