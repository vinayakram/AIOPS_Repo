from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core import get_settings, logger


# ── Default PromQL queries for common failure signals ──────────────────

DEFAULT_QUERIES: list[dict[str, str]] = [
    {
        "name": "sample_agent_query_p95_latency",
        "query": 'histogram_quantile(0.95, sum(rate(sample_agent_query_duration_seconds_bucket{{app="{agent}"}}[{window}])) by (le))',
        "description": "SampleAgent p95 RAG query latency",
    },
    {
        "name": "sample_agent_query_p99_latency",
        "query": 'histogram_quantile(0.99, sum(rate(sample_agent_query_duration_seconds_bucket{{app="{agent}"}}[{window}])) by (le))',
        "description": "SampleAgent p99 RAG query latency",
    },
    {
        "name": "sample_agent_query_rate",
        "query": 'sum(rate(sample_agent_query_requests_total{{app="{agent}"}}[{window}]))',
        "description": "SampleAgent RAG query throughput",
    },
    {
        "name": "sample_agent_query_errors",
        "query": 'sum(increase(sample_agent_query_requests_total{{app="{agent}",status="error"}}[{window}]))',
        "description": "SampleAgent RAG query errors",
    },
    {
        "name": "sample_agent_current_concurrency",
        "query": 'max(sample_agent_query_in_flight{{app="{agent}"}})',
        "description": "SampleAgent current in-flight RAG queries",
    },
    {
        "name": "sample_agent_max_concurrency",
        "query": 'max(sample_agent_query_max_concurrency{{app="{agent}"}})',
        "description": "SampleAgent maximum observed concurrent RAG queries",
    },
    {
        "name": "sample_agent_articles_p95",
        "query": 'histogram_quantile(0.95, sum(rate(sample_agent_query_articles_fetched_bucket{{app="{agent}"}}[{window}])) by (le))',
        "description": "SampleAgent p95 articles fetched per query",
    },
    {
        "name": "sample_agent_llm_request_rate",
        "query": 'sum(rate(sample_agent_llm_requests_total{{app="{agent}"}}[{window}])) by (scenario, deployment, model, status)',
        "description": "SampleAgent LLM request rate by scenario and status",
    },
    {
        "name": "sample_agent_llm_rate_limited_total",
        "query": 'sum(increase(sample_agent_llm_requests_total{{app="{agent}",status="rate_limited"}}[{window}])) by (scenario, deployment, model)',
        "description": "SampleAgent LLM requests rate-limited in the query window",
    },
    {
        "name": "sample_agent_llm_current_window_hits",
        "query": 'max(sample_agent_llm_rate_limit_current_window_hits{{app="{agent}"}}) by (scenario, deployment)',
        "description": "SampleAgent LLM current rolling-window request count",
    },
    {
        "name": "sample_agent_llm_limit_per_minute",
        "query": 'max(sample_agent_llm_rate_limit_per_minute{{app="{agent}"}}) by (scenario, deployment)',
        "description": "SampleAgent LLM configured requests-per-minute limit",
    },
    {
        "name": "sample_agent_llm_remaining",
        "query": 'min(sample_agent_llm_rate_limit_remaining{{app="{agent}"}}) by (scenario, deployment)',
        "description": "SampleAgent LLM remaining requests before rate limit",
    },
    {
        "name": "sample_agent_pod_cpu_utilisation",
        "query": 'max_over_time(sample_agent_pod_cpu_utilisation_percent{{app="{agent}"}}[{window}])',
        "description": "SampleAgent pod CPU utilisation over the last query window",
    },
    {
        "name": "sample_agent_pod_cpu_threshold",
        "query": 'max_over_time(sample_agent_pod_cpu_threshold_percent{{app="{agent}"}}[{window}])',
        "description": "SampleAgent configured pod CPU threshold",
    },
    {
        "name": "sample_agent_pod_memory_utilisation",
        "query": 'max_over_time(sample_agent_pod_memory_utilisation_percent{{app="{agent}"}}[{window}])',
        "description": "SampleAgent pod memory utilisation over the last query window",
    },
    {
        "name": "sample_agent_pod_threshold_breaches",
        "query": 'increase(sample_agent_pod_threshold_breaches_total[{window}])',
        "description": "SampleAgent pod threshold breach count in the query window",
    },
    {
        "name": "dependent_agent_upstream_errors",
        "query": 'sum(increase(dependent_agent_upstream_requests_total{{upstream="sample-agent",result=~"upstream_error|upstream_timeout|upstream_unreachable"}}[{window}]))',
        "description": "Dependent agent upstream failures attributed to sample-agent",
    },
    {
        "name": "dependent_agent_cascade_failures",
        "query": 'sum(increase(dependent_agent_cascade_failures_total{{upstream="sample-agent"}}[{window}]))',
        "description": "Dependent agent cascade failure count for sample-agent dependency",
    },
    {
        "name": "dependent_agent_last_upstream_status",
        "query": 'min_over_time(dependent_agent_last_upstream_status{{app=~"{agent}|triage-agent",upstream="sample-agent"}}[{window}])',
        "description": "Dependent agent last observed sample-agent upstream status",
    },
    {
        "name": "dependent_agent_upstream_p95_latency",
        "query": 'histogram_quantile(0.95, sum(rate(dependent_agent_upstream_request_duration_seconds_bucket{{upstream="sample-agent"}}[{window}])) by (le))',
        "description": "Dependent agent p95 latency calling sample-agent",
    },
    {
        "name": "dependent_agent_http_5xx",
        "query": 'sum(increase(dependent_agent_http_requests_total{{status=~"5.."}}[{window}]))',
        "description": "Dependent agent HTTP 5xx responses",
    },
    {
        "name": "cross_service_sample_agent_guard_active",
        "query": 'max_over_time(sample_agent_pod_threshold_breaches_total{{reason!=""}}[{window}])',
        "description": "Cross-service evidence that sample-agent resource guard has breached",
    },
    {
        "name": "cross_service_sample_agent_http_503",
        "query": 'sum(increase(sample_agent_http_requests_total{{app="sample-agent",status="503"}}[{window}]))',
        "description": "Sample-agent HTTP 503 responses during the incident window",
    },
    {
        "name": "cadvisor_sample_agent_cpu_limit_usage_percent",
        "query": '100 * sum by (name) (rate(container_cpu_usage_seconds_total{name=~".*sample-agent-pod.*|.*{agent}.*",image!="",cpu="total"}[{window}])) / (max by (name) (container_spec_cpu_quota{name=~".*sample-agent-pod.*|.*{agent}.*",image!=""}) / max by (name) (container_spec_cpu_period{name=~".*sample-agent-pod.*|.*{agent}.*",image!=""}))',
        "description": "cAdvisor Sample Agent container CPU usage as a percentage of its configured CPU limit",
    },
    {
        "name": "cadvisor_sample_agent_cpu_core_usage_percent",
        "query": 'sum(rate(container_cpu_usage_seconds_total{name=~".*sample-agent-pod.*|.*{agent}.*",image!="",cpu="total"}[{window}])) * 100',
        "description": "cAdvisor Sample Agent container CPU usage as percentage of one CPU core",
    },
    {
        "name": "cadvisor_sample_agent_memory_working_set_bytes",
        "query": 'max(container_memory_working_set_bytes{name=~".*sample-agent-pod.*|.*{agent}.*",image!=""})',
        "description": "cAdvisor container memory working set for the Sample Agent Docker container",
    },
    {
        "name": "cadvisor_sample_agent_memory_limit_bytes",
        "query": 'max(container_spec_memory_limit_bytes{name=~".*sample-agent-pod.*|.*{agent}.*",image!=""})',
        "description": "cAdvisor configured memory limit for the Sample Agent Docker container",
    },
    {
        "name": "error_rate",
        "query": 'sum(rate(http_requests_total{{status=~"5..",job=~".*{agent}.*"}}[{window}]))',
        "description": "HTTP 5xx error rate",
    },
    {
        "name": "latency_p99",
        "query": 'histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{{job=~".*{agent}.*"}}[{window}])) by (le))',
        "description": "P99 request latency",
    },
    {
        "name": "up_status",
        "query": 'up{{job=~".*{agent}.*"}}',
        "description": "Target up/down status",
    },
    {
        "name": "memory_usage",
        "query": 'container_memory_usage_bytes{{pod=~".*{agent}.*"}}',
        "description": "Container memory usage",
    },
    {
        "name": "restart_count",
        "query": 'kube_pod_container_status_restarts_total{{pod=~".*{agent}.*"}}',
        "description": "Pod restart count",
    },
    {
        "name": "dns_failures",
        "query": 'sum(rate(coredns_dns_responses_total{{rcode="SERVFAIL"}}[{window}]))',
        "description": "DNS SERVFAIL rate",
    },
]


class PrometheusClient:
    """
    Fetches metrics from Prometheus using PromQL range queries.

    Queries a ±window around the incident timestamp to capture
    anomalous signals near the time of failure.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = settings.prometheus_url.rstrip("/")
        self._window = settings.prometheus_query_window
        self._buffer_seconds = settings.prometheus_buffer_seconds

    async def fetch_metrics(
        self,
        timestamp: str,
        agent_name: str,
        trace_start: str | None = None,
        trace_end: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run default PromQL queries and return results as log-like dicts.

        If trace_start and trace_end are provided (from a Langfuse trace),
        the query window spans the full trace duration with a configurable
        buffer on each side: (trace_start - buffer) to (trace_end + buffer).

        If not provided, falls back to: timestamp ± buffer.

        Args:
            timestamp:   ISO-8601 incident timestamp (fallback anchor)
            agent_name:  Agent name used to template PromQL filters
            trace_start: ISO-8601 trace start time from Langfuse (optional)
            trace_end:   ISO-8601 trace end time from Langfuse (optional)
        """
        if trace_start and trace_end:
            logger.info(
                "Prometheus | fetching metrics for agent=%s "
                "trace_window=[%s → %s] buffer=%ds",
                agent_name, trace_start, trace_end, self._buffer_seconds,
            )
        else:
            logger.info(
                "Prometheus | fetching metrics for agent=%s ts=%s buffer=%ds",
                agent_name, timestamp, self._buffer_seconds,
            )

        start, end = self._compute_time_range(
            timestamp=timestamp,
            buffer_seconds=self._buffer_seconds,
            trace_start=trace_start,
            trace_end=trace_end,
        )
        logs: list[dict[str, Any]] = []

        for qdef in DEFAULT_QUERIES:
            promql = qdef["query"].format(agent=agent_name, window=self._window)

            try:
                result = await self._range_query(promql, start, end, step="15s")
                entries = self._result_to_logs(
                    query_name=qdef["name"],
                    description=qdef["description"],
                    promql=promql,
                    result=result,
                    agent_name=agent_name,
                )
                logs.extend(entries)
            except Exception as exc:
                logger.warning(
                    "Prometheus | query '%s' failed: %s", qdef["name"], exc,
                )
                logs.append({
                    "timestamp": timestamp,
                    "source": "prometheus",
                    "service": agent_name,
                    "message": f"Prometheus query '{qdef['name']}' failed: {exc}",
                    "level": "WARN",
                    "metadata": {"query": promql, "error": str(exc)},
                })

        logger.info("Prometheus | got %d log entries for agent=%s", len(logs), agent_name)
        return logs

    # ── HTTP helper ────────────────────────────────────────────────────

    async def _range_query(
        self,
        query: str,
        start: str,
        end: str,
        step: str = "15s",
    ) -> dict[str, Any]:
        """POST /api/v1/query_range"""
        url = f"{self._base_url}/api/v1/query_range"
        params = {"query": query, "start": start, "end": end, "step": step}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "success":
                raise ValueError(f"Prometheus returned status={data.get('status')}")

            return data.get("data", {})

    # ── Time range computation ─────────────────────────────────────────

    @staticmethod
    def _compute_time_range(
        timestamp: str,
        buffer_seconds: int = 300,
        trace_start: str | None = None,
        trace_end: str | None = None,
    ) -> tuple[str, str]:
        """
        Compute Prometheus query (start, end) window.

        Mode 1 — trace span (preferred when Langfuse data is available):
          window = (trace_start - buffer_seconds) to (trace_end + buffer_seconds)
          Captures the full trace duration plus context on both sides.

        Mode 2 — single timestamp fallback (no Langfuse trace):
          window = (timestamp - buffer_seconds) to (timestamp + buffer_seconds)
        """
        buf = timedelta(seconds=buffer_seconds)

        if trace_start and trace_end:
            ts_s = trace_start.replace("Z", "+00:00")
            ts_e = trace_end.replace("Z", "+00:00")
            dt_start = datetime.fromisoformat(ts_s)
            dt_end = datetime.fromisoformat(ts_e)
            if dt_start.tzinfo is None:
                dt_start = dt_start.replace(tzinfo=timezone.utc)
            if dt_end.tzinfo is None:
                dt_end = dt_end.replace(tzinfo=timezone.utc)
            return (dt_start - buf).isoformat(), (dt_end + buf).isoformat()

        # Fallback: single timestamp ± buffer
        ts = timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (dt - buf).isoformat(), (dt + buf).isoformat()

    # ── Transform to log format ────────────────────────────────────────

    @staticmethod
    def _result_to_logs(
        query_name: str,
        description: str,
        promql: str,
        result: dict[str, Any],
        agent_name: str,
    ) -> list[dict[str, Any]]:
        """
        Convert Prometheus range query result into flat log entries.
        Picks the latest value from each time series.
        """
        logs: list[dict[str, Any]] = []
        result_type = result.get("resultType", "matrix")
        series_list = result.get("result", [])

        if not series_list:
            return []

        for series in series_list:
            metric_labels = series.get("metric", {})
            values = series.get("values", [])

            if not values:
                continue

            # Use the last data point in the range
            ts_unix, value = values[-1]
            ts_iso = datetime.fromtimestamp(
                float(ts_unix), tz=timezone.utc
            ).isoformat()

            # Determine severity from the metric
            level = "INFO"
            try:
                numeric_val = float(value)
                if query_name == "error_rate" and numeric_val > 0:
                    level = "ERROR"
                elif query_name == "up_status" and numeric_val == 0:
                    level = "ERROR"
                elif query_name == "restart_count" and numeric_val > 0:
                    level = "WARN"
                elif query_name in {
                    "dependent_agent_upstream_errors",
                    "dependent_agent_cascade_failures",
                    "dependent_agent_http_5xx",
                    "cross_service_sample_agent_http_503",
                } and numeric_val > 0:
                    level = "ERROR"
                elif query_name == "dependent_agent_last_upstream_status" and numeric_val == 0:
                    level = "ERROR"
                elif query_name == "cross_service_sample_agent_guard_active" and numeric_val > 0:
                    level = "WARN"
            except (ValueError, TypeError):
                pass

            message = f"{description}: {query_name}={value}"
            if metric_labels:
                label_str = ", ".join(f"{k}={v}" for k, v in metric_labels.items())
                message += f" [{label_str}]"

            logs.append({
                "timestamp": ts_iso,
                "source": "prometheus",
                "service": metric_labels.get("job", agent_name),
                "message": message,
                "level": level,
                "metadata": {
                    "query_name": query_name,
                    "promql": promql,
                    "result_type": result_type,
                    "value": value,
                    "labels": metric_labels,
                    "sample_count": len(values),
                },
            })

        return logs
