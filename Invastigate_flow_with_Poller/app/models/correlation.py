from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalization import NormalizedIncident


# ── Enums ──────────────────────────────────────────────────────────────

class ComponentRole(str, Enum):
    """Role of a peer component in the failure chain."""

    ROOT_UPSTREAM_FAILURE = "root_upstream_failure"
    CONTRIBUTING_FACTOR = "contributing_factor"


class AnalysisDomain(str, Enum):
    """Where further error analysis should be performed."""

    AGENT = "Agent"
    INFRA_LOGS = "InfraLogs"
    UNKNOWN = "Unknown"


# ── Request Model ──────────────────────────────────────────────────────

class CorrelationRequest(BaseModel):
    """
    Request payload for the correlation endpoint.

    Takes the normalization output and uses it to fetch logs from
    the appropriate backends for cross-system correlation.

    Routing:
      - trace_id present  → Langfuse + Prometheus
      - trace_id absent   → Prometheus only
    """

    incident: NormalizedIncident = Field(
        ...,
        description="Normalized incident output from the Normalization Agent",
    )
    trace_id: Optional[str] = Field(
        None,
        description="Distributed trace ID. If present, Langfuse logs are included in correlation.",
    )
    agent_name: str = Field(
        ...,
        description="Name of the AI agent under investigation",
        examples=["summarizer-v2", "retrieval-agent"],
    )


# ── Response Models (PRD-aligned) ─────────────────────────────────────

class PeerComponent(BaseModel):
    """A component identified as part of the failure chain."""

    component: str = Field(
        ...,
        description="Name of the service or component",
    )
    role: ComponentRole = Field(
        ...,
        description="Role in the failure: root_upstream_failure or contributing_factor",
    )
    evidence: str = Field(
        ...,
        description="Evidence from logs supporting this classification",
    )


class TimelineEvent(BaseModel):
    """A single event in the incident timeline."""

    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp of the event",
    )
    event: str = Field(
        ...,
        description="Description of what happened at this point",
    )
    service: str = Field(
        ...,
        description="Service or component where the event occurred",
    )


class RootCauseCandidate(BaseModel):
    """The most likely root cause identified by the correlation engine."""

    component: str = Field(
        ...,
        description="Service or component identified as the root cause",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this root cause hypothesis (0.0-1.0)",
    )
    reason: str = Field(
        ...,
        description="Explanation of why this is the root cause candidate",
    )


class CorrelationResult(BaseModel):
    """
    Core correlation output — the causal failure graph produced by the
    Correlation Agent. This Pydantic model IS the contract; GPT-4o
    output is validated against it.
    """

    correlation_chain: list[str] = Field(
        ...,
        description="Failure propagation chain (e.g. ['DNS failure → proxy timeout → gateway failure'])",
    )
    peer_components: list[PeerComponent] = Field(
        default_factory=list,
        description="Components involved in the failure with roles and evidence",
    )
    timeline: list[TimelineEvent] = Field(
        default_factory=list,
        description="Chronological timeline of failure events",
    )
    root_cause_candidate: RootCauseCandidate = Field(
        ...,
        description="The most likely root cause hypothesis",
    )
    analysis_target: AnalysisDomain = Field(
        ...,
        description="Where to perform further error analysis: Agent (AI agent logs/traces), InfraLogs (infrastructure metrics/logs), or Unknown",
    )


class CorrelationResponse(BaseModel):
    """Full API response wrapping the correlation result plus metadata."""

    correlation: CorrelationResult = Field(
        ...,
        description="The correlation analysis produced by the agent",
    )
    data_sources: list[str] = Field(
        ...,
        description="Which backends were queried (e.g. ['langfuse', 'prometheus'])",
    )
    total_logs_analyzed: int = Field(
        ...,
        description="Total number of log/metric entries analyzed across all sources",
    )
    processing_time_ms: float = Field(
        ...,
        description="Total LLM processing time in milliseconds",
    )
