from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models.error_analysis import ErrorAnalysisResult
from app.models.rca import RCAResult


# ── Enums ──────────────────────────────────────────────────────────────

class SolutionEffort(str, Enum):
    """Estimated effort to implement the solution."""

    QUICK_FIX = "quick_fix"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SolutionCategory(str, Enum):
    """Category of the recommended solution."""

    CONFIG_CHANGE = "config_change"
    CODE_FIX = "code_fix"
    INFRASTRUCTURE = "infrastructure"
    SCALING = "scaling"
    RETRY_LOGIC = "retry_logic"
    FALLBACK = "fallback"
    MONITORING = "monitoring"
    ACCESS_MANAGEMENT = "access_management"
    NETWORK = "network"
    DEPENDENCY_UPDATE = "dependency_update"
    PROCESS_CHANGE = "process_change"
    ARCHITECTURE = "architecture"


# ── Request Model ──────────────────────────────────────────────────────

class RecommendationRequest(BaseModel):
    """
    Request payload for the recommendation endpoint.

    Takes the Error Analysis and RCA outputs to produce ranked
    solutions. No data source routing — this agent synthesises
    upstream findings only; it does NOT fetch fresh logs.
    """

    error_analysis: ErrorAnalysisResult = Field(
        ...,
        description="Error analysis output from the Error Analysis Agent",
    )
    rca: RCAResult = Field(
        ...,
        description="Root cause analysis output from the RCA Agent",
    )
    agent_name: str = Field(
        ...,
        description="Name of the AI agent under investigation",
        examples=["summarizer-v2", "retrieval-agent"],
    )


# ── Response Models (Recommendation output) ───────────────────────────

class Solution(BaseModel):
    """A single recommended solution with ranking."""

    rank: int = Field(
        ...,
        ge=1,
        le=4,
        description="Priority ranking of this solution (1 = highest priority, max 4)",
    )
    title: str = Field(
        ...,
        max_length=120,
        description="Short actionable title for the solution",
    )
    description: str = Field(
        ...,
        description="Detailed description of what to do and why it addresses the root cause",
    )
    category: SolutionCategory = Field(
        ...,
        description="Category of this solution",
    )
    effort: SolutionEffort = Field(
        ...,
        description="Estimated effort to implement",
    )
    addresses_root_cause: bool = Field(
        ...,
        description="Whether this solution directly addresses the identified root cause (True) or mitigates impact/prevents recurrence (False)",
    )
    affected_components: list[str] = Field(
        default_factory=list,
        description="Components that this solution targets",
    )
    expected_outcome: str = Field(
        ...,
        description="What improvement is expected after implementing this solution",
    )
    error_ids: list[str] = Field(
        default_factory=list,
        description="References to ErrorDetail.error_id entries this solution addresses",
    )


class RecommendationResult(BaseModel):
    """
    Core recommendation output — ranked solutions produced by
    the Recommendation Agent. This Pydantic model IS the contract;
    GPT-4o output is validated against it.

    Rules:
      - 1 to 4 solutions (only as many as genuinely applicable)
      - Each solution has a unique rank from 1 to N
      - Rank 1 = highest priority (addresses root cause most directly)
    """

    recommendation_summary: str = Field(
        ...,
        max_length=500,
        description="Executive summary of the recommended action plan",
    )
    solutions: list[Solution] = Field(
        ...,
        min_length=1,
        max_length=4,
        description="Ranked list of recommended solutions (1-4 items, rank 1 = highest priority)",
    )
    root_cause_addressed: str = Field(
        ...,
        description="The root cause that these solutions are designed to address",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in the recommendation set (0.0-1.0)",
    )

    @model_validator(mode="after")
    def _validate_unique_ranks(self) -> "RecommendationResult":
        """Ensure each solution has a unique rank and ranks are sequential from 1."""
        ranks = [s.rank for s in self.solutions]
        expected = list(range(1, len(self.solutions) + 1))
        if sorted(ranks) != expected:
            raise ValueError(
                f"Solution ranks must be sequential from 1 to {len(self.solutions)}, "
                f"got {sorted(ranks)}"
            )
        return self


class RecommendationResponse(BaseModel):
    """Full API response wrapping the recommendation result plus metadata."""

    recommendations: RecommendationResult = Field(
        ...,
        description="The ranked recommendations produced by the agent",
    )
    processing_time_ms: float = Field(
        ...,
        description="Total LLM processing time in milliseconds",
    )
