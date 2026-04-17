"""
Integration tests for GET /api/traces/{trace_id}/profile

Verifies that the profile endpoint returns system metric snapshots
correlated with the trace's time window, so the dashboard can show
resource usage (CPU, memory, disk, network) side-by-side with spans.
"""
import pytest
from datetime import datetime, timedelta


@pytest.mark.integration
class TestTraceProfile:

    async def test_profile_returns_summary_and_snapshots(self, client, db_session):
        """Profile for a trace with metrics in its window returns summary + snapshots."""
        from server.database.models import Trace, SystemMetric

        now = datetime.utcnow()
        trace = Trace(
            id="trace-profile-001",
            app_name="medical-agent",
            status="ok",
            started_at=now - timedelta(seconds=10),
            ended_at=now,
            total_duration_ms=10000,
        )
        db_session.add(trace)

        # Two metric snapshots inside the trace window
        for i, offset in enumerate([8, 3]):
            db_session.add(SystemMetric(
                collected_at=now - timedelta(seconds=offset),
                cpu_percent=40.0 + i * 10,
                mem_percent=60.0 + i * 5,
                mem_used_mb=4096.0,
                mem_total_mb=8192.0,
                mem_available_mb=4096.0,
                disk_read_bytes_sec=1024.0 * 1024,
                disk_write_bytes_sec=512.0 * 1024,
                net_bytes_recv_sec=2048.0 * 1024,
                net_bytes_sent_sec=256.0 * 1024,
                net_active_connections=42,
                process_count=120,
            ))
        db_session.commit()

        resp = await client.get("/api/traces/trace-profile-001/profile")
        assert resp.status_code == 200

        data = resp.json()
        assert data["trace_id"] == "trace-profile-001"
        assert len(data["snapshots"]) >= 1

        summary = data["summary"]
        assert "cpu" in summary
        assert "mem" in summary
        assert "disk" in summary
        assert "net" in summary

        # Each metric group has min / avg / max
        for key in ("min", "avg", "max"):
            assert key in summary["cpu"]
            assert key in summary["mem"]

    async def test_profile_404_for_unknown_trace(self, client):
        """Unknown trace_id returns 404."""
        resp = await client.get("/api/traces/nonexistent-trace/profile")
        assert resp.status_code == 404

    async def test_profile_no_metrics_returns_empty_snapshots(self, client, db_session):
        """Trace with no system metrics in window returns empty snapshots list."""
        from server.database.models import Trace

        old_time = datetime.utcnow() - timedelta(hours=5)
        db_session.add(Trace(
            id="trace-no-metrics",
            app_name="web-search-agent",
            status="ok",
            started_at=old_time,
            ended_at=old_time + timedelta(seconds=5),
            total_duration_ms=5000,
        ))
        db_session.commit()

        resp = await client.get("/api/traces/trace-no-metrics/profile")
        assert resp.status_code == 200

        data = resp.json()
        assert data["snapshots"] == []
        assert data["summary"] == {}

    async def test_profile_at_param_fallback(self, client, db_session):
        """?at= param allows profiling a trace not in local DB by timestamp."""
        from server.database.models import SystemMetric

        now = datetime.utcnow()
        db_session.add(SystemMetric(
            collected_at=now - timedelta(seconds=5),
            cpu_percent=75.0,
            mem_percent=80.0,
            mem_used_mb=6553.0,
            mem_total_mb=8192.0,
            mem_available_mb=1639.0,
            disk_read_bytes_sec=0,
            disk_write_bytes_sec=0,
            net_bytes_recv_sec=0,
            net_bytes_sent_sec=0,
            net_active_connections=10,
            process_count=90,
        ))
        db_session.commit()

        at_ts = (now).isoformat()
        resp = await client.get(f"/api/traces/unknown-ext-trace/profile?at={at_ts}&window=30")
        assert resp.status_code == 200

        data = resp.json()
        assert len(data["snapshots"]) >= 1
        assert data["summary"]["cpu"]["avg"] == pytest.approx(75.0, abs=1.0)

    async def test_profile_summary_min_avg_max_correct(self, client, db_session):
        """Summary statistics (min/avg/max) are computed correctly."""
        from server.database.models import Trace, SystemMetric

        now = datetime.utcnow()
        db_session.add(Trace(
            id="trace-stats-check",
            app_name="test-app",
            status="ok",
            started_at=now - timedelta(seconds=30),
            ended_at=now,
            total_duration_ms=30000,
        ))
        for cpu in [20.0, 40.0, 60.0]:
            db_session.add(SystemMetric(
                collected_at=now - timedelta(seconds=25),
                cpu_percent=cpu,
                mem_percent=50.0,
                mem_used_mb=4096.0,
                mem_total_mb=8192.0,
                mem_available_mb=4096.0,
                disk_read_bytes_sec=0,
                disk_write_bytes_sec=0,
                net_bytes_recv_sec=0,
                net_bytes_sent_sec=0,
                net_active_connections=5,
                process_count=80,
            ))
        db_session.commit()

        resp = await client.get("/api/traces/trace-stats-check/profile")
        assert resp.status_code == 200

        cpu_summary = resp.json()["summary"]["cpu"]
        assert cpu_summary["min"] == pytest.approx(20.0, abs=0.5)
        assert cpu_summary["avg"] == pytest.approx(40.0, abs=0.5)
        assert cpu_summary["max"] == pytest.approx(60.0, abs=0.5)
