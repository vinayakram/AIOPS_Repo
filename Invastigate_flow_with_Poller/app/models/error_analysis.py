from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.correlation import AnalysisDomain, CorrelationResult
from app.models.normalization import NormalizedIncident


# ── Enums ──────────────────────────────────────────────────────────────

class ErrorSeverity(str, Enum):
    """Severity classification of an individual error."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ErrorCategory(str, Enum):
    """Functional category of the error."""

    LLM_FAILURE = "llm_failure"
    PROMPT_ERROR = "prompt_error"
    TOOL_CALL_FAILURE = "tool_call_failure"
    RETRIEVAL_FAILURE = "retrieval_failure"
    PARSING_ERROR = "parsing_error"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_FAILURE = "auth_failure"
    CONNECTION_ERROR = "connection_error"
    DNS_FAILURE = "dns_failure"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    SERVICE_UNAVAILABLE = "service_unavailable"
    CONTAINER_CRASH = "container_crash"
    CONFIGURATION_ERROR = "configuration_error"
    DATA_ERROR = "data_error"
    FUNCTIONAL_ERROR = "functional_error"
    UNKNOWN = "unknown"


# ── Request Model ──────────────────────────────────────────────────────

class ErrorAnalysisRequest(BaseModel):
    """
    Request payload for the error analysis endpoint.

    Takes the full correlation output and uses `analysis_target` to route
    to the correct data source(s) for deep-dive error analysis:
      - Agent       → Langfuse (AI agent traces/spans)
      - InfraLogs   → Prometheus (infrastructure metrics)
      - Unknown     → Both Langfuse + Prometheus
    """

    correlation: CorrelationResult = Field(
        ...,
        description="Correlation result from the Correlation Agent, includes analysis_target for routing",
    )
    incident: NormalizedIncident = Field(
        ...,
        description="Normalized incident from the Normalization Agent",
    )
    trace_id: Optional[str] = Field(
        None,
        description="Distributed trace ID. Required when analysis_target is Agent or Unknown.",
    )
    agent_name: str = Field(
        ...,
        description="Name of the AI agent under investigation",
        examples=["summarizer-v2", "retrieval-agent"],
    )


# ── Response Models (Error Analysis output) ────────────────────────────

class ErrorDetail(BaseModel):
    """A single error identified during deep-dive analysis."""

    error_id: str = Field(
        ...,
        description="Unique identifier for this error instance (e.g. ERR-001)",
    )
    category: ErrorCategory = Field(
        ...,
        description="Functional category of the error",
    )
    severity: ErrorSeverity = Field(
        ...,
        description="Severity level of this error",
    )
    component: str = Field(
        ...,
        description="Service or component where this error occurred",
    )
    error_message: str = Field(
        ...,
        description="The actual error message or description extracted from logs",
    )
    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp when this error occurred",
    )
    evidence: str = Field(
        ...,
        description="Raw log evidence supporting this error identification",
    )
    source: str = Field(
        ...,
        description="Data source where this error was found (langfuse or prometheus)",
    )

    @field_validator("category", mode="before")
    @classmethod
    def coerce_category(cls, v: object) -> object:
        """Map any LLM-invented category value to 'unknown' instead of crashing."""
        if isinstance(v, str):
            valid = {e.value for e in ErrorCategory}
            if v not in valid:
                return ErrorCategory.UNKNOWN.value
        return v

    @field_validator("severity", mode="before")
    @classmethod
    def coerce_severity(cls, v: object) -> object:
        """Map any LLM-invented severity value to 'medium' instead of crashing."""
        if isinstance(v, str):
            valid = {e.value for e in ErrorSeverity}
            if v not in valid:
                return ErrorSeverity.MEDIUM.value
        return v


class ErrorPattern(BaseModel):
    """A recurring error pattern detected across logs."""

    pattern_name: str = Field(
        ...,
        description="Short descriptive name for the pattern (e.g. 'Repeated LLM Timeout')",
    )
    description: str = Field(
        ...,
        description="Detailed description of the recurring pattern",
    )
    occurrence_count: int = Field(
        ...,
        ge=1,
        description="Number of times this pattern was observed",
    )
    affected_components: list[str] = Field(
        default_factory=list,
        description="Components affected by this pattern",
    )
    error_ids: list[str] = Field(
        default_factory=list,
        description="References to ErrorDetail.error_id entries that form this pattern",
    )


class ErrorImpact(BaseModel):
    """Assessment of the error's impact on the system."""

    affected_service: str = Field(
        ...,
        description="Service or component that was impacted",
    )
    impact_description: str = Field(
        ...,
        description="How this service was affected by the error(s)",
    )
    severity: ErrorSeverity = Field(
        ...,
        description="Severity of the impact on this service",
    )


class ErrorAnalysisResult(BaseModel):
    """
    Core error analysis output — the detailed error breakdown produced
    by the Error Analysis Agent. This Pydantic model IS the contract;
    GPT-4o output is validated against it.
    """

    analysis_summary: str = Field(
        ...,
        max_length=500,
        description="High-level summary of the error analysis findings",
    )
    analysis_target: AnalysisDomain = Field(
        ...,
        description="Which domain was analyzed: Agent, InfraLogs, or Unknown (both)",
    )
    errors: list[ErrorDetail] = Field(
        ...,
        min_length=1,
        description="List of individual errors identified during deep-dive analysis",
    )
    error_patterns: list[ErrorPattern] = Field(
        default_factory=list,
        description="Recurring error patterns detected across the logs",
    )
    error_impacts: list[ErrorImpact] = Field(
        default_factory=list,
        description="Assessment of how errors impacted each affected service",
    )
    error_propagation_path: list[str] = Field(
        default_factory=list,
        description="Ordered list showing how errors propagated (e.g. ['DNS failure → proxy timeout → gateway 502'])",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for the overall error analysis (0.0-1.0)",
    )


class ErrorAnalysisResponse(BaseModel):
    """Full API response wrapping the error analysis result plus metadata."""

    analysis: ErrorAnalysisResult = Field(
        ...,
        description="The error analysis produced by the agent",
    )
    rca_target: AnalysisDomain = Field(
        ...,
        description="Where the next RCA agent should investigate — passed through from the Correlation Agent's analysis_target (Agent, InfraLogs, or Unknown)",
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
