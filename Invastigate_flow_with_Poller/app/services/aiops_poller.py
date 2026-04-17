from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import logger
from app.models.orchestrator import InvestigationRequest
from app.services.trace_store import TraceStore


class AIOpsPoller:
    """
    Background poller that fetches incident records from the AIOps
    engine at a configurable interval and feeds each one to the
    orchestrator pipeline.

    Deduplication (two layers):
      1. In-memory set (_processed_trace_ids) — fast first check
      2. SQLite DB check (trace_store.trace_exists) — survives restarts

    On startup, the poller loads all existing trace_ids from SQLite
    into the in-memory set so it never reprocesses after a restart.

    Expected response from AIOps server:
    {
        "incidents": [
            {"trace_id": "...", "timestamp": "...", "agent_name": "..."},
            ...
        ]
    }
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.aiops_server_url.rstrip("/")
        self._endpoint = settings.aiops_poll_endpoint
        self._interval = settings.aiops_poll_interval_seconds
        self._enabled = settings.aiops_poll_enabled

        self._orchestrator = None  # lazy init to avoid circular import
        self._trace_store = TraceStore()
        self._processed_trace_ids: set[str] = set()
        self._task: asyncio.Task | None = None
        self._running = False

        # Stats
        self._total_polled = 0
        self._total_processed = 0
        self._total_skipped = 0
        self._total_errors = 0
        self._last_poll_time: str | None = None

    def _get_orchestrator(self):
        """Lazy import to avoid circular dependency at module load."""
        if self._orchestrator is None:
            from app.agents.orchestrator import Orchestrator
            self._orchestrator = Orchestrator()
        return self._orchestrator

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background polling loop."""
        if not self._enabled:
            logger.info("AIOps Poller is disabled (AIOPS_POLL_ENABLED=false)")
            return

        if self._running:
            logger.warning("AIOps Poller is already running")
            return

        # Pre-load all existing trace_ids from SQLite into memory
        # so we never reprocess after a server restart
        try:
            existing = await self._trace_store.load_all_trace_ids()
            self._processed_trace_ids = existing
            logger.info(
                "AIOps Poller | loaded %d existing trace_ids from DB",
                len(existing),
            )
        except Exception as exc:
            logger.warning("AIOps Poller | failed to load existing trace_ids: %s", exc)

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "AIOps Poller started | url=%s%s interval=%ds known_traces=%d",
            self._base_url, self._endpoint, self._interval,
            len(self._processed_trace_ids),
        )

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("AIOps Poller stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "poll_interval_seconds": self._interval,
            "aiops_server": f"{self._base_url}{self._endpoint}",
            "total_polled": self._total_polled,
            "total_processed": self._total_processed,
            "total_skipped": self._total_skipped,
            "total_errors": self._total_errors,
            "known_trace_ids": len(self._processed_trace_ids),
            "last_poll_time": self._last_poll_time,
        }

    # ── Polling loop ──────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Main loop — polls every N seconds until stopped."""
        logger.info("AIOps Poller loop started")

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("AIOps Poller unexpected error: %s", exc)
                self._total_errors += 1

            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break

        logger.info("AIOps Poller loop exited")

    async def _poll_once(self) -> None:
        """Single poll cycle — fetch incidents and process new ones."""
        from datetime import datetime, timezone

        self._last_poll_time = datetime.now(timezone.utc).isoformat()
        self._total_polled += 1

        logger.info("AIOps Poller | poll #%d — fetching incidents", self._total_polled)

        incidents = await self._fetch_incidents()

        if not incidents:
            logger.info("AIOps Poller | no incidents returned")
            return

        logger.info("AIOps Poller | fetched %d incidents", len(incidents))

        for incident in incidents:
            trace_id = incident.get("trace_id")

            if not trace_id:
                logger.warning("AIOps Poller | skipping incident with no trace_id: %s", incident)
                self._total_skipped += 1
                continue

            # ── Layer 1: In-memory check (fast) ───────────────────
            if trace_id in self._processed_trace_ids:
                logger.debug("AIOps Poller | skip (in-memory) trace_id=%s", trace_id)
                self._total_skipped += 1
                continue

            # ── Layer 2: DB check (survives restarts) ─────────────
            try:
                if await self._trace_store.trace_exists(trace_id):
                    logger.info(
                        "AIOps Poller | skip (exists in DB) trace_id=%s", trace_id,
                    )
                    # Backfill the in-memory set so we don't hit DB again
                    self._processed_trace_ids.add(trace_id)
                    self._total_skipped += 1
                    continue
            except Exception as exc:
                logger.warning(
                    "AIOps Poller | DB check failed for trace_id=%s: %s — proceeding cautiously",
                    trace_id, exc,
                )

            # ── Not seen before — process it ──────────────────────
            timestamp = incident.get("timestamp", "")
            agent_name = incident.get("agent_name", "unknown")

            if not timestamp:
                logger.warning("AIOps Poller | skipping trace_id=%s — missing timestamp", trace_id)
                self._total_skipped += 1
                continue

            logger.info(
                "AIOps Poller | NEW incident — processing trace_id=%s agent=%s ts=%s",
                trace_id, agent_name, timestamp,
            )

            try:
                request = InvestigationRequest(
                    timestamp=timestamp,
                    trace_id=trace_id,
                    agent_name=agent_name,
                )
                result = await self._get_orchestrator().investigate(request)

                # Mark processed in memory (DB is updated by orchestrator)
                self._processed_trace_ids.add(trace_id)
                self._total_processed += 1

                logger.info(
                    "AIOps Poller | completed trace_id=%s | pipeline=%s steps=%d",
                    trace_id, result.completed, len(result.pipeline_steps),
                )

            except Exception as exc:
                logger.error(
                    "AIOps Poller | failed to process trace_id=%s: %s",
                    trace_id, exc,
                )
                self._total_errors += 1
                # Still mark as processed — failed traces are stored in DB
                # with status=failed. Don't retry in a loop.
                self._processed_trace_ids.add(trace_id)

    # ── HTTP fetch ────────────────────────────────────────────────────

    async def _fetch_incidents(self) -> list[dict[str, Any]]:
        """
        GET incidents from the AIOps engine.

        Supports:
          {"incidents": [...]}   — object wrapper
          [...]                  — flat list
        """
        url = f"{self._base_url}{self._endpoint}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()

            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("incidents", [])

            logger.warning("AIOps Poller | unexpected response format: %s", type(data))
            return []

        except httpx.TimeoutException:
            logger.warning("AIOps Poller | timeout fetching %s", url)
            self._total_errors += 1
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "AIOps Poller | HTTP %d from %s", exc.response.status_code, url,
            )
            self._total_errors += 1
            return []
        except Exception as exc:
            logger.warning("AIOps Poller | failed to fetch: %s", exc)
            self._total_errors += 1
            return []
