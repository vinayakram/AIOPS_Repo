"""
Convenience decorators for manual instrumentation when auto-injection isn't enough.

Usage:
    from aiops_sdk import trace_span

    @trace_span("my_step")
    def my_function(x):
        return x * 2
"""
import functools
import logging
from typing import Optional

from aiops_sdk.client import AIopsClient
from aiops_sdk.context import (
    SpanContext, push_span, pop_span, current_span,
    new_span_id, current_trace_id,
)

logger = logging.getLogger("aiops.decorators")


def trace_span(name: str = None, span_type: str = "chain"):
    """Decorator to wrap a function as an AIops span."""
    def decorator(func):
        span_name = name or func.__name__

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            client = AIopsClient.get()
            tid = current_trace_id()
            if not tid or not client.config.enabled:
                return func(*args, **kwargs)
            parent = current_span()
            span = SpanContext(
                span_id=new_span_id(),
                trace_id=tid,
                parent_span_id=parent.span_id if parent else None,
                name=span_name,
                span_type=span_type,
            )
            push_span(span)
            try:
                result = func(*args, **kwargs)
                span.finish(output_preview=str(result)[:300])
                return result
            except Exception as e:
                span.finish(error=str(e))
                raise
            finally:
                client.add_span(tid, span)
                pop_span()

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            client = AIopsClient.get()
            tid = current_trace_id()
            if not tid or not client.config.enabled:
                return await func(*args, **kwargs)
            parent = current_span()
            span = SpanContext(
                span_id=new_span_id(),
                trace_id=tid,
                parent_span_id=parent.span_id if parent else None,
                name=span_name,
                span_type=span_type,
            )
            push_span(span)
            try:
                result = await func(*args, **kwargs)
                span.finish(output_preview=str(result)[:300])
                return result
            except Exception as e:
                span.finish(error=str(e))
                raise
            finally:
                client.add_span(tid, span)
                pop_span()

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
