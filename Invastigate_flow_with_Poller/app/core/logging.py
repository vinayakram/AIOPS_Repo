"""
Structured logging configuration for the observability system backend.
Creates a named logger with a timestamped format and configures the log level from application settings.
Exports a module-level logger instance for use throughout the codebase.
A single StreamHandler to stdout is registered to avoid duplicate log entries on repeated imports.

オブザービリティシステムバックエンドの構造化ログ設定。
タイムスタンプ付きフォーマットで名前付きロガーを作成し、アプリケーション設定からログレベルを設定する。
コードベース全体で使用するモジュールレベルのロガーインスタンスをエクスポートする。
重複インポート時のログエントリ重複を避けるため、stdoutへの単一のStreamHandlerを登録する。
"""
import logging
import sys

from app.core.config import get_settings


def setup_logging() -> logging.Logger:
    """Configure structured logging for the application."""
    settings = get_settings()

    logger = logging.getLogger("observability")
    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = setup_logging()
