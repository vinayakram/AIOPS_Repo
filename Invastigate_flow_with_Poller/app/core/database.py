from __future__ import annotations

import aiosqlite

from app.core.config import get_settings
from app.core.logging import logger

_db: aiosqlite.Connection | None = None


async def init_db() -> None:
    """Initialize SQLite and create the trace_results table."""
    global _db
    settings = get_settings()
    db_path = settings.db_path

    logger.info("Initializing SQLite database at %s", db_path)
    _db = await aiosqlite.connect(db_path)
    _db.row_factory = aiosqlite.Row

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS trace_results (
            trace_id                TEXT PRIMARY KEY,
            agent_name              TEXT NOT NULL,
            timestamp               TEXT NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'running',
            created_at              TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT NOT NULL DEFAULT (datetime('now')),

            -- Each agent's input and output as JSON
            normalization_input     TEXT,
            normalization_output    TEXT,
            correlation_input       TEXT,
            correlation_output      TEXT,
            error_analysis_input    TEXT,
            error_analysis_output   TEXT,
            rca_input               TEXT,
            rca_output              TEXT,
            recommendation_input    TEXT,
            recommendation_output   TEXT,

            -- Raw fetched log entries per agent/source (for UI persistence across refreshes)
            fetched_logs            TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trace_results_agent
            ON trace_results(agent_name);
        CREATE INDEX IF NOT EXISTS idx_trace_results_created
            ON trace_results(created_at DESC);
    """)
    await _db.commit()

    # Migration: add fetched_logs column to existing databases that predate this column
    try:
        await _db.execute("ALTER TABLE trace_results ADD COLUMN fetched_logs TEXT")
        await _db.commit()
        logger.info("Migration: added fetched_logs column to trace_results")
    except Exception:
        pass  # Column already exists — normal on fresh or already-migrated DBs

    logger.info("SQLite database initialized — table: trace_results")


async def get_db() -> aiosqlite.Connection:
    """Return the database connection. Raises if not initialized."""
    if _db is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return _db


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        logger.info("SQLite connection closed")
