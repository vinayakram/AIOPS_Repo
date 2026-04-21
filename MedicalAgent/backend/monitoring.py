from __future__ import annotations

import math
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from starlette.responses import Response


APP_NAME = "medical-rag"
CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
REQUEST_DURATION_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89)
QUERY_DURATION_BUCKETS = (0.5, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144)
ARTICLES_FETCHED_BUCKETS = (0, 1, 5, 10, 20, 30, 50, 75, 100)

_lock = threading.Lock()
_http_counts: dict[tuple[str, str, str, str], int] = {}
_http_histograms: dict[tuple[str, str, str, str], list[float]] = {}
_query_counts: dict[tuple[str, str], int] = {}
_query_histograms: dict[tuple[str, str], list[float]] = {}
_articles_histograms: dict[tuple[str], list[float]] = {}
_llm_counts: dict[tuple[str, str, str, str], int] = {}
_llm_rate_state: dict[tuple[str, str, str], dict[str, float]] = {}
_pod_resource_state: dict[str, float] = {}
_pod_threshold_breaches: dict[tuple[str], int] = {}
_current_query_concurrency = 0
_max_query_concurrency = 0


def metrics_response() -> Response:
    return Response(_render_metrics(), media_type=CONTENT_TYPE_LATEST)


def _normalize_path(path: str) -> str:
    if path == "/api/query":
        return path
    if path in {"/api/health", "/metrics"}:
        return path
    if path.startswith("/static/"):
        return "/static/*"
    return path


def observe_http_request(method: str, path: str, status_code: int, duration_seconds: float) -> None:
    key = (APP_NAME, method, _normalize_path(path), str(status_code))
    with _lock:
        _http_counts[key] = _http_counts.get(key, 0) + 1
        _http_histograms.setdefault(key, []).append(duration_seconds)


@contextmanager
def track_query() -> Iterator[None]:
    global _current_query_concurrency, _max_query_concurrency

    with _lock:
        _current_query_concurrency += 1
        _max_query_concurrency = max(_max_query_concurrency, _current_query_concurrency)
    try:
        yield
    finally:
        with _lock:
            _current_query_concurrency = max(0, _current_query_concurrency - 1)


def observe_query(started_at: float, status: str, total_fetched: int | None = None) -> None:
    elapsed = time.perf_counter() - started_at
    query_key = (APP_NAME, status)
    with _lock:
        _query_counts[query_key] = _query_counts.get(query_key, 0) + 1
        _query_histograms.setdefault(query_key, []).append(elapsed)
        if status == "success" and total_fetched is not None:
            _articles_histograms.setdefault((APP_NAME,), []).append(float(total_fetched))


def observe_llm_request(scenario: str, deployment: str, model: str, status: str) -> None:
    key = (APP_NAME, scenario, deployment, model, status)
    with _lock:
        _llm_counts[key] = _llm_counts.get(key, 0) + 1


def set_llm_rate_limit_state(
    scenario: str,
    deployment: str,
    model: str,
    current_window_hits: int,
    limit_per_minute: int,
    remaining: int,
) -> None:
    key = (APP_NAME, scenario, deployment)
    with _lock:
        _llm_rate_state[key] = {
            "current_window_hits": float(current_window_hits),
            "limit_per_minute": float(limit_per_minute),
            "remaining": float(remaining),
        }


def observe_pod_resource_sample(
    cpu_percent: float,
    cpu_threshold_percent: float,
    memory_percent: float | None,
    memory_threshold_percent: float,
) -> None:
    with _lock:
        _pod_resource_state["cpu_percent"] = float(cpu_percent)
        _pod_resource_state["cpu_threshold_percent"] = float(cpu_threshold_percent)
        if memory_percent is not None:
            _pod_resource_state["memory_percent"] = float(memory_percent)
        _pod_resource_state["memory_threshold_percent"] = float(memory_threshold_percent)


def observe_pod_threshold_breach(reason: str) -> None:
    key = (reason[:160],)
    with _lock:
        _pod_threshold_breaches[key] = _pod_threshold_breaches.get(key, 0) + 1


def _render_metrics() -> str:
    with _lock:
        http_counts = dict(_http_counts)
        http_histograms = {k: list(v) for k, v in _http_histograms.items()}
        query_counts = dict(_query_counts)
        query_histograms = {k: list(v) for k, v in _query_histograms.items()}
        articles_histograms = {k: list(v) for k, v in _articles_histograms.items()}
        llm_counts = dict(_llm_counts)
        llm_rate_state = {k: dict(v) for k, v in _llm_rate_state.items()}
        pod_resource_state = dict(_pod_resource_state)
        pod_threshold_breaches = dict(_pod_threshold_breaches)
        current_concurrency = _current_query_concurrency
        max_concurrency = _max_query_concurrency

    lines: list[str] = []
    _append_counter(lines, "medical_rag_http_requests_total", "Total HTTP requests served by MedicalAgent.", http_counts, ("app", "method", "path", "status"))
    _append_histogram(lines, "medical_rag_http_request_duration_seconds", "HTTP request latency for MedicalAgent.", http_histograms, REQUEST_DURATION_BUCKETS, ("app", "method", "path", "status"))
    _append_counter(lines, "medical_rag_query_requests_total", "Total MedicalAgent RAG query requests.", query_counts, ("app", "status"))
    _append_histogram(lines, "medical_rag_query_duration_seconds", "RAG query latency for MedicalAgent /api/query calls.", query_histograms, QUERY_DURATION_BUCKETS, ("app", "status"))
    _append_gauge(lines, "medical_rag_query_in_flight", "Current in-flight MedicalAgent RAG query requests.", {"app": APP_NAME}, current_concurrency)
    _append_gauge(lines, "medical_rag_query_max_concurrency", "Maximum observed concurrent MedicalAgent RAG query requests since process start.", {"app": APP_NAME}, max_concurrency)
    _append_histogram(lines, "medical_rag_query_articles_fetched", "Number of articles fetched per successful MedicalAgent RAG query.", articles_histograms, ARTICLES_FETCHED_BUCKETS, ("app",))
    _append_counter(lines, "medical_rag_llm_requests_total", "Total MedicalAgent LLM requests by scenario and status.", llm_counts, ("app", "scenario", "deployment", "model", "status"))
    _append_llm_rate_state(lines, llm_rate_state)
    _append_pod_resource_state(lines, pod_resource_state)
    _append_counter(lines, "medical_rag_pod_threshold_breaches_total", "Total MedicalAgent pod resource threshold breaches.", pod_threshold_breaches, ("reason",))
    return "\n".join(lines) + "\n"


def _append_counter(lines: list[str], name: str, help_text: str, values: dict[tuple[str, ...], int], label_names: tuple[str, ...]) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} counter")
    for label_values, value in values.items():
        labels = dict(zip(label_names, label_values))
        lines.append(f"{name}{_labels(labels)} {value}")


def _append_gauge(lines: list[str], name: str, help_text: str, labels: dict[str, str], value: float) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name}{_labels(labels)} {value}")


def _append_histogram(
    lines: list[str],
    name: str,
    help_text: str,
    values: dict[tuple[str, ...], list[float]],
    buckets: tuple[float, ...],
    label_names: tuple[str, ...],
) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} histogram")
    for label_values, samples in values.items():
        base_labels = dict(zip(label_names, label_values))
        sorted_samples = sorted(samples)
        for bucket in buckets:
            count = sum(1 for sample in sorted_samples if sample <= bucket)
            lines.append(f'{name}_bucket{_labels({**base_labels, "le": _fmt_bucket(bucket)})} {count}')
        lines.append(f'{name}_bucket{_labels({**base_labels, "le": "+Inf"})} {len(sorted_samples)}')
        lines.append(f"{name}_count{_labels(base_labels)} {len(sorted_samples)}")
        lines.append(f"{name}_sum{_labels(base_labels)} {sum(sorted_samples) if sorted_samples else 0}")


def _append_llm_rate_state(lines: list[str], values: dict[tuple[str, str, str], dict[str, float]]) -> None:
    specs = (
        ("medical_rag_llm_rate_limit_current_window_hits", "Current LLM requests observed in the active rolling 60-second window.", "current_window_hits"),
        ("medical_rag_llm_rate_limit_per_minute", "Configured LLM requests-per-minute limit for the scenario.", "limit_per_minute"),
        ("medical_rag_llm_rate_limit_remaining", "Remaining LLM requests before rate limiting in the active rolling 60-second window.", "remaining"),
    )
    for metric_name, help_text, state_key in specs:
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} gauge")
        for label_values, state in values.items():
            app, scenario, deployment = label_values
            lines.append(
                f"{metric_name}{_labels({'app': app, 'scenario': scenario, 'deployment': deployment})} "
                f"{state.get(state_key, 0)}"
            )


def _append_pod_resource_state(lines: list[str], state: dict[str, float]) -> None:
    specs = (
        ("medical_rag_pod_cpu_utilisation_percent", "Latest MedicalAgent pod CPU utilisation percent.", "cpu_percent"),
        ("medical_rag_pod_cpu_threshold_percent", "Configured MedicalAgent pod CPU threshold percent.", "cpu_threshold_percent"),
        ("medical_rag_pod_memory_utilisation_percent", "Latest MedicalAgent pod memory utilisation percent.", "memory_percent"),
        ("medical_rag_pod_memory_threshold_percent", "Configured MedicalAgent pod memory threshold percent.", "memory_threshold_percent"),
    )
    for metric_name, help_text, state_key in specs:
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} gauge")
        lines.append(f"{metric_name}{_labels({'app': APP_NAME})} {state.get(state_key, 0.0)}")


def _labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{key}="{_escape_label(str(value))}"' for key, value in labels.items())
    return "{" + pairs + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _fmt_bucket(value: float) -> str:
    if math.isinf(value):
        return "+Inf"
    return str(int(value)) if float(value).is_integer() else str(value)
