"""
ContextVar-based active trace/span tracking so spans can find their parent
without explicit parameter passing through LangGraph's call chain.
"""
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SpanContext:
    span_id: str
    trace_id: str
    parent_span_id: Optional[str]
    name: str
    span_type: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    status: str = "ok"
    input_preview: Optional[str] = None
    output_preview: Optional[str] = None
    error_message: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    model_name: Optional[str] = None
    duration_ms: Optional[float] = None

    def finish(self, output_preview: str = None, error: str = None):
        self.ended_at = datetime.utcnow()
        self.duration_ms = (self.ended_at - self.started_at).total_seconds() * 1000
        if output_preview:
            self.output_preview = output_preview[:500]
        if error:
            self.error_message = error
            self.status = "error"

    def to_dict(self) -> dict:
        return {
            "id": self.span_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "span_type": self.span_type,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "input_preview": self.input_preview,
            "output_preview": self.output_preview,
            "error_message": self.error_message,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "model_name": self.model_name,
        }


# Active span stack — list so nested spans work correctly
_active_spans: ContextVar[list[SpanContext]] = ContextVar("aiops_active_spans", default=[])


def push_span(span: SpanContext) -> None:
    current = list(_active_spans.get([]))
    current.append(span)
    _active_spans.set(current)


def pop_span() -> Optional[SpanContext]:
    current = list(_active_spans.get([]))
    if not current:
        return None
    span = current.pop()
    _active_spans.set(current)
    return span


def current_span() -> Optional[SpanContext]:
    stack = _active_spans.get([])
    return stack[-1] if stack else None


def current_trace_id() -> Optional[str]:
    span = current_span()
    return span.trace_id if span else None


def new_span_id() -> str:
    return str(uuid.uuid4())


def new_trace_id() -> str:
    return str(uuid.uuid4())
