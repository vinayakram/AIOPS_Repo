from __future__ import annotations

import json
from typing import Any

from app.core.logging import logger
from app.core.database import get_db

# Agents whose I/O columns exist in trace_results
_AGENTS = ("normalization", "correlation", "error_analysis", "rca", "recommendation")


class TraceStore:
    """
    Persistence layer for pipeline traces.

    Single table `trace_results` keyed by `trace_id`.
    Each agent has two columns: `{agent}_input` and `{agent}_output`.
    """

    # ── Write ─────────────────────────────────────────────────────────

    async def create_trace(
        self,
        trace_id: str,
        agent_name: str,
        timestamp: str,
    ) -> None:
        """Insert a new row when the pipeline starts."""
        db = await get_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO trace_results (trace_id, agent_name, timestamp, status)
            VALUES (?, ?, ?, 'running')
            """,
            (trace_id, agent_name, timestamp),
        )
        await db.commit()

    async def save_agent_io(
        self,
        trace_id: str,
        agent: str,
        input_data: Any | None = None,
        output_data: Any | None = None,
    ) -> None:
        """
        Save one agent's input and output for a trace.

        `agent` must be one of: normalization, correlation,
        error_analysis, rca, recommendation.
        """
        if agent not in _AGENTS:
            logger.warning("Unknown agent name for storage: %s", agent)
            return

        input_json = json.dumps(input_data) if input_data is not None else None
        output_json = json.dumps(output_data) if output_data is not None else None

        db = await get_db()
        await db.execute(
            f"""
            UPDATE trace_results
            SET {agent}_input  = ?,
                {agent}_output = ?,
                updated_at     = datetime('now')
            WHERE trace_id = ?
            """,
            (input_json, output_json, trace_id),
        )
        await db.commit()

    async def save_fetched_logs(
        self,
        trace_id: str,
        agent: str,
        source: str,
        entries: list[dict],
    ) -> None:
        """
        Persist raw fetched log entries for a given agent/source into the
        `fetched_logs` JSON blob so they survive page refreshes.

        Structure: { "normalization": { "langfuse": [...], "prometheus": [...] }, ... }
        """
        if not trace_id or not entries:
            return
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT fetched_logs FROM trace_results WHERE trace_id = ?", (trace_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return  # Trace row not created yet — skip

            current: dict = {}
            if row["fetched_logs"]:
                try:
                    current = json.loads(row["fetched_logs"])
                except (json.JSONDecodeError, TypeError):
                    current = {}

            current.setdefault(agent, {}).setdefault(source, [])
            current[agent][source].extend(entries)

            await db.execute(
                "UPDATE trace_results SET fetched_logs = ?, updated_at = datetime('now') WHERE trace_id = ?",
                (json.dumps(current), trace_id),
            )
            await db.commit()
        except Exception as exc:
            logger.warning("save_fetched_logs failed (%s/%s/%s): %s", trace_id, agent, source, exc)

    async def complete_trace(self, trace_id: str, status: str) -> None:
        """Mark the trace as 'completed' or 'failed'."""
        db = await get_db()
        await db.execute(
            "UPDATE trace_results SET status = ?, updated_at = datetime('now') WHERE trace_id = ?",
            (status, trace_id),
        )
        await db.commit()

    # ── Read ──────────────────────────────────────────────────────────

    async def trace_exists(self, trace_id: str) -> bool:
        """Check if a trace_id already exists in the database."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT 1 FROM trace_results WHERE trace_id = ? LIMIT 1",
            (trace_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def load_all_trace_ids(self) -> set[str]:
        """Load all existing trace_ids from the database — used on startup to seed the dedup set."""
        db = await get_db()
        cursor = await db.execute("SELECT trace_id FROM trace_results")
        rows = await cursor.fetchall()
        return {row["trace_id"] for row in rows}

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """
        Return all stored I/O for a trace_id.

        JSON columns are parsed back into dicts automatically.
        Returns None if not found.
        """
        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM trace_results WHERE trace_id = ?", (trace_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        result = dict(row)

        # Parse every JSON column
        for agent in _AGENTS:
            for suffix in ("_input", "_output"):
                col = f"{agent}{suffix}"
                if result.get(col):
                    try:
                        result[col] = json.loads(result[col])
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Parse fetched_logs blob
        if result.get("fetched_logs"):
            try:
                result["fetched_logs"] = json.loads(result["fetched_logs"])
            except (json.JSONDecodeError, TypeError):
                result["fetched_logs"] = {}

        return result

    async def list_traces(
        self,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List traces (summary only — no agent IO)."""
        db = await get_db()
        cursor = await db.execute(
            """
            SELECT trace_id, agent_name, timestamp, status, created_at, updated_at
            FROM trace_results
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
