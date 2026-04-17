"""
Integration tests for server/api/health.py
"""
import pytest


@pytest.mark.integration
class TestHealthEndpoint:
    async def test_health_returns_200(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_health_response_contains_status(self, client):
        resp = await client.get("/health")
        body = resp.json()
        assert "status" in body

    async def test_health_status_is_ok(self, client):
        resp = await client.get("/health")
        assert resp.json()["status"] == "ok"
