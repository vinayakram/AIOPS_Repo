"""
Tests for the AIOps Poller.

Run with: pytest tests/test_poller.py -v
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.aiops_poller import AIOpsPoller


client = TestClient(app)


# ── Unit: AIOpsPoller ──────────────────────────────────────────────────


class TestAIOpsPollerUnit:
    def test_poller_initializes(self):
        poller = AIOpsPoller()
        assert poller.is_running is False
        assert poller.stats["total_polled"] == 0
        assert poller.stats["total_processed"] == 0

    def test_poller_stats_structure(self):
        poller = AIOpsPoller()
        stats = poller.stats
        assert "running" in stats
        assert "poll_interval_seconds" in stats
        assert "aiops_server" in stats
        assert "total_polled" in stats
        assert "total_processed" in stats
        assert "total_skipped" in stats
        assert "total_errors" in stats
        assert "known_trace_ids" in stats
        assert "last_poll_time" in stats

    def test_poller_default_not_running(self):
        poller = AIOpsPoller()
        assert poller.is_running is False
        assert poller.stats["running"] is False


# ── Integration: Poller API Endpoints ──────────────────────────────────


class TestPollerEndpoints:
    def test_routes_exist(self):
        routes = [r.path for r in app.routes]
        assert "/api/v1/poller/status" in routes
        assert "/api/v1/poller/start" in routes
        assert "/api/v1/poller/stop" in routes

    def test_get_status(self):
        resp = client.get("/api/v1/poller/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "poll_interval_seconds" in data
        assert "total_polled" in data
        assert "total_processed" in data
        assert "aiops_server" in data

    def test_stop_when_not_running(self):
        resp = client.post("/api/v1/poller/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("stopped", "already_stopped")
