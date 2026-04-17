from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.normalization import NormalizationResponse
from app.models.correlation import CorrelationResponse
from app.models.error_analysis import ErrorAnalysisResponse
from app.models.rca import RCAResponse
from app.models.recommendation import RecommendationResponse


# ── Request Model ──────────────────────────────────────────────────────

class InvestigationRequest(BaseModel):
    """
    Single entry point for the full observability pipeline.

    The orchestrator chains all 5 agents automatically:
      Normalization → Correlation → Error Analysis → RCA → Recommendation

    The trace_id is used as the primary key for storing all agent I/O.
    """

    timestamp: str = Field(
        ...,
        description="ISO-8601 timestamp of the incident",
        examples=["2025-01-15T10:32:00Z"],
    )
    trace_id: str = Field(
        ...,
        description="Distributed trace ID — used as the primary key to store and retrieve all agent I/O",
        examples=["trace-abc-123-def-456"],
    )
    agent_name: str = Field(
        ...,
        description="Name of the AI agent that encountered the failure",
        examples=["summarizer-v2", "retrieval-agent", "planner-v1"],
    )


# ── Response Model ─────────────────────────────────────────────────────

class PipelineStep(BaseModel):
    """Metadata about a single pipeline step execution."""

    agent: str = Field(..., description="Name of the agent that ran")
    status: str = Field(..., description="completed or failed")
    processing_time_ms: float = Field(..., description="Time taken in ms")
    error: Optional[str] = Field(None, description="Error message if failed")


class InvestigationResponse(BaseModel):
    """
    Full pipeline response — all 5 agent outputs keyed by the trace_id.

    Retrieve this later via GET /api/v1/traces/{trace_id}
    """

    trace_id: str = Field(
        ...,
        description="The trace_id used as primary key — use GET /api/v1/traces/{trace_id} to retrieve later",
    )

    # Agent outputs (None if that step was skipped / failed)
    normalization: Optional[NormalizationResponse] = Field(
        None, description="Output from the Normalization Agent (step 1)",
    )
    correlation: Optional[CorrelationResponse] = Field(
        None, description="Output from the Correlation Agent (step 2)",
    )
    error_analysis: Optional[ErrorAnalysisResponse] = Field(
        None, description="Output from the Error Analysis Agent (step 3)",
    )
    rca: Optional[RCAResponse] = Field(
        None, description="Output from the RCA Agent (step 4)",
    )
    recommendations: Optional[RecommendationResponse] = Field(
        None, description="Output from the Recommendation Agent (step 5)",
    )

    # Pipeline metadata
    pipeline_steps: list[PipelineStep] = Field(
        default_factory=list, description="Execution log per step",
    )
    total_processing_time_ms: float = Field(
        ..., description="Total wall-clock time in ms",
    )
    completed: bool = Field(
        ..., description="Whether the full pipeline completed successfully",
    )
