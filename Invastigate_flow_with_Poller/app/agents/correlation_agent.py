from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings, logger
from app.models.correlation import (
    CorrelationRequest,
    CorrelationResponse,
    CorrelationResult,
)
from app.models.normalization import NormalizedIncident
from app.services.langfuse_client import LangfuseClient
from app.services.prometheus_client import PrometheusClient
from app.services.event_bus import get_event_bus
from app.services.trace_store import TraceStore

# ── System Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a Correlation Engine for a distributed observability system.

Your job is to analyze logs across systems and build a causal failure graph.

You MUST:
- Identify failure propagation chain
- Detect upstream → downstream dependency flow
- Identify earliest failure node
- Identify peer component responsible for initiating failure

You MUST NOT:
- Hallucinate services
- Invent logs
- Guess without evidence

## Rules
- Causality is time-based only
- Earliest failure = root candidate
- Peer must be evidence-backed
- No hallucinated edges
- If only one data source has issues, correlation chain may be short — that is OK
- Use the normalization summary to understand the error context
- If no external logs are available, build the correlation from the normalization
  context alone (error_type, error_summary, signals, entities). Set lower confidence
  when evidence is limited.

## Normalization Context
The following normalized incident was produced by the upstream Normalization Agent:

Error Type: {error_type}
Error Summary: {error_summary}
Signals: {signals}
Agent: {agent_name}

## Data Sources Analyzed
{data_sources_description}

## Output
Respond with ONLY a valid JSON object matching the schema below.
No markdown fences, no explanation — raw JSON only.

{schema}
"""


class CorrelationAgent:
    """
    Correlation Agent — takes the normalization output, fetches logs from
    the appropriate backends, and uses GPT-4o to build a causal failure
    graph with timeline, peer components, and root cause hypothesis.

    Routing:
      - trace_id present  → Langfuse logs + Prometheus metrics
      - trace_id absent   → Prometheus metrics only
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._response_schema = self._build_response_schema()

        self._langfuse = LangfuseClient()
        self._prometheus = PrometheusClient()

    # ── Public API ─────────────────────────────────────────────────────

    async def correlate(self, request: CorrelationRequest) -> CorrelationResponse:
        """
        Full correlation pipeline:
        1. Fetch logs from Langfuse (if trace_id) and Prometheus
        2. Combine all logs into a single dataset
        3. Build LLM prompt with normalization context + logs + schema
        4. Call GPT-4o
        5. Validate response against CorrelationResult model

        If external sources return no data, the agent still correlates
        using the normalization output (error_type, signals, entities).
        """
        start = time.perf_counter()

        # Step 1 & 2: Fetch and combine logs
        all_logs, data_sources = await self._fetch_all_logs(request)

        if not all_logs:
            logger.warning(
                "No external logs fetched from %s for agent=%s — "
                "correlating from normalization context only",
                data_sources, request.agent_name,
            )

        # Step 3: Build user message
        user_message = self._build_user_message(
            incident=request.incident,
            agent_name=request.agent_name,
            trace_id=request.trace_id,
            logs=all_logs,
            data_sources=data_sources,
        )

        # Step 4: Call GPT-4o
        raw_output = await self._call_llm(
            user_message=user_message,
            incident=request.incident,
            agent_name=request.agent_name,
            data_sources=data_sources,
        )

        # Step 5: Validate
        correlation = self._parse_and_validate(raw_output)

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "Correlation complete | sources=%s chain=%s root=%s confidence=%.2f time=%.0fms",
            data_sources,
            correlation.correlation_chain,
            correlation.root_cause_candidate.component,
            correlation.root_cause_candidate.confidence,
            elapsed_ms,
        )

        return CorrelationResponse(
            correlation=correlation,
            data_sources=data_sources,
            total_logs_analyzed=len(all_logs),
            processing_time_ms=round(elapsed_ms, 2),
        )

    # ── Data fetching ──────────────────────────────────────────────────

    async def _fetch_all_logs(
        self, request: CorrelationRequest,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """
        Fetch logs from appropriate backends:
          - trace_id present  → Langfuse + Prometheus
          - trace_id absent   → Prometheus only
        """
        all_logs: list[dict[str, Any]] = []
        data_sources: list[str] = []
        timestamp = request.incident.timestamp
        agent_name = request.agent_name

        # Trace timespan extracted from Langfuse (used to set Prometheus window)
        trace_start: str | None = None
        trace_end: str | None = None

        # Fetch Langfuse first when trace_id is available so the trace span
        # can be used to set the correct Prometheus query window
        if request.trace_id:
            logger.info(
                "Correlation | fetching Langfuse trace | trace_id=%s",
                request.trace_id,
            )
            try:
                langfuse_logs = await self._langfuse.fetch_trace(request.trace_id)
                all_logs.extend(langfuse_logs)
                data_sources.append("langfuse")
                logger.info("Correlation | Langfuse returned %d entries", len(langfuse_logs))
                trace_start, trace_end = self._langfuse.extract_timespan(langfuse_logs)
                logger.info(
                    "Correlation | trace span: %s → %s", trace_start, trace_end,
                )
                await get_event_bus().publish(request.trace_id, {
                    "type": "logs_fetched", "agent": "correlation",
                    "source": "langfuse", "count": len(langfuse_logs),
                    "entries": langfuse_logs,
                })
                await TraceStore().save_fetched_logs(request.trace_id, "correlation", "langfuse", langfuse_logs)
            except Exception as exc:
                logger.warning("Correlation | Langfuse fetch failed: %s", exc)
                err_entry = {
                    "timestamp": timestamp, "source": "langfuse", "service": "langfuse",
                    "message": f"Langfuse trace fetch failed: {exc}",
                    "level": "WARN", "metadata": {"error": str(exc), "trace_id": request.trace_id},
                }
                all_logs.append(err_entry)
                data_sources.append("langfuse")
                await get_event_bus().publish(request.trace_id, {
                    "type": "logs_fetched", "agent": "correlation",
                    "source": "langfuse", "count": 0, "entries": [err_entry],
                })
                await TraceStore().save_fetched_logs(request.trace_id, "correlation", "langfuse", [err_entry])

        # Fetch Prometheus — use trace span window when available
        logger.info(
            "Correlation | fetching Prometheus metrics | ts=%s agent=%s",
            timestamp, agent_name,
        )
        try:
            prom_logs = await self._prometheus.fetch_metrics(
                timestamp=timestamp,
                agent_name=agent_name,
                trace_start=trace_start,
                trace_end=trace_end,
            )
            all_logs.extend(prom_logs)
            data_sources.append("prometheus")
            logger.info("Correlation | Prometheus returned %d entries", len(prom_logs))
            await get_event_bus().publish(request.trace_id or "", {
                "type": "logs_fetched", "agent": "correlation",
                "source": "prometheus", "count": len(prom_logs),
                "entries": prom_logs,
            })
            await TraceStore().save_fetched_logs(request.trace_id or "", "correlation", "prometheus", prom_logs)
        except Exception as exc:
            logger.warning("Correlation | Prometheus fetch failed: %s", exc)
            err_entry = {
                "timestamp": timestamp, "source": "prometheus", "service": "prometheus",
                "message": f"Prometheus query failed: {exc}",
                "level": "WARN", "metadata": {"error": str(exc)},
            }
            all_logs.append(err_entry)
            data_sources.append("prometheus")
            await get_event_bus().publish(request.trace_id or "", {
                "type": "logs_fetched", "agent": "correlation",
                "source": "prometheus", "count": 0, "entries": [err_entry],
            })
            await TraceStore().save_fetched_logs(request.trace_id or "", "correlation", "prometheus", [err_entry])

        # Sort all logs by timestamp for chronological analysis
        all_logs.sort(key=lambda x: x.get("timestamp", ""))

        return all_logs, data_sources

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _build_response_schema() -> str:
        """Generate JSON schema from CorrelationResult Pydantic model."""
        schema = CorrelationResult.model_json_schema()
        return json.dumps(schema, indent=2)

    def _build_user_message(
        self,
        incident: NormalizedIncident,
        agent_name: str,
        trace_id: str | None,
        logs: list[dict[str, Any]],
        data_sources: list[str],
    ) -> str:
        """Build the user message with all logs grouped by source."""
        lines: list[str] = [
            "## Incident Context (from Normalization Agent)",
            f"- Agent: {agent_name}",
            f"- Error Type: {incident.error_type.value}",
            f"- Error Summary: {incident.error_summary}",
            f"- Timestamp: {incident.timestamp}",
            f"- Signals: {', '.join(incident.signals) if incident.signals else 'none'}",
        ]
        if trace_id:
            lines.append(f"- Trace ID: {trace_id}")
        if incident.entities.agent_id:
            lines.append(f"- Entity Agent ID: {incident.entities.agent_id}")
        if incident.entities.service:
            lines.append(f"- Entity Service: {incident.entities.service}")

        lines.append("")

        if not logs:
            lines.append("## External Logs: NONE AVAILABLE")
            lines.append("No logs were returned from external sources (Prometheus/Langfuse).")
            lines.append("Build the correlation analysis from the normalization context above.")
            lines.append("Set lower confidence since evidence is limited to normalization output only.")
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
        incident: NormalizedIncident,
        agent_name: str,
        data_sources: list[str],
    ) -> str:
        """Send the prompt to GPT-4o and return the raw JSON response."""
        logger.debug("Calling LLM for correlation with %d chars", len(user_message))

        # Build data sources description for the system prompt
        if "langfuse" in data_sources and "prometheus" in data_sources:
            ds_desc = "Langfuse (AI agent traces/spans) AND Prometheus (infra metrics). Correlate across both."
        elif "langfuse" in data_sources:
            ds_desc = "Langfuse (AI agent traces/spans) only. No Prometheus metrics available."
        elif "prometheus" in data_sources:
            ds_desc = "Prometheus (infra metrics) only. No AI agent trace data available."
        else:
            ds_desc = (
                "NO external log sources returned data. "
                "Use only the normalization context above to build the correlation. "
                "Set confidence lower since evidence is limited."
            )

        system_prompt = SYSTEM_PROMPT.format(
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

        logger.debug("LLM correlation response: %s", content[:200])
        return content

    @staticmethod
    def _parse_and_validate(raw_json: str) -> CorrelationResult:
        """Parse LLM output and validate against the Pydantic model."""
        try:
            data: dict[str, Any] = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            raise ValueError(f"LLM output is not valid JSON: {exc}") from exc

        return CorrelationResult.model_validate(data)
