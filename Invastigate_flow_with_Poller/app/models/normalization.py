from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────

class ErrorType(str, Enum):
    """Category of failure detected in logs."""

    NO_ERROR = "NO_ERROR"
    AI_AGENT = "AI_AGENT"
    INFRA = "INFRA"
    NETWORK = "NETWORK"
    UNKNOWN = "UNKNOWN"


class DataSource(str, Enum):
    """Which observability backend was used to fetch data."""

    LANGFUSE = "langfuse"
    PROMETHEUS = "prometheus"


# ── Request Model (from Frontend) ──────────────────────────────────────

class NormalizationRequest(BaseModel):
    """
    Request payload sent by the frontend.

    Routing logic:
      - If trace_id is provided → fetch from Langfuse
      - If trace_id is absent   → fetch metrics from Prometheus using timestamp
    """

    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp of the incident from the frontend",
        examples=["2025-01-15T10:32:00Z"],
    )
    trace_id: Optional[str] = Field(
        None,
        description="Distributed trace ID. If provided, Langfuse is queried. Otherwise Prometheus is used.",
        examples=["trace-abc-123-def-456"],
    )
    agent_name: str = Field(
        ...,
        description="Name of the AI agent that encountered the failure",
        examples=["summarizer-v2", "retrieval-agent", "planner-v1"],
    )


# ── Response Models (PRD-aligned Pydantic classes) ─────────────────────

class Entities(BaseModel):
    """Extracted entity references from the logs."""

    agent_id: Optional[str] = Field(
        None,
        description="AI agent identifier extracted from logs",
    )
    service: Optional[str] = Field(
        None,
        description="Primary service or component involved in the failure",
    )
    trace_id: Optional[str] = Field(
        None,
        description="Distributed trace ID if found in the logs",
    )


class NormalizedIncident(BaseModel):
    """
    Structured incident representation — the core output of the
    Normalization Agent, matching the PRD schema exactly.

    This Pydantic model IS the contract; GPT-4o output is validated
    against it, so the prompt does not need to embed the JSON schema.
    """

    error_type: ErrorType = Field(
        ...,
        description="Category of failure: AI_AGENT, INFRA, NETWORK, or UNKNOWN",
    )
    error_summary: str = Field(
        ...,
        max_length=300,
        description="Factual 1-2 line summary of the error",
    )
    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp of the first occurrence in the logs",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score based on explicitness of the logs (0.0-1.0)",
    )
    entities: Entities = Field(
        default_factory=Entities,
        description="Extracted entity references (agent_id, service, trace_id)",
    )
    signals: list[str] = Field(
        default_factory=list,
        description="Atomic failure signals extracted from logs (e.g. dns_failure, timeout, connection_error)",
    )


class NormalizationResponse(BaseModel):
    """Full API response wrapping the normalized incident plus metadata."""

    incident: NormalizedIncident = Field(
        ...,
        description="The normalized incident produced by the agent",
    )
    data_source: DataSource = Field(
        ...,
        description="Which backend was queried: langfuse (via trace_id) or prometheus (via timestamp)",
    )
    raw_log_count: int = Field(
        ...,
        description="Number of raw log/metric entries that were processed",
    )
    processing_time_ms: float = Field(
        ...,
        description="Total LLM processing time in milliseconds",
    )
