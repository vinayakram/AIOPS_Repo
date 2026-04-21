from __future__ import annotations

import json
import time
from typing import Any

from openai import AsyncOpenAI

from app.core import get_settings, logger
from app.models.normalization import (
    DataSource,
    Entities,
    ErrorType,
    NormalizedIncident,
    NormalizationRequest,
    NormalizationResponse,
)
from app.services.langfuse_client import LangfuseClient
from app.services.prometheus_client import PrometheusClient
from app.services.event_bus import get_event_bus
from app.services.trace_store import TraceStore

# ── System Prompt ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a Log Normalization Agent in a distributed observability system.

Your job is to convert raw, unstructured logs into a STRICT structured incident representation.

You MUST NOT:
- Diagnose root cause
- Guess system-wide failures
- Correlate across services
- Add assumptions not present in logs

You MUST:
- Extract factual signals only
- Normalize error patterns
- Identify likely category of failure (NO_ERROR, AI_AGENT, INFRA, NETWORK, UNKNOWN)
- Preserve timestamps and entities exactly

## Rules
- No inference beyond logs
- Extract atomic signals only
- One error_type only (prefer INFRA > NETWORK > AI_AGENT when mixed)
- Confidence based on explicitness of logs
- If there are NO errors, warnings, or anomalies in the logs, set error_type to "NO_ERROR"
  and error_summary to "No error detected"

## Data Source
The logs below were fetched from: {data_source}

## Output
Respond with ONLY a valid JSON object matching the schema below.
No markdown fences, no explanation — raw JSON only.

{schema}
"""

# ── Log-level error indicators ─────────────────────────────────────────

import re

_ERROR_LEVELS = {"ERROR", "FATAL", "CRITICAL", "WARN", "WARNING"}

_ERROR_KEYWORDS = [
    "error", "fail", "failed", "failure", "exception", "timeout",
    "refused", "denied", "crash", "crashed", "oom", "killed",
    "unreachable", "unavailable", "rejected", "abort", "panic",
]

_PERFORMANCE_RULE_IDS = {
    "NFR-7",
    "NFR-7a",
    "NFR-7p95",
    "NFR-7p95a",
    "NFR-19",
}

_PERFORMANCE_ISSUE_KEYWORDS = (
    "latency",
    "response_time",
    "response time",
    "p95",
    "under_load",
    "under load",
    "slow",
    "degradation",
)

# Pre-compiled regex: matches keywords as whole words only
# e.g. "error" matches "Connection error" but NOT "error_rate=0"
_ERROR_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(kw) for kw in _ERROR_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _has_error_signals(logs: list[dict[str, Any]]) -> bool:
    """
    Quick scan of raw logs to check if ANY entry contains error signals.

    Detection strategy:
      1. Log level is ERROR / FATAL / CRITICAL / WARN / WARNING → error
      2. For logs with INFO/DEBUG/missing level, check message for
         error keywords as whole words (avoids metric name false positives
         like "error_rate=0")

    Returns False if all logs look clean → triggers NO_ERROR short-circuit.
    """
    for log in logs:
        # Primary check: explicit error level
        level = (log.get("level") or "").upper()
        if level in _ERROR_LEVELS:
            return True

        # Secondary check: keyword scan only for ambiguous levels
        # Skip for INFO/DEBUG — these are already classified as non-error
        # by the source (Prometheus client sets level based on metric value)
        if level not in {"INFO", "DEBUG"}:
            message = log.get("message") or ""
            if _ERROR_PATTERN.search(message):
                return True

    return False


def _has_performance_issue_hint(request: NormalizationRequest) -> bool:
    rule_id = (request.rule_id or "").strip()
    if rule_id in _PERFORMANCE_RULE_IDS:
        return True

    haystack = " ".join(
        [
            request.issue_type or "",
            request.title or "",
            request.description or "",
        ]
    ).lower()
    return any(keyword in haystack for keyword in _PERFORMANCE_ISSUE_KEYWORDS)


def _has_upstream_issue_hint(request: NormalizationRequest) -> bool:
    return any(
        [
            request.issue_type,
            request.rule_id,
            request.severity,
            request.title,
            request.description,
        ]
    )


def _incident_from_upstream_hint(
    request: NormalizationRequest,
    *,
    extra_signal: str = "",
) -> NormalizedIncident:
    haystack = " ".join(
        [
            request.issue_type or "",
            request.rule_id or "",
            request.title or "",
            request.description or "",
        ]
    ).lower()

    if _has_performance_issue_hint(request) or any(
        token in haystack for token in ("cpu", "memory", "storage", "resource", "saturation")
    ):
        error_type = ErrorType.INFRA
    elif any(token in haystack for token in ("network", "connection", "dns", "unreachable")):
        error_type = ErrorType.NETWORK
    elif any(token in haystack for token in ("llm", "query", "preprocessing", "ai_agent", "genai")):
        error_type = ErrorType.AI_AGENT
    else:
        error_type = ErrorType.UNKNOWN

    summary = (
        request.title
        or request.description
        or "Incident detected by upstream AIOps rule."
    )
    signals = [
        signal
        for signal in [
            request.rule_id,
            request.issue_type,
            extra_signal,
        ]
        if signal
    ]
    if _has_performance_issue_hint(request):
        signals.extend(["latency_slo_breach", "representative_trace_has_no_error_span"])

    return NormalizedIncident(
        error_type=error_type,
        error_summary=summary[:300],
        timestamp=request.timestamp,
        confidence=0.85,
        entities=Entities(
            agent_id=request.agent_name,
            service=request.agent_name,
            trace_id=request.trace_id,
        ),
        signals=signals,
    )


class NormalizationAgent:
    """
    Normalization Agent — accepts incident identifiers from the frontend,
    routes to the correct data source (Langfuse or Prometheus), fetches
    raw data, sends it to GPT-4o, and returns a Pydantic-validated
    NormalizedIncident matching the PRD schema.

    Routing logic:
      - trace_id provided   → Langfuse (AI agent traces)
      - trace_id absent     → Prometheus (infra metrics)

    If no errors/warnings are found in the fetched data, the agent
    short-circuits and returns NO_ERROR without calling the LLM.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model
        self._response_schema = self._build_response_schema()

        # Data source clients
        self._langfuse = LangfuseClient()
        self._prometheus = PrometheusClient()

    # ── Public API ─────────────────────────────────────────────────────

    async def normalize(self, request: NormalizationRequest) -> NormalizationResponse:
        """
        Full normalization pipeline:
        1. Route to Langfuse (trace_id) or Prometheus (timestamp)
        2. Fetch raw logs/metrics
        3. Check for error signals — if none, return NO_ERROR immediately
        4. Build LLM prompt with data + Pydantic-derived schema
        5. Call GPT-4o
        6. Validate response against NormalizedIncident model
        """
        start = time.perf_counter()

        # Step 1 & 2: Route and fetch. If the observability backend is rate
        # limited/unavailable, keep RCA alive by falling back to upstream issue
        # context supplied by AIopsTelemetry.
        try:
            raw_logs, data_source = await self._fetch_data(request)
        except Exception as exc:
            if not _has_upstream_issue_hint(request):
                raise

            data_source = DataSource.LANGFUSE if request.trace_id else DataSource.PROMETHEUS
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "Observability fetch failed | source=%s trace=%s rule=%s — using upstream issue context: %s",
                data_source.value,
                request.trace_id,
                request.rule_id,
                str(exc)[:200],
            )
            return NormalizationResponse(
                incident=_incident_from_upstream_hint(
                    request,
                    extra_signal="observability_fetch_failed",
                ),
                data_source=data_source,
                raw_log_count=0,
                processing_time_ms=round(elapsed_ms, 2),
            )

        # Publish raw log entries so the monitor can show them live
        source_key = data_source.value.lower()
        await get_event_bus().publish(request.trace_id or "", {
            "type": "logs_fetched", "agent": "normalization",
            "source": source_key,
            "count": len(raw_logs),
            "entries": raw_logs,
        })
        # Persist so they survive page refreshes
        await TraceStore().save_fetched_logs(request.trace_id or "", "normalization", source_key, raw_logs)

        # Step 3: No-error short-circuit. Performance/SLO incidents can have a
        # clean representative trace, so upstream NFR hints must keep RCA alive.
        has_error_signals = _has_error_signals(raw_logs)
        has_performance_hint = _has_performance_issue_hint(request)
        if (not raw_logs or not has_error_signals) and not has_performance_hint:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "No error signals detected | source=%s agent=%s — returning NO_ERROR",
                data_source.value, request.agent_name,
            )
            return NormalizationResponse(
                incident=NormalizedIncident(
                    error_type=ErrorType.NO_ERROR,
                    error_summary="No error detected",
                    timestamp=request.timestamp,
                    confidence=1.0,
                    entities=Entities(
                        agent_id=request.agent_name,
                        trace_id=request.trace_id,
                    ),
                    signals=[],
                ),
                data_source=data_source,
                raw_log_count=len(raw_logs),
                processing_time_ms=round(elapsed_ms, 2),
            )

        if has_performance_hint and not has_error_signals:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Performance incident hint detected | rule=%s issue_type=%s — continuing RCA",
                request.rule_id,
                request.issue_type,
            )
            return NormalizationResponse(
                incident=_incident_from_upstream_hint(request),
                data_source=data_source,
                raw_log_count=len(raw_logs),
                processing_time_ms=round(elapsed_ms, 2),
            )

        # Step 4: Build the user message
        user_message = self._build_user_message(
            timestamp=request.timestamp,
            trace_id=request.trace_id,
            agent_name=request.agent_name,
            issue_type=request.issue_type,
            rule_id=request.rule_id,
            severity=request.severity,
            title=request.title,
            description=request.description,
            data_source=data_source,
            logs=raw_logs,
        )

        # Step 5: Call GPT-4o
        raw_output = await self._call_llm(user_message, data_source)

        # Step 6: Parse and validate against Pydantic model
        incident = self._parse_and_validate(raw_output)

        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "Normalization complete | source=%s type=%s confidence=%.2f signals=%s time=%.0fms",
            data_source.value,
            incident.error_type.value,
            incident.confidence,
            incident.signals,
            elapsed_ms,
        )

        return NormalizationResponse(
            incident=incident,
            data_source=data_source,
            raw_log_count=len(raw_logs),
            processing_time_ms=round(elapsed_ms, 2),
        )

    # ── Data fetching with routing ─────────────────────────────────────

    async def _fetch_data(
        self, request: NormalizationRequest,
    ) -> tuple[list[dict[str, Any]], DataSource]:
        """
        Route to the correct backend:
          - trace_id provided → Langfuse
          - trace_id absent   → Prometheus
        """
        if request.trace_id:
            logger.info(
                "Routing to Langfuse | trace_id=%s agent=%s",
                request.trace_id, request.agent_name,
            )
            logs = await self._langfuse.fetch_trace(request.trace_id)
            return logs, DataSource.LANGFUSE
        else:
            logger.info(
                "Routing to Prometheus | timestamp=%s agent=%s",
                request.timestamp, request.agent_name,
            )
            logs = await self._prometheus.fetch_metrics(
                timestamp=request.timestamp,
                agent_name=request.agent_name,
            )
            return logs, DataSource.PROMETHEUS

    # ── Private helpers ────────────────────────────────────────────────

    @staticmethod
    def _build_response_schema() -> str:
        """
        Generate a JSON schema string from the NormalizedIncident Pydantic
        model. Injected into the system prompt so the LLM knows the exact
        output contract without hardcoding JSON in the prompt.
        """
        schema = NormalizedIncident.model_json_schema()
        return json.dumps(schema, indent=2)

    def _build_user_message(
        self,
        timestamp: str,
        trace_id: str | None,
        agent_name: str,
        issue_type: str | None,
        rule_id: str | None,
        severity: str | None,
        title: str | None,
        description: str | None,
        data_source: DataSource,
        logs: list[dict[str, Any]],
    ) -> str:
        """Format incident context + raw logs into a prompt-ready string."""
        lines: list[str] = [
            "## Incident Context",
            f"- Timestamp: {timestamp}",
            f"- Agent Name: {agent_name}",
            f"- Data Source: {data_source.value}",
        ]
        if trace_id:
            lines.append(f"- Trace ID: {trace_id}")
        if rule_id or issue_type or title or description:
            lines.extend(
                [
                    f"- Upstream Rule ID: {rule_id or 'N/A'}",
                    f"- Upstream Issue Type: {issue_type or 'N/A'}",
                    f"- Upstream Severity: {severity or 'N/A'}",
                    f"- Upstream Title: {title or 'N/A'}",
                    f"- Upstream Description: {description or 'N/A'}",
                ]
            )

        lines.append("")
        lines.append(f"## Raw Data ({data_source.value})")

        for i, log in enumerate(logs, 1):
            parts = [f"[{i}]"]
            if log.get("timestamp"):
                parts.append(f"ts={log['timestamp']}")
            if log.get("source"):
                parts.append(f"src={log['source']}")
            if log.get("service"):
                parts.append(f"svc={log['service']}")
            if log.get("level"):
                parts.append(f"level={log['level']}")
            parts.append(f"msg={log['message']}")
            if log.get("metadata"):
                parts.append(f"meta={json.dumps(log['metadata'])}")
            lines.append(" | ".join(parts))

        return "\n".join(lines)

    async def _call_llm(self, user_message: str, data_source: DataSource) -> str:
        """Send the prompt to GPT-4o and return the raw JSON response."""
        logger.debug("Calling LLM with %d chars (source=%s)", len(user_message), data_source.value)

        system_prompt = SYSTEM_PROMPT.format(
            schema=self._response_schema,
            data_source=data_source.value,
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

        logger.debug("LLM response: %s", content[:200])
        return content

    @staticmethod
    def _parse_and_validate(raw_json: str) -> NormalizedIncident:
        """Parse LLM output and validate against the Pydantic model."""
        try:
            data: dict[str, Any] = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned invalid JSON: %s", exc)
            raise ValueError(f"LLM output is not valid JSON: {exc}") from exc

        return NormalizedIncident.model_validate(data)
