"""
Configuration class for the AIops SDK, reading settings from environment variables or kwargs.
Exposes server_url, app_name, api_key, enabled flag, and flush_interval_seconds as properties,
and derives the ingest, batch, and authentication header values used by the AIopsClient.
Defaults to localhost:7000 and can be overridden via AIOPS_SERVER_URL, AIOPS_APP_NAME, AIOPS_API_KEY.

AIops SDKの設定クラス。環境変数またはキーワード引数から設定を読み込む。
server_url、app_name、api_key、enabledフラグ、flush_interval_secondsをプロパティとして公開し、
AIopsClientが使用するインジェストURL、バッチURL、認証ヘッダーを導出する。
デフォルトはlocalhost:7000で、環境変数AIOPS_SERVER_URL等で上書き可能。
"""
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
