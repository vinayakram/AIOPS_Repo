"""
SSE endpoint that streams ModifierAgent progress to the dashboard.
"""
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from server.engine.modifier_agent import ModifierAgent

router = APIRouter(prefix="/agent", tags=["agent"])


class InstrumentRequest(BaseModel):
    project_dir: str
    app_name: str
    aiops_server_url: str = "http://localhost:7000"


@router.post("/instrument")
async def instrument(payload: InstrumentRequest):
    """
    Stream codebase-modifier agent progress as Server-Sent Events.
    Each event is a JSON object with at minimum a "type" field.
    """
    async def event_stream():
        try:
            agent = ModifierAgent(
                payload.project_dir,
                payload.app_name,
                payload.aiops_server_url,
            )
            async for event in agent.run():
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
