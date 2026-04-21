from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings, logger
from app.models.error_analysis import ErrorAnalysisResult
from app.models.rca import RCAResult
from app.models.recommendation import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendationResult,
)

# ── System Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a Recommendation Engine in a distributed observability system.

Your job is to synthesize the upstream Error Analysis and Root Cause Analysis
findings and produce a ranked set of actionable solutions to resolve the
incident and prevent recurrence.

You MUST:
- Produce between 1 and 4 solutions — ONLY as many as genuinely applicable
- If the problem has only 1 or 2 real solutions, output only those — do NOT
  pad with vague or redundant solutions to reach 4
- Rank solutions by priority: rank 1 = highest priority
- Rank 1 MUST directly address the identified root cause
- Each subsequent rank addresses secondary concerns, mitigations, or prevention
- Each solution must be specific and actionable — not generic advice
- Reference error_ids from the Error Analysis to link solutions to specific errors
- Indicate whether each solution directly addresses the root cause or mitigates impact
- Estimate implementation effort honestly

You MUST NOT:
- Invent problems not found in the Error Analysis or RCA
- Recommend solutions unrelated to the identified errors
- Always output 4 solutions regardless — fewer is better than padding
- Give vague advice like "improve monitoring" without specifics
- Hallucinate components or services not mentioned in the analysis
- Claim CPU or memory saturation unless the RCA includes explicit CPU or memory
  metric evidence. For latency-only RCA, recommend capacity scaling,
  concurrency/worker tuning, caching, or query-path optimization.
- For Medical RAG pod resource threshold incidents, rank the pod threshold config
  change first. Name POD_CPU_THRESHOLD_PERCENT and/or
  POD_MEMORY_THRESHOLD_PERCENT explicitly, then validate by rerunning the bounded
  pod CPU-utilisation script.
- When Deployment Context is present, use it to make the recommendation concrete.
  If runtime is docker or orchestrator is docker compose, rank 1 MUST mention the
  listed Docker config file(s), especially MedicalAgent/Dockerfile and
  MedicalAgent/docker-compose.yml for Medical RAG, and include recreating the
  service with `docker compose up -d --build medical-rag-pod`.

## Ranking Rules
- Rank 1: Directly fixes the root cause. This is the immediate action to take.
- Rank 2: Addresses the most critical secondary concern or prevents propagation.
- Rank 3: Addresses a contributing factor or adds resilience.
- Rank 4: Preventive measure to avoid future recurrence (only if genuinely useful).
- If fewer than 4 distinct, useful solutions exist, output only 1-3.

## Root Cause Analysis Context
- RCA Summary: {rca_summary}
- Root Cause Category: {root_cause_category}
- Root Cause Component: {root_cause_component}
- Root Cause Description: {root_cause_description}
- Root Cause Confidence: {root_cause_confidence}
- Causal Chain: {causal_chain}
- Contributing Factors: {contributing_factors}
- Blast Radius: {blast_radius}

## Error Analysis Context
- Error Analysis Summary: {error_analysis_summary}
- Errors Found: {error_count}
- Error Categories: {error_categories}
- Error Propagation Path: {error_propagation_path}

{error_details_section}

## Deployment Context
{deployment_context}

## Output
Respond with ONLY a valid JSON object matching the schema below.
No markdown fences, no explanation — raw JSON only.

{schema}
"""


class RecommendationAgent:
    """
    Recommendation Agent — takes the Error Analysis and RCA outputs,
    synthesizes the findings, and uses GPT-4o to produce 1-4 ranked
    actionable solutions.

    This agent does NOT fetch any external logs — it works purely
    from the upstream agents' outputs.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._response_schema = self._build_response_schema()

    # ── Public API ─────────────────────────────────────────────────────

    async def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        """
        Full recommendation pipeline:
        1. Build LLM prompt with Error Analysis + RCA context
        2. Call GPT-4o
        3. Validate response against RecommendationResult model
        """
        start = time.perf_counter()

        # Step 1: Build user message
        user_message = self._build_user_message(
            error_analysis=request.error_analysis,
            rca=request.rca,
            agent_name=request.agent_name,
            deployment_context=request.deployment_context,
        )

        # Step 2: Call GPT-4o
        raw_output = await self._call_llm(
            user_message=user_message,
            error_analysis=request.error_analysis,
            rca=request.rca,
            agent_name=request.agent_name,
            deployment_context=request.deployment_context,
        )

        # Step 3: Validate
        result = self._parse_and_validate(raw_output)

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "Recommendation complete | agent=%s solutions=%d "
            "top_solution='%s' confidence=%.2f time=%.0fms",
            request.agent_name,
            len(result.solutions),
            result.solutions[0].title if result.solutions else "none",
            result.confidence,
            elapsed_ms,
        )

        return RecommendationResponse(
            recommendations=result,
            processing_time_ms=round(elapsed_ms, 2),
        )

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _build_response_schema() -> str:
        """Generate JSON schema from RecommendationResult Pydantic model."""
        schema = RecommendationResult.model_json_schema()
        return json.dumps(schema, indent=2)

    @staticmethod
    def _format_error_details(error_analysis: ErrorAnalysisResult) -> str:
        """Format individual errors from Error Analysis into a readable section."""
        lines: list[str] = ["## Individual Errors"]
        for err in error_analysis.errors:
            lines.append(
                f"- [{err.error_id}] {err.category.value} | {err.severity.value} | "
                f"component={err.component} | msg={err.error_message}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_deployment_context(deployment_context: dict[str, Any] | None) -> str:
        """Format deployment context for concrete, file-aware recommendations."""
        if not deployment_context:
            return "No deployment context was provided."

        lines: list[str] = []
        for key, value in deployment_context.items():
            if isinstance(value, list):
                rendered = ", ".join(str(item) for item in value)
            else:
                rendered = str(value)
            lines.append(f"- {key}: {rendered}")
        return "\n".join(lines)

    def _build_user_message(
        self,
        error_analysis: ErrorAnalysisResult,
        rca: RCAResult,
        agent_name: str,
        deployment_context: dict[str, Any] | None,
    ) -> str:
        """Build the user message with full context from both upstream agents."""
        lines: list[str] = [
            "## Agent Under Investigation",
            f"- Agent: {agent_name}",
            "",
            "## Root Cause Analysis",
            f"- Summary: {rca.rca_summary}",
            f"- Root Cause: {rca.root_cause.component} ({rca.root_cause.category.value})",
            f"- Description: {rca.root_cause.description}",
            f"- Confidence: {rca.root_cause.confidence}",
        ]

        if rca.causal_chain:
            lines.append("- Causal Chain:")
            for link in rca.causal_chain:
                lines.append(
                    f"  - {link.source_event} →[{link.link_type.value}]→ "
                    f"{link.target_event}"
                )

        if rca.contributing_factors:
            lines.append("- Contributing Factors:")
            for cf in rca.contributing_factors:
                lines.append(
                    f"  - {cf.factor} (component={cf.component}, "
                    f"severity={cf.severity})"
                )

        if rca.blast_radius:
            lines.append(f"- Blast Radius: {', '.join(rca.blast_radius)}")

        lines.append("")
        lines.append("## Deployment Context")
        lines.append(self._format_deployment_context(deployment_context))

        lines.append("")
        lines.append("## Error Analysis")
        lines.append(f"- Summary: {error_analysis.analysis_summary}")
        lines.append(f"- Total Errors: {len(error_analysis.errors)}")

        if error_analysis.error_propagation_path:
            lines.append(
                f"- Propagation: {' → '.join(error_analysis.error_propagation_path)}"
            )

        lines.append("")
        lines.append("## Individual Errors")
        for err in error_analysis.errors:
            lines.append(
                f"- [{err.error_id}] {err.category.value} | {err.severity.value} | "
                f"component={err.component} | msg={err.error_message}"
            )

        if error_analysis.error_patterns:
            lines.append("")
            lines.append("## Error Patterns")
            for pat in error_analysis.error_patterns:
                lines.append(
                    f"- {pat.pattern_name} (count={pat.occurrence_count}): "
                    f"{pat.description}"
                )

        if error_analysis.error_impacts:
            lines.append("")
            lines.append("## Error Impacts")
            for imp in error_analysis.error_impacts:
                lines.append(
                    f"- {imp.affected_service} ({imp.severity.value}): "
                    f"{imp.impact_description}"
                )

        return "\n".join(lines)

    async def _call_llm(
        self,
        user_message: str,
        error_analysis: ErrorAnalysisResult,
        rca: RCAResult,
        agent_name: str,
        deployment_context: dict[str, Any] | None,
    ) -> str:
        """Send the prompt to GPT-4o and return the raw JSON response."""
        logger.debug("Calling LLM for recommendation with %d chars", len(user_message))

        # Collect unique categories and components
        error_categories = list({e.category.value for e in error_analysis.errors})

        # Format contributing factors
        cf_desc = "; ".join(
            f"{cf.factor} ({cf.component})" for cf in rca.contributing_factors
        ) if rca.contributing_factors else "None identified"

        # Format causal chain for system prompt
        causal_chain_desc = " → ".join(
            f"{link.source_event} →[{link.link_type.value}]→ {link.target_event}"
            for link in rca.causal_chain
        ) if rca.causal_chain else "Not determined"

        system_prompt = SYSTEM_PROMPT.format(
            rca_summary=rca.rca_summary,
            root_cause_category=rca.root_cause.category.value,
            root_cause_component=rca.root_cause.component,
            root_cause_description=rca.root_cause.description,
            root_cause_confidence=rca.root_cause.confidence,
            causal_chain=causal_chain_desc,
            contributing_factors=cf_desc,
            blast_radius=", ".join(rca.blast_radius) if rca.blast_radius else "Not determined",
            error_analysis_summary=error_analysis.analysis_summary,
            error_count=len(error_analysis.errors),
            error_categories=", ".join(error_categories),
            error_propagation_path=(
                " → ".join(error_analysis.error_propagation_path)
                if error_analysis.error_propagation_path
                else "Not determined"
            ),
            error_details_section=self._format_error_details(error_analysis),
            deployment_context=self._format_deployment_context(deployment_context),
            schema=self._response_schema,
        )

        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("LLM returned empty response")

        logger.debug("LLM recommendation response: %s", content[:200])
        return content

    @staticmethod
    def _parse_and_validate(raw_json: str) -> RecommendationResult:
        """Parse LLM output and validate against the Pydantic model."""
        try:
            data: dict[str, Any] = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            raise ValueError(f"LLM output is not valid JSON: {exc}") from exc

        return RecommendationResult.model_validate(data)
