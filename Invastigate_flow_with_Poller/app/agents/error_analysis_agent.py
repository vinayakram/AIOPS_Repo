from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings, logger
from app.models.correlation import AnalysisDomain, CorrelationResult
from app.models.error_analysis import (
    ErrorAnalysisRequest,
    ErrorAnalysisResponse,
    ErrorAnalysisResult,
)
from app.models.normalization import NormalizedIncident
from app.services.langfuse_client import LangfuseClient
from app.services.prometheus_client import PrometheusClient
from app.services.event_bus import get_event_bus
from app.services.trace_store import TraceStore

# ── System Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are an Error Analysis Engine in a distributed observability system.

Your job is to perform a deep-dive error analysis on the component logs
identified by the upstream Correlation Agent. You analyze raw logs to
extract, categorize, and assess every error present.

You MUST:
- Identify every distinct error in the logs
- Categorize each error by functional category and severity
- Detect recurring error patterns across log entries
- Assess the impact of errors on each affected component
- Trace how errors propagated through the system
- Assign a unique error_id (ERR-001, ERR-002, ...) to each distinct error
- Base all findings strictly on log evidence

You MUST NOT:
- Perform root cause analysis (that is a separate agent's job)
- Provide recommendations or fixes
- Hallucinate errors not present in the logs
- Invent services or components
- Guess without evidence

## Rules
- Every error MUST have direct log evidence
- Severity is based on impact: critical (system down), high (major feature broken),
  medium (degraded performance), low (minor issue), info (informational anomaly)
- Patterns require 2+ occurrences of similar errors
- Error propagation path must be time-ordered
- If only one data source has errors, analysis may be shorter — that is OK
- If no external logs are available, analyze from correlation + normalization
  context alone. Set lower confidence when evidence is limited.
- Do not claim CPU saturation, memory pressure, or resource utilization metrics
  unless those exact metric entries are present. For latency-only evidence, use
  capacity/throughput saturation or performance degradation wording.

## Correlation Context
The upstream Correlation Agent identified:
- Analysis Target: {analysis_target}
- Correlation Chain: {correlation_chain}
- Root Cause Candidate: {root_cause_component} (confidence: {root_cause_confidence})
- Root Cause Reason: {root_cause_reason}

## Normalization Context
- Error Type: {error_type}
- Error Summary: {error_summary}
- Signals: {signals}
- Agent: {agent_name}

## Data Sources Analyzed
{data_sources_description}

## Output
Respond with ONLY a valid JSON object matching the schema below.
No markdown fences, no explanation — raw JSON only.

{schema}
"""


class ErrorAnalysisAgent:
    """
    Error Analysis Agent — takes the correlation output, routes to the
    correct data source(s) based on `analysis_target`, fetches logs,
    and uses GPT-4o to perform a detailed error analysis.

    Routing (based on correlation's analysis_target):
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

    async def analyze(self, request: ErrorAnalysisRequest) -> ErrorAnalysisResponse:
        """
        Full error analysis pipeline:
        1. Route to data source(s) based on analysis_target
        2. Fetch logs from Langfuse and/or Prometheus
        3. Build LLM prompt with correlation context + logs + schema
        4. Call GPT-4o
        5. Validate response against ErrorAnalysisResult model

        If external sources return no data, the agent still analyzes
        using the correlation and normalization context.
        """
        start = time.perf_counter()

        analysis_target = request.correlation.analysis_target

        # Step 1 & 2: Route and fetch logs
        all_logs, data_sources = await self._fetch_logs_by_target(
            analysis_target=analysis_target,
            trace_id=request.trace_id,
            timestamp=request.incident.timestamp,
            agent_name=request.agent_name,
        )

        if not all_logs:
            logger.warning(
                "No external logs fetched from %s for agent=%s (target=%s) — "
                "analyzing from correlation/normalization context only",
                data_sources, request.agent_name, analysis_target.value,
            )

        # Step 3: Build user message
        user_message = self._build_user_message(
            correlation=request.correlation,
            incident=request.incident,
            agent_name=request.agent_name,
            trace_id=request.trace_id,
            logs=all_logs,
            data_sources=data_sources,
        )

        # Step 4: Call GPT-4o
        raw_output = await self._call_llm(
            user_message=user_message,
            correlation=request.correlation,
            incident=request.incident,
            agent_name=request.agent_name,
            data_sources=data_sources,
        )

        # Step 5: Validate
        analysis = self._parse_and_validate(raw_output)

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "Error analysis complete | target=%s sources=%s errors=%d "
            "patterns=%d confidence=%.2f time=%.0fms",
            analysis_target.value,
            data_sources,
            len(analysis.errors),
            len(analysis.error_patterns),
            analysis.confidence,
            elapsed_ms,
        )

        return ErrorAnalysisResponse(
            analysis=analysis,
            rca_target=analysis_target,
            data_sources=data_sources,
            total_logs_analyzed=len(all_logs),
            processing_time_ms=round(elapsed_ms, 2),
        )

    # ── Data fetching with analysis_target routing ─────────────────────

    async def _fetch_logs_by_target(
        self,
        analysis_target: AnalysisDomain,
        trace_id: str | None,
        timestamp: str,
        agent_name: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Route to data source(s) based on analysis_target:
          - Agent       → Langfuse only
          - InfraLogs   → Prometheus only
          - Unknown     → Both Langfuse + Prometheus
        """
        all_logs: list[dict[str, Any]] = []
        data_sources: list[str] = []

        fetch_langfuse = analysis_target in (AnalysisDomain.AGENT, AnalysisDomain.UNKNOWN)
        fetch_prometheus = analysis_target in (AnalysisDomain.INFRA_LOGS, AnalysisDomain.UNKNOWN)

        # Trace timespan extracted from Langfuse (used to set Prometheus window)
        trace_start: str | None = None
        trace_end: str | None = None

        # ── Langfuse (Agent traces) ───────────────────────────────────
        if fetch_langfuse:
            if trace_id:
                logger.info(
                    "ErrorAnalysis | fetching Langfuse trace | target=%s trace_id=%s",
                    analysis_target.value, trace_id,
                )
                try:
                    langfuse_logs = await self._langfuse.fetch_trace(trace_id)
                    all_logs.extend(langfuse_logs)
                    if langfuse_logs:
                        data_sources.append("langfuse")
                    logger.info(
                        "ErrorAnalysis | Langfuse returned %d entries", len(langfuse_logs),
                    )
                    trace_start, trace_end = self._langfuse.extract_timespan(langfuse_logs)
                    logger.info(
                        "ErrorAnalysis | trace span: %s → %s", trace_start, trace_end,
                    )
                    await get_event_bus().publish(trace_id, {
                        "type": "logs_fetched", "agent": "error_analysis",
                        "source": "langfuse", "count": len(langfuse_logs),
                        "entries": langfuse_logs,
                    })
                    await TraceStore().save_fetched_logs(trace_id, "error_analysis", "langfuse", langfuse_logs)
                except Exception as exc:
                    logger.warning("ErrorAnalysis | Langfuse fetch failed: %s", exc)
                    err_entry = {
                        "timestamp": timestamp, "source": "langfuse", "service": "langfuse",
                        "message": f"Langfuse trace fetch failed: {exc}",
                        "level": "WARN", "metadata": {"error": str(exc), "trace_id": trace_id},
                    }
                    await get_event_bus().publish(trace_id, {
                        "type": "logs_fetched", "agent": "error_analysis",
                        "source": "langfuse", "count": 0, "entries": [err_entry],
                    })
                    await TraceStore().save_fetched_logs(trace_id, "error_analysis", "langfuse", [err_entry])
            else:
                logger.warning(
                    "ErrorAnalysis | target=%s requires Langfuse but no trace_id provided",
                    analysis_target.value,
                )
                missing_entry = {
                    "timestamp": timestamp,
                    "source": "langfuse",
                    "service": "langfuse",
                    "message": "No trace_id provided — cannot fetch Langfuse data",
                    "level": "WARN",
                    "metadata": {"error": "missing_trace_id"},
                }
                await TraceStore().save_fetched_logs("", "error_analysis", "langfuse", [missing_entry])

        # ── Prometheus (Infra metrics) ────────────────────────────────
        if fetch_prometheus:
            logger.info(
                "ErrorAnalysis | fetching Prometheus metrics | target=%s ts=%s agent=%s",
                analysis_target.value, timestamp, agent_name,
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
                    "ErrorAnalysis | Prometheus returned %d entries", len(prom_logs),
                )
                await get_event_bus().publish(trace_id or "", {
                    "type": "logs_fetched", "agent": "error_analysis",
                    "source": "prometheus", "count": len(prom_logs),
                    "entries": prom_logs,
                })
                await TraceStore().save_fetched_logs(trace_id or "", "error_analysis", "prometheus", prom_logs)
            except Exception as exc:
                logger.warning("ErrorAnalysis | Prometheus fetch failed: %s", exc)
                err_entry = {
                    "timestamp": timestamp, "source": "prometheus", "service": "prometheus",
                    "message": f"Prometheus query failed: {exc}",
                    "level": "WARN", "metadata": {"error": str(exc)},
                }
                await get_event_bus().publish(trace_id or "", {
                    "type": "logs_fetched", "agent": "error_analysis",
                    "source": "prometheus", "count": 0, "entries": [err_entry],
                })
                await TraceStore().save_fetched_logs(trace_id or "", "error_analysis", "prometheus", [err_entry])

        # Sort all logs by timestamp for chronological analysis
        all_logs.sort(key=lambda x: x.get("timestamp", ""))

        return all_logs, data_sources

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _build_response_schema() -> str:
        """Generate JSON schema from ErrorAnalysisResult Pydantic model."""
        schema = ErrorAnalysisResult.model_json_schema()
        return json.dumps(schema, indent=2)

    def _build_user_message(
        self,
        correlation: CorrelationResult,
        incident: NormalizedIncident,
        agent_name: str,
        trace_id: str | None,
        logs: list[dict[str, Any]],
        data_sources: list[str],
    ) -> str:
        """Build the user message with correlation context + all logs grouped by source."""
        lines: list[str] = [
            "## Incident Context",
            f"- Agent: {agent_name}",
            f"- Error Type: {incident.error_type.value}",
            f"- Error Summary: {incident.error_summary}",
            f"- Timestamp: {incident.timestamp}",
            f"- Signals: {', '.join(incident.signals) if incident.signals else 'none'}",
            f"- Analysis Target: {correlation.analysis_target.value}",
        ]
        if trace_id:
            lines.append(f"- Trace ID: {trace_id}")
        if incident.entities.agent_id:
            lines.append(f"- Entity Agent ID: {incident.entities.agent_id}")
        if incident.entities.service:
            lines.append(f"- Entity Service: {incident.entities.service}")

        lines.append("")

        # Correlation context
        lines.append("## Correlation Context")
        lines.append(f"- Correlation Chain: {' → '.join(correlation.correlation_chain)}")
        lines.append(f"- Root Cause Candidate: {correlation.root_cause_candidate.component}")
        lines.append(f"- Root Cause Confidence: {correlation.root_cause_candidate.confidence}")
        lines.append(f"- Root Cause Reason: {correlation.root_cause_candidate.reason}")

        if correlation.peer_components:
            lines.append("- Peer Components:")
            for pc in correlation.peer_components:
                lines.append(f"  - {pc.component} ({pc.role.value}): {pc.evidence}")

        if correlation.timeline:
            lines.append("- Timeline:")
            for te in correlation.timeline:
                lines.append(f"  - [{te.timestamp}] {te.service}: {te.event}")

        lines.append("")

        if not logs:
            lines.append("## External Logs: NONE AVAILABLE")
            lines.append("No logs were returned from external sources.")
            lines.append("Build the error analysis from the correlation and normalization context above.")
            lines.append("Set lower confidence since evidence is limited.")
        else:
            lines.append(f"## Data Sources: {', '.join(data_sources)}")
            lines.append("")

            # Group logs by source for clarity
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
        correlation: CorrelationResult,
        incident: NormalizedIncident,
        agent_name: str,
        data_sources: list[str],
    ) -> str:
        """Send the prompt to GPT-4o and return the raw JSON response."""
        logger.debug("Calling LLM for error analysis with %d chars", len(user_message))

        target = correlation.analysis_target
        if not data_sources:
            ds_desc = (
                "NO external log or metric entries were returned. "
                "Use the correlation and normalization context to identify errors. "
                "Do not cite Prometheus or Langfuse as evidence unless entries are present in the user message. "
                "Set confidence lower since evidence is limited."
            )
        elif target == AnalysisDomain.AGENT:
            ds_desc = (
                "Langfuse (AI agent traces/spans) only. "
                "Deep-dive into agent execution errors, LLM failures, tool call issues."
            )
        elif target == AnalysisDomain.INFRA_LOGS:
            ds_desc = (
                "Prometheus (infra metrics) only. "
                "Deep-dive into infrastructure errors, resource exhaustion, service health."
            )
        elif target == AnalysisDomain.UNKNOWN:
            ds_desc = (
                "Both Langfuse (AI agent traces) AND Prometheus (infra metrics). "
                "Analyze errors from both agent and infrastructure perspectives."
            )
        else:
            ds_desc = (
                "NO external log or metric entries were returned. "
                "Use the correlation and normalization context to identify errors. "
                "Do not cite Prometheus or Langfuse as evidence unless entries are present in the user message. "
                "Set confidence lower since evidence is limited."
            )

        system_prompt = SYSTEM_PROMPT.format(
            analysis_target=target.value,
            correlation_chain=" → ".join(correlation.correlation_chain),
            root_cause_component=correlation.root_cause_candidate.component,
            root_cause_confidence=correlation.root_cause_candidate.confidence,
            root_cause_reason=correlation.root_cause_candidate.reason,
            error_type=incident.error_type.value,
            error_summary=incident.error_summary,
            signals=", ".join(incident.signals) if incident.signals else "none",
            agent_name=agent_name,
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

        logger.debug("LLM error analysis response: %s", content[:200])
        return content

    @staticmethod
    def _parse_and_validate(raw_json: str) -> ErrorAnalysisResult:
        """Parse LLM output and validate against the Pydantic model."""
        try:
            data: dict[str, Any] = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            raise ValueError(f"LLM output is not valid JSON: {exc}") from exc

        return ErrorAnalysisResult.model_validate(data)
