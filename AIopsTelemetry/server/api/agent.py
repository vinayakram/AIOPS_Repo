"""
Server-Sent Events endpoint that streams live ModifierAgent codebase-instrumentation
progress to the AIops dashboard. Accepts a target project path and optional
configuration, then delegates to ModifierAgent which injects telemetry hooks.
Each yielded event is a JSON-encoded status dict with stage, message, and payload.

ModifierAgentの実行進捗をServer-Sent Eventsでダッシュボードにリアルタイム配信するAPIエンドポイント。
対象プロジェクトのパスと設定を受け取り、ModifierAgentにテレメトリ注入処理を委譲する。
各イベントはステージ・メッセージ・ペイロードを含むJSON形式でストリーム出力される。
実行中の状態変化をリアルタイムに可視化するためのSSEプロトコルを採用している。
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
