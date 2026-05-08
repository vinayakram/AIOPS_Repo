from __future__ import annotations

import asyncio
import hashlib
import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a professional Japanese technical translator specialising in IT operations, SRE, and cloud infrastructure.

Translate the provided English text into natural, professional Japanese suitable for an operations dashboard.

Rules:
- Produce native Japanese — not a word-for-word literal translation
- Preserve technical terms in English where they are industry-standard (e.g. CPU, memory, HTTP, DNS, timeout, trace, span, LLM, API, SLO, p95, latency)
- Service and component names stay in English (e.g. sample-agent, triage-agent, Prometheus, Langfuse)
- Enum values (error categories, severities) stay in English
- Return ONLY the translated text — no explanation, no quotes, no preamble
"""


@lru_cache(maxsize=1)
def _get_client() -> Any:
    try:
        from openai import OpenAI  # type: ignore
        import os
        api_key = os.environ.get("AIOPS_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        return OpenAI(api_key=api_key)
    except Exception:
        return None


_cache: dict[str, str] = {}


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def translate_to_japanese(text: str | None, *, use_cache_only: bool = False) -> str | None:
    """Translate English text to Japanese using OpenAI.

    Returns None if translation is unavailable. Results are cached in-process.
    Falls back gracefully — never raises.
    """
    if not text or not text.strip():
        return text

    key = _cache_key(text)
    if key in _cache:
        return _cache[key]

    if use_cache_only:
        return None

    client = _get_client()
    if client is None:
        logger.debug("LLM translator: no OpenAI client available, skipping translation")
        return None

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            max_tokens=500,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        result = (response.choices[0].message.content or "").strip()
        if result:
            _cache[key] = result
            return result
    except Exception as exc:
        logger.warning("LLM translation failed: %s", exc)

    return None


async def translate_to_japanese_async(text: str | None, *, use_cache_only: bool = False) -> str | None:
    """Async wrapper — runs translation in a thread pool so FastAPI stays non-blocking."""
    if not text:
        return text
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: translate_to_japanese(text, use_cache_only=use_cache_only))


def translate_batch(texts: list[str | None]) -> list[str | None]:
    """Translate a list of strings, preserving None entries."""
    return [translate_to_japanese(t) for t in texts]
