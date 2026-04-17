from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import StreamingResponse

from app.agents.orchestrator import Orchestrator
from app.models.orchestrator import InvestigationRequest
from app.services.event_bus import get_event_bus
from app.core import logger

router = APIRouter(prefix="/api/v1/monitor", tags=["monitor"])

_orchestrator = Orchestrator()
_bus = get_event_bus()


@router.post(
    "/investigate",
    summary="Trigger a pipeline investigation and stream events via SSE",
)
async def monitor_investigate(
    request: InvestigationRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Queues a full pipeline investigation to run in the background and
    immediately returns the trace_id + stream URL.

    Connect to the returned `stream_url` with an EventSource to watch
    all 5 agent steps execute in real time.
    """
    trace_id = request.trace_id
    logger.info(
        "Monitor | Queuing background investigation | trace_id=%s agent=%s",
        trace_id, request.agent_name,
    )
    background_tasks.add_task(_run_background, request)
    return {
        "trace_id": trace_id,
        "stream_url": f"/api/v1/monitor/stream/{trace_id}",
    }


async def _run_background(request: InvestigationRequest) -> None:
    """Run the orchestrator in the background; errors are published to the bus."""
    try:
        await _orchestrator.investigate(request)
    except Exception as exc:
        logger.error("Monitor | Background pipeline error | trace_id=%s error=%s", request.trace_id, exc)
        await _bus.publish(request.trace_id, {"type": "error", "message": str(exc)})


@router.get(
    "/stream/{trace_id}",
    summary="SSE stream — real-time pipeline events for a trace_id",
)
async def stream_trace(trace_id: str) -> StreamingResponse:
    """
    Server-Sent Events endpoint.  Connect with an EventSource to receive
    real-time events as the pipeline executes:

      - pipeline_started
      - step_started (with agent input)
      - step_completed (with agent input + output, data_sources, logs_count, confidence)
      - step_failed (with error)
      - pipeline_completed
      - keepalive (every 20 s to keep the connection alive)
    """
    return StreamingResponse(
        _sse_generator(trace_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _sse_generator(trace_id: str):
    sub_id, queue = _bus.subscribe(trace_id)
    logger.info("Monitor | SSE connected | trace_id=%s sub=%s", trace_id, sub_id[:8])

    try:
        # Acknowledge the connection
        yield f"data: {json.dumps({'type': 'connected', 'trace_id': trace_id})}\n\n"

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20.0)
            except asyncio.TimeoutError:
                # Send keepalive so the browser doesn't close the connection
                yield f"event: keepalive\ndata: {{}}\n\n"
                continue

            yield f"data: {json.dumps(event, default=str)}\n\n"

            # Terminal events — pipeline is done, close the stream
            if event.get("type") in ("pipeline_completed", "error"):
                break

    except asyncio.CancelledError:
        pass
    finally:
        _bus.unsubscribe(trace_id, sub_id)
        logger.info("Monitor | SSE disconnected | trace_id=%s sub=%s", trace_id, sub_id[:8])
