from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.correlation import AnalysisDomain
from app.models.error_analysis import ErrorAnalysisResult
from app.models.normalization import NormalizedIncident


# ── Enums ──────────────────────────────────────────────────────────────

class RootCauseCategory(str, Enum):
    """Classification of the root cause domain."""

    LLM_PROVIDER = "llm_provider"
    AGENT_LOGIC = "agent_logic"
    PROMPT_ENGINEERING = "prompt_engineering"
    TOOL_INTEGRATION = "tool_integration"
    RETRIEVAL_PIPELINE = "retrieval_pipeline"
    NETWORK = "network"
    DNS = "dns"
    COMPUTE_RESOURCE = "compute_resource"
    MEMORY = "memory"
    STORAGE = "storage"
    CONTAINER_ORCHESTRATION = "container_orchestration"
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    RATE_LIMITING = "rate_limiting"
    DEPENDENCY_FAILURE = "dependency_failure"
    DATA_INTEGRITY = "data_integrity"
    UNKNOWN = "unknown"


class CausalLinkType(str, Enum):
    """Type of causal relationship between two events."""

    DIRECT_CAUSE = "direct_cause"
    INDIRECT_CAUSE = "indirect_cause"
    TRIGGER = "trigger"
    AMPLIFIER = "amplifier"


# ── Request Model ──────────────────────────────────────────────────────

class RCARequest(BaseModel):
    """
    Request payload for the RCA endpoint.

    Takes the Error Analysis output and uses `rca_target` to route
    to the correct data source(s) for root cause investigation:
      - Agent       → Langfuse (AI agent traces/spans)
      - InfraLogs   → Prometheus (infrastructure metrics)
      - Unknown     → Both Langfuse + Prometheus
    """

    error_analysis: ErrorAnalysisResult = Field(
        ...,
        description="Error analysis output from the Error Analysis Agent",
    )
    rca_target: AnalysisDomain = Field(
        ...,
        description="Routing target from Error Analysis Agent — Agent, InfraLogs, or Unknown",
    )
    incident: NormalizedIncident = Field(
        ...,
        description="Normalized incident from the Normalization Agent",
    )
    trace_id: Optional[str] = Field(
        None,
        description="Distributed trace ID. Required when rca_target is Agent or Unknown.",
    )
    agent_name: str = Field(
        ...,
        description="Name of the AI agent under investigation",
        examples=["summarizer-v2", "retrieval-agent"],
    )


# ── Response Models (RCA output) ──────────────────────────────────────

class CausalLink(BaseModel):
    """A single cause-effect link in the causal chain."""

    source_event: str = Field(
        ...,
        description="The causing event or condition",
    )
    target_event: str = Field(
        ...,
        description="The event that was caused",
    )
    link_type: CausalLinkType = Field(
        ...,
        description="Type of causal relationship",
    )
    evidence: str = Field(
        ...,
        description="Log evidence supporting this causal link",
    )


class ContributingFactor(BaseModel):
    """A factor that contributed to or amplified the root cause."""

    factor: str = Field(
        ...,
        description="Description of the contributing factor",
    )
    component: str = Field(
        ...,
        description="Service or component where this factor was observed",
    )
    evidence: str = Field(
        ...,
        description="Log evidence supporting this factor",
    )
    severity: str = Field(
        ...,
        description="How much this factor contributed: primary, secondary, or minor",
    )


class RootCause(BaseModel):
    """The identified root cause of the incident."""

    category: RootCauseCategory = Field(
        ...,
        description="Classification of the root cause domain",
    )
    component: str = Field(
        ...,
        description="The specific service or component that is the root cause",
    )
    description: str = Field(
        ...,
        description="Detailed explanation of what went wrong and why",
    )
    evidence: list[str] = Field(
        ...,
        min_length=1,
        description="List of log evidence entries supporting this root cause determination",
    )
    error_ids: list[str] = Field(
        default_factory=list,
        description="References to ErrorDetail.error_id entries from the Error Analysis that support this root cause",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this root cause determination (0.0-1.0)",
    )


class FailureTimeline(BaseModel):
    """Detailed causal timeline showing the root cause propagation."""

    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp of this event in the causal chain",
    )
    component: str = Field(
        ...,
        description="Service or component where this event occurred",
    )
    event: str = Field(
        ...,
        description="What happened at this point",
    )
    is_root_cause: bool = Field(
        False,
        description="Whether this event is the identified root cause origin",
    )


class WhyStep(BaseModel):
    """A single 'Why?' step in the Five Whys analysis."""

    step: int = Field(
        ...,
        ge=1,
        le=5,
        description="Step number in the Five Whys sequence (1 through 5)",
    )
    question: str = Field(
        ...,
        description="The 'Why?' question asked at this step",
    )
    answer: str = Field(
        ...,
        description="The explanation of the cause at this level of analysis",
    )
    evidence: str = Field(
        ...,
        description="Log or metric evidence supporting this answer",
    )
    component: str = Field(
        ...,
        description="The service or component implicated at this step",
    )


class FiveWhyAnalysis(BaseModel):
    """
    Five Whys root cause analysis — iteratively asks 'Why?' five times,
    each answer becoming the subject of the next question, drilling down
    from the observed symptom to the fundamental root cause.
    """

    problem_statement: str = Field(
        ...,
        description="The initial observed problem or symptom being investigated",
    )
    whys: list[WhyStep] = Field(
        ...,
        min_length=5,
        max_length=5,
        description="The five iterative Why steps, each building on the previous answer",
    )
    fundamental_root_cause: str = Field(
        ...,
        description="The fundamental root cause revealed after five iterations of asking Why",
    )


class RCAResult(BaseModel):
    """
    Core RCA output — the detailed root cause analysis produced by
    the RCA Agent. This Pydantic model IS the contract; GPT-4o
    output is validated against it.
    """

    rca_summary: str = Field(
        ...,
        max_length=800,
        description="Executive summary of the root cause analysis findings",
    )
    root_cause: RootCause = Field(
        ...,
        description="The primary identified root cause",
    )
    causal_chain: list[CausalLink] = Field(
        ...,
        min_length=1,
        description="Ordered causal links showing how the root cause propagated to the observed errors",
    )
    contributing_factors: list[ContributingFactor] = Field(
        default_factory=list,
        description="Factors that contributed to or amplified the failure",
    )
    failure_timeline: list[FailureTimeline] = Field(
        default_factory=list,
        description="Chronological timeline of the failure from root cause to final impact",
    )
    blast_radius: list[str] = Field(
        default_factory=list,
        description="List of all components/services affected by this root cause",
    )
    five_why_analysis: FiveWhyAnalysis = Field(
        ...,
        description=(
            "Five Whys analysis — asks 'Why?' five times, each answer becoming "
            "the input to the next question, tracing from the observed symptom "
            "to the fundamental root cause"
        ),
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in the RCA determination (0.0-1.0)",
    )


class RCAResponse(BaseModel):
    """Full API response wrapping the RCA result plus metadata."""

    rca: RCAResult = Field(
        ...,
        description="The root cause analysis produced by the agent",
    )
    rca_target: AnalysisDomain = Field(
        ...,
        description="Which domain was investigated: Agent, InfraLogs, or Unknown",
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
