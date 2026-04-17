from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any


class EventBus:
    """
    In-process pub/sub for real-time pipeline events.

    The Orchestrator publishes events during pipeline execution.
    SSE endpoints subscribe per trace_id and stream events to the browser.

    Wildcard subscribers (trace_id="*") receive ALL events — useful for
    a global dashboard feed.

    Replay buffer: all events are buffered per trace_id so that a client
    connecting AFTER the pipeline has already started still receives every
    event (e.g. logs_fetched emitted before the SSE connection was open).
    The buffer is cleared 120 s after the pipeline completes.
    """

    def __init__(self) -> None:
        # trace_id -> {subscriber_id -> asyncio.Queue}
        self._subs: dict[str, dict[str, asyncio.Queue]] = defaultdict(dict)
        # replay buffer: trace_id -> ordered list of past events
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # ── Subscribe / Unsubscribe ────────────────────────────────────────

    def subscribe(self, trace_id: str) -> tuple[str, asyncio.Queue]:
        """
        Register a new subscriber for a trace_id.
        Returns (sub_id, queue) — caller reads events from the queue.

        All buffered events for this trace_id are replayed immediately
        into the new queue so the client catches up on any events that
        were published before the SSE connection was established.
        """
        sub_id = str(uuid.uuid4())
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[trace_id][sub_id] = q

        # Replay history to the new subscriber
        for event in self._history.get(trace_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

        return sub_id, q

    def unsubscribe(self, trace_id: str, sub_id: str) -> None:
        """Remove a subscriber. Cleans up empty trace_id buckets."""
        self._subs[trace_id].pop(sub_id, None)
        if not self._subs[trace_id]:
            self._subs.pop(trace_id, None)

    # ── Publish ────────────────────────────────────────────────────────

    async def publish(self, trace_id: str, event: dict[str, Any]) -> None:
        """
        Publish an event to all subscribers of trace_id AND wildcard subscribers.
        Events are also appended to the replay buffer for late-connecting clients.
        Events are dropped (not blocked) if a subscriber queue is full.
        """
        # Buffer for replay (skip wildcard channel itself)
        if trace_id and trace_id != "*":
            self._history[trace_id].append(event)
            # Schedule buffer cleanup 120 s after the pipeline finishes
            if event.get("type") in ("pipeline_completed", "error"):
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_later(120, self._clear_history, trace_id)
                except RuntimeError:
                    pass

        targets = list(self._subs.get(trace_id, {}).values())
        targets += list(self._subs.get("*", {}).values())

        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Slow consumer — drop rather than block the pipeline

    # ── Internal ──────────────────────────────────────────────────────

    def _clear_history(self, trace_id: str) -> None:
        """Remove the replay buffer for a completed trace."""
        self._history.pop(trace_id, None)


# ── Singleton ──────────────────────────────────────────────────────────

_bus = EventBus()


def get_event_bus() -> EventBus:
    """Return the global EventBus singleton."""
    return _bus
