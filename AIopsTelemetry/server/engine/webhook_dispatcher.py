"""
Asynchronous webhook dispatcher that sends HTTP POST (or configurable method)
requests with JSON payloads to configured webhook URLs. Implements configurable
retry logic with exponential backoff (1s, 2s, 4s) and returns a success flag
with a detail string for logging by the escalation engine.

JSON ペイロードを設定済みWebhook URLにHTTP POST（またはメソッド指定）で送信する
非同期Webookディスパッチャモジュール。指数バックオフ（1秒・2秒・4秒）による
設定可能なリトライロジックを実装し、エスカレーションエンジンのログ記録用に
成功フラグと詳細文字列のタプルを返す設計となっている。
"""
import json
import asyncio
import httpx
from typing import Optional
from server.config import settings


async def fire_webhook(
    url: str,
    payload: dict,
    method: str = "POST",
    headers: Optional[dict] = None,
    retries: int = None,
) -> tuple[bool, str]:
    """Fire a webhook. Returns (success, detail)."""
    max_retries = retries if retries is not None else settings.WEBHOOK_MAX_RETRIES
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=settings.WEBHOOK_TIMEOUT_SECONDS) as client:
                resp = await client.request(method, url, json=payload, headers=hdrs)
                if resp.status_code < 400:
                    return True, f"HTTP {resp.status_code}"
                detail = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except httpx.TimeoutException:
            detail = f"Timeout after {settings.WEBHOOK_TIMEOUT_SECONDS}s"
        except Exception as e:
            detail = str(e)

        if attempt < max_retries:
            await asyncio.sleep(2 ** attempt)  # exponential backoff: 1s, 2s, 4s

    return False, detail
