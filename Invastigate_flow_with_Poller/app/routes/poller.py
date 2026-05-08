"""
FastAPI route handlers for AIOps poller admin operations at /api/v1/poller/*.
Provides GET /status to inspect poller state and statistics, and POST /start and /stop lifecycle controls.
Exposes a singleton AIOpsPoller instance shared with the application lifespan via get_poller().
Poll statistics include total incidents polled, processed, skipped, errored, and last poll timestamp.

/api/v1/poller/*のAIOpsポーラー管理操作のFastAPIルートハンドラ。
ポーラー状態と統計を確認するGET /statusと、POST /startおよび/stopライフサイクル制御を提供する。
get_poller()を介してアプリケーションライフスパンと共有されるシングルトンAIOpsPollerインスタンスを公開する。
ポーリング統計にはポーリング済み、処理済み、スキップ済み、エラー発生インシデントの合計と最終ポーリング時刻が含まれる。
"""
from fastapi import APIRouter

from app.services.aiops_poller import AIOpsPoller
from app.core.logging import logger

router = APIRouter(prefix="/api/v1/poller", tags=["poller"])

# Singleton poller instance — shared across routes and lifespan
_poller = AIOpsPoller()


def get_poller() -> AIOpsPoller:
    """Get the singleton poller instance (used by main.py lifespan)."""
    return _poller


@router.get(
    "/status",
    summary="Get poller status and stats",
)
async def poller_status():
    """
    Returns the current poller state including:
    - Whether it's running
    - Poll interval
    - AIOps server URL
    - Total incidents polled, processed, skipped, errored
    - Number of known (already-processed) trace_ids
    - Last poll timestamp
    """
    return _poller.stats


@router.post(
    "/start",
    summary="Start the background poller",
)
async def start_poller():
    """Start polling the AIOps engine for new incidents."""
    if _poller.is_running:
        return {"status": "already_running", "message": "Poller is already running"}

    await _poller.start()
    logger.info("Poller started via API")
    return {"status": "started", "message": "Poller is now running"}


@router.post(
    "/stop",
    summary="Stop the background poller",
)
async def stop_poller():
    """Stop the background poller."""
    if not _poller.is_running:
        return {"status": "already_stopped", "message": "Poller is not running"}

    await _poller.stop()
    logger.info("Poller stopped via API")
    return {"status": "stopped", "message": "Poller has been stopped"}
