from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings, logger
from app.models.correlation import AnalysisDomain
from app.models.error_analysis import ErrorAnalysisResult, ErrorDetail
from app.models.normalization import NormalizedIncident
from app.models.rca import (
    RCARequest,
    RCAResponse,
    RCAResult,
)
from app.services.langfuse_client import LangfuseClient
from app.services.prometheus_client import PrometheusClient
from app.services.event_bus import get_event_bus
from app.services.trace_store import TraceStore

# ── System Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a Root Cause Analysis (RCA) Engine in a distributed observability system.

Your job is to determine the definitive root cause of an incident by combining
the upstream Error Analysis findings with fresh log evidence from the appropriate
data sources. You go deeper than error identification — you determine WHY the
errors occurred and trace the full causal chain from origin to impact.

You MUST:
- Identify the single primary root cause of the incident
- Build a complete causal chain showing cause → effect links with evidence
- Distinguish between direct causes, indirect causes, triggers, and amplifiers
- Identify contributing factors that worsened or enabled the failure
- Build a failure timeline marking the root cause origin event
- Determine the blast radius — every component affected
- Reference error_ids from the Error Analysis output to link your findings
- Base ALL findings strictly on log evidence — no speculation
- Produce a complete Five Whys analysis with EXACTLY 5 why steps

You MUST NOT:
- Provide recommendations or remediation steps (that is a separate agent's job)
- Hallucinate root causes not supported by logs
- Invent services, components, or events
- Guess without evidence
- Confuse correlation with causation — temporal proximity alone is not causation

## Rules
- The root cause is the EARLIEST verifiable failure point that triggered the chain
- Every causal link MUST have direct log evidence
- Confidence reflects evidence strength: high if logs are explicit, lower if inferred
- Contributing factors are conditions that existed before or during the incident
  that amplified damage but did not initiate the failure
- blast_radius includes every component that experienced degradation or failure
- If only one data source has relevant data, the analysis may be shorter — that is OK
- If no external logs are available, perform RCA from error analysis context alone
  and set lower confidence
- Do not claim CPU saturation, memory pressure, or resource utilization metrics
  unless those exact metric entries are present in the logs/metrics. For
  latency-only evidence, describe the cause as capacity/throughput saturation
  or performance degradation under concurrent load.
- For Medical RAG pod resource threshold incidents, fetch and use both Langfuse
  and Prometheus data from the last 5 minutes when available. The fix to surface
  is a pod threshold configuration change, specifically
  POD_CPU_THRESHOLD_PERCENT and/or POD_MEMORY_THRESHOLD_PERCENT, followed by
  redeploying the pod and rerunning the bounded CPU-utilisation scenario.
- When Deployment Context is present, treat it as authoritative application
  context. If runtime is docker or orchestrator is docker compose, RCA wording
  MUST identify the Docker-managed configuration, including the listed config
  files. For Medical RAG pod threshold incidents, the concrete configuration
  location is MedicalAgent/Dockerfile and/or MedicalAgent/docker-compose.yml,
  not a generic environment setting.

## Specificity Rules for root_cause and rca_summary
- root_cause.description MUST state the exact error condition observed in the logs —
  use the actual error message text, the actual component name, and the actual failure state
- rca_summary MUST be specific enough that a reader can open the logs and verify the
  statement directly — it must name what broke, where, and what the logs said
- Never reduce the root cause to a category label (e.g. "configuration error",
  "agent logic failure") — always describe WHAT the specific condition was
- The Five Whys analysis goes DEEPER than root_cause — it drills into WHY that specific
  root cause existed; it must NOT cause root_cause.description to become more abstract
- root_cause.description = the specific observable failure from logs (concrete)
- five_why_analysis.fundamental_root_cause = the underlying structural reason that
  specific failure was possible (may be more systemic, but still evidence-grounded)
- Both fields are required and serve different purposes — do not merge or conflate them

## Five Whys Analysis Rules
You MUST produce a `five_why_analysis` with EXACTLY 5 `whys` entries (step 1 through 5).
Follow this methodology strictly:
- problem_statement: State the observed symptom or error as reported
- Why 1 (step=1): Ask "Why did [problem_statement] occur?" — answer with the immediate cause
- Why 2 (step=2): Ask "Why did [Why 1 answer] occur?" — go one level deeper
- Why 3 (step=3): Ask "Why did [Why 2 answer] occur?" — go deeper still
- Why 4 (step=4): Ask "Why did [Why 3 answer] occur?" — continue drilling
- Why 5 (step=5): Ask "Why did [Why 4 answer] occur?" — this reveals the fundamental cause
- fundamental_root_cause: Summarize the finding from Why 5 — the deepest traceable cause
Each why step MUST include:
- step: integer 1–5
- question: the full "Why ...?" question asked at that step
- answer: the explanation of the cause at that level, grounded in evidence
- evidence: specific log line, metric, or error detail supporting the answer
- component: the service or system element involved at this step
If evidence is thin at deeper steps, acknowledge limited visibility and set lower confidence.

## Error Analysis Context
The upstream Error Analysis Agent identified the following errors:

Errors Found: {error_count}
Error Summary: {error_analysis_summary}
Error Categories: {error_categories}
Error Propagation Path: {error_propagation_path}
Affected Components: {affected_components}

{error_details_section}

## Normalization Context
- Error Type: {error_type}
- Error Summary: {error_summary}
- Signals: {signals}
- Agent: {agent_name}

## Deployment Context
{deployment_context}

## Data Sources Analyzed
{data_sources_description}

## Output
Respond with ONLY a valid JSON object matching the schema below.
No markdown fences, no explanation — raw JSON only.

{schema}
"""


class RCAAgent:
    """
    RCA Agent — takes the Error Analysis output, routes to the correct
    data source(s) based on `rca_target`, fetches fresh logs, and uses
    GPT-4o to produce a detailed root cause analysis.

    Routing (based on rca_target from Error Analysis):
      - Agent       → Langfuse only (AI agent traces/spans)
      - InfraLogs   → Prometheus only (infrastructure metrics)
      - Unknown     → Both Langfuse + Prometheus
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._response_schema = self._build_response_schema()

        self._langfuse = LangfuseClient()
        self._prometheus = PrometheusClient()

    # ── Public API ─────────────────────────────────────────────────────

    async def analyze_root_cause(self, request: RCARequest) -> RCAResponse:
        """
        Full RCA pipeline:
        1. Route to data source(s) based on rca_target
        2. Fetch fresh logs from Langfuse and/or Prometheus
        3. Build LLM prompt with error analysis context + fresh logs + schema
        4. Call GPT-4o
        5. Validate response against RCAResult model

        If external sources return no data, the agent still performs RCA
        using the error analysis and normalization context.
        """
        start = time.perf_counter()

        rca_target = request.rca_target

        # Step 1 & 2: Route and fetch logs
        all_logs, data_sources = await self._fetch_logs_by_target(
            rca_target=rca_target,
            trace_id=request.trace_id,
            timestamp=request.incident.timestamp,
            agent_name=request.agent_name,
        )

        if not all_logs:
            logger.warning(
                "No external logs fetched from %s for agent=%s (rca_target=%s) — "
                "performing RCA from error analysis context only",
                data_sources, request.agent_name, rca_target.value,
            )

        # Step 3: Build user message
        user_message = self._build_user_message(
            error_analysis=request.error_analysis,
            incident=request.incident,
            agent_name=request.agent_name,
            trace_id=request.trace_id,
            logs=all_logs,
            data_sources=data_sources,
            deployment_context=request.deployment_context,
        )

        # Step 4: Call GPT-4o
        raw_output = await self._call_llm(
            user_message=user_message,
            error_analysis=request.error_analysis,
            incident=request.incident,
            agent_name=request.agent_name,
            rca_target=rca_target,
            data_sources=data_sources,
            deployment_context=request.deployment_context,
        )

        # Step 5: Validate
        rca_result = self._parse_and_validate(raw_output)

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "RCA complete | target=%s sources=%s root_cause=%s "
            "category=%s causal_links=%d confidence=%.2f time=%.0fms",
            rca_target.value,
            data_sources,
            rca_result.root_cause.component,
            rca_result.root_cause.category.value,
            len(rca_result.causal_chain),
            rca_result.confidence,
            elapsed_ms,
        )

        return RCAResponse(
            rca=rca_result,
            rca_target=rca_target,
            data_sources=data_sources,
            total_logs_analyzed=len(all_logs),
            processing_time_ms=round(elapsed_ms, 2),
        )

    # ── Data fetching with rca_target routing ──────────────────────────

    async def _fetch_logs_by_target(
        self,
        rca_target: AnalysisDomain,
        trace_id: str | None,
        timestamp: str,
        agent_name: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Route to data source(s) based on rca_target:
          - Agent       → Langfuse only
          - InfraLogs   → Prometheus only
          - Unknown     → Both Langfuse + Prometheus
        """
        all_logs: list[dict[str, Any]] = []
        data_sources: list[str] = []

        fetch_langfuse = rca_target in (AnalysisDomain.AGENT, AnalysisDomain.UNKNOWN)
        fetch_prometheus = rca_target in (AnalysisDomain.INFRA_LOGS, AnalysisDomain.UNKNOWN)

        # Trace timespan extracted from Langfuse (used to set Prometheus window)
        trace_start: str | None = None
        trace_end: str | None = None

        # ── Langfuse (Agent traces) ───────────────────────────────────
        if fetch_langfuse:
            if trace_id:
                logger.info(
                    "RCA | fetching Langfuse trace | rca_target=%s trace_id=%s",
                    rca_target.value, trace_id,
                )
                try:
                    langfuse_logs = await self._langfuse.fetch_trace(trace_id)
                    all_logs.extend(langfuse_logs)
                    if langfuse_logs:
                        data_sources.append("langfuse")
                    logger.info(
                        "RCA | Langfuse returned %d entries", len(langfuse_logs),
                    )
                    trace_start, trace_end = self._langfuse.extract_timespan(langfuse_logs)
                    logger.info(
                        "RCA | trace span: %s → %s", trace_start, trace_end,
                    )
                    await get_event_bus().publish(trace_id, {
                        "type": "logs_fetched", "agent": "rca",
                        "source": "langfuse", "count": len(langfuse_logs),
                        "entries": langfuse_logs,
                    })
                    await TraceStore().save_fetched_logs(trace_id, "rca", "langfuse", langfuse_logs)
                except Exception as exc:
                    logger.warning("RCA | Langfuse fetch failed: %s", exc)
                    err_entry = {
                        "timestamp": timestamp, "source": "langfuse", "service": "langfuse",
                        "message": f"Langfuse trace fetch failed: {exc}",
                        "level": "WARN", "metadata": {"error": str(exc), "trace_id": trace_id},
                    }
                    await get_event_bus().publish(trace_id, {
                        "type": "logs_fetched", "agent": "rca",
                        "source": "langfuse", "count": 0, "entries": [err_entry],
                    })
                    await TraceStore().save_fetched_logs(trace_id, "rca", "langfuse", [err_entry])
            else:
                logger.warning(
                    "RCA | rca_target=%s requires Langfuse but no trace_id provided",
                    rca_target.value,
                )
                missing_entry = {
                    "timestamp": timestamp,
                    "source": "langfuse",
                    "service": "langfuse",
                    "message": "No trace_id provided — cannot fetch Langfuse data",
                    "level": "WARN",
                    "metadata": {"error": "missing_trace_id"},
                }
                await TraceStore().save_fetched_logs("", "rca", "langfuse", [missing_entry])

        # ── Prometheus (Infra metrics) ────────────────────────────────
        if fetch_prometheus:
            logger.info(
                "RCA | fetching Prometheus metrics | rca_target=%s ts=%s agent=%s",
                rca_target.value, timestamp, agent_name,
            )
            try:
                prom_logs = await self._prometheus.fetch_metrics(
                    timestamp=timestamp,
                    agent_name=agent_name,
                    trace_start=trace_start,
                    trace_end=trace_end,
                )
                all_logs.extend(prom_logs)
                if prom_logs:
                    data_sources.append("prometheus")
                logger.info(
                    "RCA | Prometheus returned %d entries", len(prom_logs),
                )
                await get_event_bus().publish(trace_id or "", {
                    "type": "logs_fetched", "agent": "rca",
                    "source": "prometheus", "count": len(prom_logs),
                    "entries": prom_logs,
                })
                await TraceStore().save_fetched_logs(trace_id or "", "rca", "prometheus", prom_logs)
            except Exception as exc:
                logger.warning("RCA | Prometheus fetch failed: %s", exc)
                err_entry = {
                    "timestamp": timestamp, "source": "prometheus", "service": "prometheus",
                    "message": f"Prometheus query failed: {exc}",
                    "level": "WARN", "metadata": {"error": str(exc)},
                }
                await get_event_bus().publish(trace_id or "", {
                    "type": "logs_fetched", "agent": "rca",
                    "source": "prometheus", "count": 0, "entries": [err_entry],
                })
                await TraceStore().save_fetched_logs(trace_id or "", "rca", "prometheus", [err_entry])

        # Sort all logs by timestamp for chronological analysis
        all_logs.sort(key=lambda x: x.get("timestamp", ""))

        return all_logs, data_sources

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _build_response_schema() -> str:
        """Generate JSON schema from RCAResult Pydantic model."""
        schema = RCAResult.model_json_schema()
        return json.dumps(schema, indent=2)

    @staticmethod
    def _format_error_details(errors: list[ErrorDetail]) -> str:
        """Format individual errors from Error Analysis into a readable section."""
        lines: list[str] = ["## Individual Errors from Error Analysis"]
        for err in errors:
            lines.append(
                f"- [{err.error_id}] {err.category.value} | {err.severity.value} | "
                f"component={err.component} | msg={err.error_message} | "
                f"ts={err.timestamp} | source={err.source}"
            )
            lines.append(f"  Evidence: {err.evidence}")
        return "\n".join(lines)

    @staticmethod
    def _format_deployment_context(deployment_context: dict[str, Any] | None) -> str:
        """Format deployment context for the LLM without inventing missing details."""
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
        incident: NormalizedIncident,
        agent_name: str,
        trace_id: str | None,
        logs: list[dict[str, Any]],
        data_sources: list[str],
        deployment_context: dict[str, Any] | None,
    ) -> str:
        """Build the user message with error analysis context + all fresh logs."""
        lines: list[str] = [
            "## Incident Context",
            f"- Agent: {agent_name}",
            f"- Error Type: {incident.error_type.value}",
            f"- Error Summary: {incident.error_summary}",
            f"- Timestamp: {incident.timestamp}",
            f"- Signals: {', '.join(incident.signals) if incident.signals else 'none'}",
            f"- RCA Target: {error_analysis.analysis_target.value}",
        ]
        if trace_id:
            lines.append(f"- Trace ID: {trace_id}")
        if incident.entities.agent_id:
            lines.append(f"- Entity Agent ID: {incident.entities.agent_id}")
        if incident.entities.service:
            lines.append(f"- Entity Service: {incident.entities.service}")

        lines.append("")

        # Deployment context
        lines.append("## Deployment Context")
        lines.append(self._format_deployment_context(deployment_context))
        lines.append("")

        # Error Analysis context
        lines.append("## Error Analysis Summary")
        lines.append(f"- Summary: {error_analysis.analysis_summary}")
        lines.append(f"- Errors Found: {len(error_analysis.errors)}")
        lines.append(f"- Patterns Found: {len(error_analysis.error_patterns)}")
        lines.append(f"- Confidence: {error_analysis.confidence}")

        if error_analysis.error_propagation_path:
            lines.append(f"- Propagation Path: {' → '.join(error_analysis.error_propagation_path)}")

        lines.append("")

        # Individual errors
        lines.append("## Errors Identified by Error Analysis Agent")
        for err in error_analysis.errors:
            lines.append(
                f"- [{err.error_id}] {err.category.value} | {err.severity.value} | "
                f"component={err.component} | msg={err.error_message} | "
                f"ts={err.timestamp} | source={err.source}"
            )
            lines.append(f"  Evidence: {err.evidence}")

        lines.append("")

        # Error patterns
        if error_analysis.error_patterns:
            lines.append("## Error Patterns Detected")
            for pat in error_analysis.error_patterns:
                lines.append(
                    f"- {pat.pattern_name}: {pat.description} "
                    f"(count={pat.occurrence_count}, "
                    f"components={', '.join(pat.affected_components)}, "
                    f"error_ids={', '.join(pat.error_ids)})"
                )
            lines.append("")

        # Error impacts
        if error_analysis.error_impacts:
            lines.append("## Error Impacts")
            for imp in error_analysis.error_impacts:
                lines.append(
                    f"- {imp.affected_service} ({imp.severity.value}): "
                    f"{imp.impact_description}"
                )
            lines.append("")

        # Fresh logs from data sources
        if not logs:
            lines.append("## Fresh Logs: NONE AVAILABLE")
            lines.append("No logs were returned from external sources.")
            lines.append("Perform RCA from the error analysis context above.")
            lines.append("Set lower confidence since evidence is limited.")
        else:
            lines.append(f"## Fresh Logs from: {', '.join(data_sources)}")
            lines.append("")

            for source in data_sources:
                source_logs = [l for l in logs if l.get("source") == source]
                lines.append(f"### Logs from {source} ({len(source_logs)} entries)")
                for i, log in enumerate(source_logs, 1):
                    parts = [f"[{source}:{i}]"]
                    if log.get("timestamp"):
                        parts.append(f"ts={log['timestamp']}")
                    if log.get("service"):
                        parts.append(f"svc={log['service']}")
                    if log.get("level"):
                        parts.append(f"level={log['level']}")
                    parts.append(f"msg={log['message']}")
                    if log.get("metadata"):
                        parts.append(f"meta={json.dumps(log['metadata'])}")
                    lines.append(" | ".join(parts))
                lines.append("")

        return "\n".join(lines)

    async def _call_llm(
        self,
        user_message: str,
        error_analysis: ErrorAnalysisResult,
        incident: NormalizedIncident,
        agent_name: str,
        rca_target: AnalysisDomain,
        data_sources: list[str],
        deployment_context: dict[str, Any] | None,
    ) -> str:
        """Send the prompt to GPT-4o and return the raw JSON response."""
        logger.debug("Calling LLM for RCA with %d chars", len(user_message))

        if not data_sources:
            ds_desc = (
                "NO external log or metric entries were returned. "
                "Use the error analysis and normalization context to determine root cause. "
                "Do not cite Prometheus or Langfuse as evidence unless entries are present in the user message. "
                "Set confidence lower since evidence is limited."
            )
        elif rca_target == AnalysisDomain.AGENT:
            ds_desc = (
                "Langfuse (AI agent traces/spans) only. "
                "Investigate agent execution flow, LLM calls, tool invocations, "
                "and span-level errors to determine the root cause."
            )
        elif rca_target == AnalysisDomain.INFRA_LOGS:
            ds_desc = (
                "Prometheus (infra metrics) only. "
                "Investigate infrastructure health, resource utilization, "
                "service availability, and system-level metrics to determine the root cause."
            )
        elif rca_target == AnalysisDomain.UNKNOWN:
            ds_desc = (
                "Both Langfuse (AI agent traces) AND Prometheus (infra metrics). "
                "Cross-reference agent execution errors with infrastructure health "
                "to determine whether the root cause is at the agent or infra level."
            )
        else:
            ds_desc = (
                "NO external log or metric entries were returned. "
                "Use the error analysis context to determine root cause. "
                "Do not cite Prometheus or Langfuse as evidence unless entries are present in the user message. "
                "Set confidence lower since evidence is limited."
            )

        # Collect unique categories and components from errors
        error_categories = list({e.category.value for e in error_analysis.errors})
        affected_components = list({e.component for e in error_analysis.errors})

        system_prompt = SYSTEM_PROMPT.format(
            error_count=len(error_analysis.errors),
            error_analysis_summary=error_analysis.analysis_summary,
            error_categories=", ".join(error_categories),
            error_propagation_path=(
                " → ".join(error_analysis.error_propagation_path)
                if error_analysis.error_propagation_path
                else "Not determined"
            ),
            affected_components=", ".join(affected_components),
            error_details_section=self._format_error_details(error_analysis.errors),
            error_type=incident.error_type.value,
            error_summary=incident.error_summary,
            signals=", ".join(incident.signals) if incident.signals else "none",
            agent_name=agent_name,
            deployment_context=self._format_deployment_context(deployment_context),
            data_sources_description=ds_desc,
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

        logger.debug("LLM RCA response: %s", content[:200])
        return content

    @staticmethod
    def _parse_and_validate(raw_json: str) -> RCAResult:
        """Parse LLM output and validate against the Pydantic model."""
        try:
            data: dict[str, Any] = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            raise ValueError(f"LLM output is not valid JSON: {exc}") from exc

        return RCAResult.model_validate(data)
