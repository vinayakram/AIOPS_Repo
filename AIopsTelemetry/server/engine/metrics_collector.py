"""
System Metrics Collector
========================
Background service that samples CPU, memory, disk I/O, and network statistics
every COLLECTION_INTERVAL_SECONDS and persists them to `system_metrics`.

Delta metrics (disk I/O, network) are computed as rates per second relative to
the previous sample, so dashboard charts are immediately interpretable.

Old snapshots are pruned to keep only the last RETENTION_COUNT rows
(default 720 = 2 hours at 10-second intervals).
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

import psutil

from server.database.engine import SessionLocal
from server.database.models import SystemMetric

logger = logging.getLogger("aiops.metrics_collector")

COLLECTION_INTERVAL_SECONDS: int = 10
RETENTION_COUNT: int = 720   # 720 × 10s = 2 hours of history

_running = False

# Previous I/O counters for delta calculation
_prev_disk: Optional[psutil._common.sdiskio] = None
_prev_net: Optional[psutil._common.snetio] = None
_prev_ts: Optional[float] = None


# ── Public lifecycle ──────────────────────────────────────────────────────────

async def start():
    global _running
    _running = True
    logger.info(
        "Metrics collector started (interval=%ds, retention=%d snapshots)",
        COLLECTION_INTERVAL_SECONDS, RETENTION_COUNT,
    )
    while _running:
        try:
            _collect_and_store()
        except Exception:
            logger.exception("Metrics collector error — skipping this sample")
        await asyncio.sleep(COLLECTION_INTERVAL_SECONDS)


def stop():
    global _running
    _running = False
    logger.info("Metrics collector stopping")


# ── Collection logic ──────────────────────────────────────────────────────────

def _collect_and_store():
    global _prev_disk, _prev_net, _prev_ts

    now_ts = datetime.utcnow().timestamp()
    elapsed = (now_ts - _prev_ts) if _prev_ts else COLLECTION_INTERVAL_SECONDS
    elapsed = max(elapsed, 0.001)   # guard against division by zero

    snapshot = SystemMetric(collected_at=datetime.utcnow())

    # ── CPU ───────────────────────────────────────────────────────────────────
    try:
        snapshot.cpu_percent = psutil.cpu_percent(interval=None)
        per_core = psutil.cpu_percent(percpu=True, interval=None)
        snapshot.cpu_per_core_json = json.dumps(per_core)
        freq = psutil.cpu_freq()
        if freq:
            snapshot.cpu_freq_mhz = round(freq.current, 1)
    except Exception:
        pass

    # ── Memory ────────────────────────────────────────────────────────────────
    try:
        vm = psutil.virtual_memory()
        snapshot.mem_total_mb     = round(vm.total     / 1_048_576, 1)
        snapshot.mem_used_mb      = round(vm.used      / 1_048_576, 1)
        snapshot.mem_available_mb = round(vm.available / 1_048_576, 1)
        snapshot.mem_percent      = vm.percent

        sw = psutil.swap_memory()
        snapshot.swap_used_mb  = round(sw.used / 1_048_576, 1)
        snapshot.swap_percent  = sw.percent
    except Exception:
        pass

    # ── Disk I/O (delta bytes/sec) ────────────────────────────────────────────
    try:
        curr_disk = psutil.disk_io_counters()
        if curr_disk and _prev_disk:
            snapshot.disk_read_bytes_sec  = round((curr_disk.read_bytes  - _prev_disk.read_bytes)  / elapsed, 1)
            snapshot.disk_write_bytes_sec = round((curr_disk.write_bytes - _prev_disk.write_bytes) / elapsed, 1)
            snapshot.disk_read_iops       = round((curr_disk.read_count  - _prev_disk.read_count)  / elapsed, 2)
            snapshot.disk_write_iops      = round((curr_disk.write_count - _prev_disk.write_count) / elapsed, 2)
        _prev_disk = curr_disk
    except Exception:
        pass

    # ── Network I/O (delta bytes/sec) ─────────────────────────────────────────
    try:
        curr_net = psutil.net_io_counters()
        if curr_net and _prev_net:
            snapshot.net_bytes_sent_sec    = round((curr_net.bytes_sent   - _prev_net.bytes_sent)   / elapsed, 1)
            snapshot.net_bytes_recv_sec    = round((curr_net.bytes_recv   - _prev_net.bytes_recv)   / elapsed, 1)
            snapshot.net_packets_sent_sec  = round((curr_net.packets_sent - _prev_net.packets_sent) / elapsed, 2)
            snapshot.net_packets_recv_sec  = round((curr_net.packets_recv - _prev_net.packets_recv) / elapsed, 2)
        _prev_net = curr_net
    except Exception:
        pass

    # ── Active network connections ────────────────────────────────────────────
    try:
        snapshot.net_active_connections = len(psutil.net_connections(kind="inet"))
    except Exception:
        pass

    # ── Process count ─────────────────────────────────────────────────────────
    try:
        snapshot.process_count = len(psutil.pids())
    except Exception:
        pass

    _prev_ts = now_ts

    # ── Persist ───────────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        db.add(snapshot)
        db.commit()
        _prune(db)
    except Exception:
        logger.exception("Failed to persist metrics snapshot")
        db.rollback()
    finally:
        db.close()


def _prune(db):
    """Keep only the most recent RETENTION_COUNT rows."""
    try:
        total = db.query(SystemMetric).count()
        if total > RETENTION_COUNT:
            cutoff_id = (
                db.query(SystemMetric.id)
                .order_by(SystemMetric.id.desc())
                .offset(RETENTION_COUNT)
                .limit(1)
                .scalar()
            )
            if cutoff_id:
                db.query(SystemMetric).filter(SystemMetric.id <= cutoff_id).delete()
                db.commit()
    except Exception:
        logger.debug("Prune failed (non-fatal): %s", exc_info=True)


# ── Query helpers (used by reason_analyzer) ──────────────────────────────────

def get_recent_snapshots(n: int = 18) -> list[dict]:
    """Return the last `n` snapshots as plain dicts (most recent last)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(SystemMetric)
            .order_by(SystemMetric.id.desc())
            .limit(n)
            .all()
        )
        rows.reverse()
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


def get_snapshots_around(dt: datetime, window_seconds: int = 120) -> list[dict]:
    """Return snapshots within `window_seconds` before and after `dt`."""
    from datetime import timedelta
    db = SessionLocal()
    try:
        lo = dt - timedelta(seconds=window_seconds)
        hi = dt + timedelta(seconds=window_seconds)
        rows = (
            db.query(SystemMetric)
            .filter(SystemMetric.collected_at.between(lo, hi))
            .order_by(SystemMetric.collected_at)
            .all()
        )
        return [_to_dict(r) for r in rows]
    finally:
        db.close()


def _to_dict(m: SystemMetric) -> dict:
    return {
        "collected_at": m.collected_at.isoformat() if m.collected_at else None,
        "cpu_percent": m.cpu_percent,
        "cpu_per_core": json.loads(m.cpu_per_core_json) if m.cpu_per_core_json else None,
        "cpu_freq_mhz": m.cpu_freq_mhz,
        "mem_percent": m.mem_percent,
        "mem_used_mb": m.mem_used_mb,
        "mem_available_mb": m.mem_available_mb,
        "swap_percent": m.swap_percent,
        "disk_read_bytes_sec": m.disk_read_bytes_sec,
        "disk_write_bytes_sec": m.disk_write_bytes_sec,
        "disk_read_iops": m.disk_read_iops,
        "disk_write_iops": m.disk_write_iops,
        "net_bytes_sent_sec": m.net_bytes_sent_sec,
        "net_bytes_recv_sec": m.net_bytes_recv_sec,
        "net_active_connections": m.net_active_connections,
        "process_count": m.process_count,
    }
