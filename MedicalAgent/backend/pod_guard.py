from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .config import settings
from .monitoring import observe_pod_resource_sample, observe_pod_threshold_breach
from .tracing.aiops_client import send_pod_threshold_breach


_CGROUP = "/sys/fs/cgroup"


@dataclass(frozen=True)
class PodResourceState:
    cpu_percent: float
    cpu_threshold_percent: float
    memory_percent: float | None
    memory_threshold_percent: float
    breached: bool
    reason: str


class PodResourceGuard:
    """Small cgroup-aware guard used by the container demo.

    The CPU calculation uses cgroup v2 cpu.stat deltas and normalises by the
    configured container quota when Docker/Kubernetes exposes one.
    """

    def __init__(self) -> None:
        self._last_usage_usec: int | None = None
        self._last_sample_at: float | None = None
        self._last_breach_sent_at = 0.0

    def check(self) -> PodResourceState:
        cpu = self._cpu_percent()
        mem = self._memory_percent()

        reasons: list[str] = []
        if settings.POD_CPU_THRESHOLD_ENABLED and cpu >= settings.POD_CPU_THRESHOLD_PERCENT:
            reasons.append(
                f"cpu utilisation {cpu:.1f}% breached threshold "
                f"{settings.POD_CPU_THRESHOLD_PERCENT:.1f}%"
            )
        if (
            settings.POD_MEMORY_THRESHOLD_ENABLED
            and mem is not None
            and mem >= settings.POD_MEMORY_THRESHOLD_PERCENT
        ):
            reasons.append(
                f"memory utilisation {mem:.1f}% breached threshold "
                f"{settings.POD_MEMORY_THRESHOLD_PERCENT:.1f}%"
            )

        state = PodResourceState(
            cpu_percent=round(cpu, 2),
            cpu_threshold_percent=settings.POD_CPU_THRESHOLD_PERCENT,
            memory_percent=round(mem, 2) if mem is not None else None,
            memory_threshold_percent=settings.POD_MEMORY_THRESHOLD_PERCENT,
            breached=bool(reasons),
            reason="; ".join(reasons),
        )
        observe_pod_resource_sample(
            cpu_percent=state.cpu_percent,
            cpu_threshold_percent=state.cpu_threshold_percent,
            memory_percent=state.memory_percent,
            memory_threshold_percent=state.memory_threshold_percent,
        )
        if state.breached:
            observe_pod_threshold_breach(state.reason)
            self._send_breach_trace(state)
        return state

    def _send_breach_trace(self, state: PodResourceState) -> None:
        now = time.monotonic()
        if now - self._last_breach_sent_at < settings.POD_THRESHOLD_TELEMETRY_MIN_INTERVAL_SECONDS:
            return
        self._last_breach_sent_at = now
        send_pod_threshold_breach(
            reason=state.reason,
            cpu_percent=state.cpu_percent,
            cpu_threshold_percent=state.cpu_threshold_percent,
            memory_percent=state.memory_percent,
            memory_threshold_percent=state.memory_threshold_percent,
        )

    def _cpu_percent(self) -> float:
        now = time.monotonic()
        usage = _read_cgroup_cpu_usage_usec()
        if usage is None:
            return _read_proc_cpu_percent()

        if self._last_usage_usec is None or self._last_sample_at is None:
            self._last_usage_usec = usage
            self._last_sample_at = now
            return 0.0

        elapsed = max(now - self._last_sample_at, 0.001)
        usage_delta_seconds = max(usage - self._last_usage_usec, 0) / 1_000_000
        self._last_usage_usec = usage
        self._last_sample_at = now

        quota_cores = _read_cgroup_cpu_quota_cores()
        if quota_cores <= 0:
            quota_cores = os.cpu_count() or 1
        return min(999.0, (usage_delta_seconds / elapsed) / quota_cores * 100)

    @staticmethod
    def _memory_percent() -> float | None:
        current = _read_int_file(f"{_CGROUP}/memory.current")
        max_raw = _read_text_file(f"{_CGROUP}/memory.max")
        if current is None or not max_raw or max_raw == "max":
            return _read_proc_memory_percent()
        try:
            max_bytes = int(max_raw)
        except ValueError:
            return None
        if max_bytes <= 0:
            return None
        return min(100.0, current / max_bytes * 100)


def _read_cgroup_cpu_usage_usec() -> int | None:
    raw = _read_text_file(f"{_CGROUP}/cpu.stat")
    if not raw:
        return None
    for line in raw.splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _read_cgroup_cpu_quota_cores() -> float:
    raw = _read_text_file(f"{_CGROUP}/cpu.max")
    if not raw:
        return 0.0
    parts = raw.split()
    if len(parts) < 2 or parts[0] == "max":
        return 0.0
    try:
        quota = float(parts[0])
        period = float(parts[1])
    except ValueError:
        return 0.0
    return quota / period if period > 0 else 0.0


def _read_proc_cpu_percent() -> float:
    # Fallback for non-cgroup local runs. It intentionally returns 0 because
    # the demo is meant to protect the VM and only enforce pod/container limits.
    return 0.0


def _read_proc_memory_percent() -> float | None:
    raw = _read_text_file("/proc/meminfo")
    if not raw:
        return None
    values: dict[str, int] = {}
    for line in raw.splitlines():
        key, _, rest = line.partition(":")
        try:
            values[key] = int(rest.strip().split()[0])
        except (ValueError, IndexError):
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return max(0.0, min(100.0, (total - available) / total * 100))


def _read_int_file(path: str) -> int | None:
    raw = _read_text_file(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _read_text_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


pod_resource_guard = PodResourceGuard()
