"""
AIops SDK client — collects spans in memory and flushes to the server.
Thread-safe; works with both sync and async LangGraph code.
"""
import json
import logging
import threading
from datetime import datetime
from typing import Optional

import requests

from aiops_sdk.config import AIopsConfig
from aiops_sdk.context import SpanContext, new_trace_id

logger = logging.getLogger("aiops.client")


class TraceBuffer:
    """Holds spans for one trace until flush."""

    def __init__(self, trace_id: str, app_name: str, input_preview: str = None):
        self.trace_id = trace_id
        self.app_name = app_name
        self.started_at = datetime.utcnow()
        self.ended_at: Optional[datetime] = None
        self.status = "ok"
        self.input_preview = input_preview[:500] if input_preview else None
        self.output_preview: Optional[str] = None
        self.spans: list[SpanContext] = []
        self.logs: list[dict] = []

    def add_span(self, span: SpanContext):
        self.spans.append(span)

    def log(self, level: str, message: str, logger: str = None, metadata: dict = None):
        self.logs.append({
            "trace_id": self.trace_id,
            "level": level.upper(),
            "logger": logger,
            "message": message,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata,
        })

    def finish(self, output_preview: str = None, status: str = "ok"):
        self.ended_at = datetime.utcnow()
        self.status = status
        if output_preview:
            self.output_preview = output_preview[:500]

    def total_duration_ms(self) -> Optional[float]:
        if self.ended_at:
            return (self.ended_at - self.started_at).total_seconds() * 1000
        return None

    def to_payload(self) -> dict:
        return {
            "id": self.trace_id,
            "app_name": self.app_name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "total_duration_ms": self.total_duration_ms(),
            "input_preview": self.input_preview,
            "output_preview": self.output_preview,
            "spans": [s.to_dict() for s in self.spans],
            "logs": self.logs,
        }


class AIopsClient:
    """Singleton-like client — call AIopsClient.configure() once at startup."""

    _instance: Optional["AIopsClient"] = None

    def __init__(self, config: AIopsConfig):
        self.config = config
        self._lock = threading.Lock()
        self._buffers: dict[str, TraceBuffer] = {}

    # ── Class-level singleton helpers ─────────────────────────────────────────

    @classmethod
    def configure(cls, **kwargs) -> "AIopsClient":
        cls._instance = cls(AIopsConfig(**kwargs))
        return cls._instance

    @classmethod
    def get(cls) -> "AIopsClient":
        if cls._instance is None:
            cls._instance = cls(AIopsConfig())
        return cls._instance

    # ── Trace lifecycle ───────────────────────────────────────────────────────

    def start_trace(self, input_preview: str = None, trace_id: str = None) -> str:
        if not self.config.enabled:
            return trace_id or new_trace_id()
        tid = trace_id or new_trace_id()
        with self._lock:
            self._buffers[tid] = TraceBuffer(tid, self.config.app_name, input_preview)
        return tid

    def finish_trace(self, trace_id: str, output_preview: str = None, status: str = "ok"):
        if not self.config.enabled:
            return
        with self._lock:
            buf = self._buffers.get(trace_id)
        if not buf:
            return
        buf.finish(output_preview, status)
        self._flush(buf)
        with self._lock:
            self._buffers.pop(trace_id, None)

    def log(self, trace_id: str, level: str, message: str, logger: str = None, metadata: dict = None):
        """Append a log entry to the in-flight trace buffer."""
        if not self.config.enabled:
            return
        with self._lock:
            buf = self._buffers.get(trace_id)
        if buf:
            buf.log(level, message, logger, metadata)

    def add_span(self, trace_id: str, span: SpanContext):
        if not self.config.enabled:
            return
        with self._lock:
            buf = self._buffers.get(trace_id)
        if buf:
            buf.add_span(span)

    # ── Flush ─────────────────────────────────────────────────────────────────

    def _flush(self, buf: TraceBuffer):
        try:
            payload = buf.to_payload()
            resp = requests.post(
                self.config.ingest_url,
                json=payload,
                headers=self.config.headers,
                timeout=5,
            )
            if resp.status_code >= 400:
                logger.warning("AIops ingest failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.debug("AIops flush error (non-fatal): %s", e)
