"""
Integration tests for server/api/issues.py

Routes tested:
  GET    /api/issues
  POST   /api/issues
  GET    /api/issues/{id}
  PATCH  /api/issues/{id}
  POST   /api/issues/{id}/acknowledge
  POST   /api/issues/{id}/escalate
  POST   /api/issues/{id}/resolve

Coverage target: ≥ 85% (see CLAUDE.md §4c)
"""
import pytest


@pytest.mark.integration
class TestListIssues:
    async def test_returns_empty_list_initially(self, client):
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["issues"] == []

    async def test_returns_created_issues(self, client):
        await client.post("/api/issues", json={
            "app_name": "test-app",
            "issue_type": "high_latency",
            "severity": "medium",
            "title": "High latency",
        })
        resp = await client.get("/api/issues")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_filter_by_app_name(self, client):
        await client.post("/api/issues", json={
            "app_name": "app-a",
            "issue_type": "high_latency",
            "severity": "low",
            "title": "App A issue",
        })
        await client.post("/api/issues", json={
            "app_name": "app-b",
            "issue_type": "high_latency",
            "severity": "low",
            "title": "App B issue",
        })
        resp = await client.get("/api/issues?app_name=app-a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["issues"][0]["app_name"] == "app-a"

    async def test_filter_by_status(self, client):
        create_resp = await client.post("/api/issues", json={
            "app_name": "app",
            "issue_type": "t",
            "severity": "low",
            "title": "Test",
        })
        issue_id = create_resp.json()["id"]
        await client.post(f"/api/issues/{issue_id}/resolve")

        resp = await client.get("/api/issues?status=RESOLVED")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    async def test_filter_by_severity(self, client):
        await client.post("/api/issues", json={
            "app_name": "app",
            "issue_type": "t",
            "severity": "critical",
            "title": "Critical",
        })
        await client.post("/api/issues", json={
            "app_name": "app",
            "issue_type": "t",
            "severity": "low",
            "title": "Low",
        })
        resp = await client.get("/api/issues?severity=critical")
        assert resp.json()["total"] == 1


@pytest.mark.integration
class TestCreateIssue:
    async def test_create_returns_201(self, client):
        resp = await client.post("/api/issues", json={
            "app_name": "test-app",
            "issue_type": "high_latency",
            "severity": "high",
            "title": "Latency spike",
            "description": "P95 exceeded 3x baseline",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert "id" in body
        assert body["created"] is True

    async def test_create_invalid_severity_returns_400(self, client):
        resp = await client.post("/api/issues", json={
            "app_name": "test-app",
            "issue_type": "high_latency",
            "severity": "INVALID",
            "title": "Bad severity",
        })
        assert resp.status_code == 400

    async def test_duplicate_open_issue_not_created(self, client):
        payload = {
            "app_name": "test-app",
            "issue_type": "high_latency",
            "severity": "medium",
            "title": "Duplicate test",
        }
        first = await client.post("/api/issues", json=payload)
        second = await client.post("/api/issues", json=payload)
        assert first.status_code == 201
        assert second.json()["created"] is False

    async def test_resolved_issue_can_be_recreated(self, client):
        payload = {
            "app_name": "test-app",
            "issue_type": "high_latency",
            "severity": "low",
            "title": "Reopenable",
        }
        create_resp = await client.post("/api/issues", json=payload)
        issue_id = create_resp.json()["id"]
        await client.post(f"/api/issues/{issue_id}/resolve")
        second = await client.post("/api/issues", json=payload)
        assert second.json()["created"] is True


@pytest.mark.integration
class TestGetIssue:
    async def test_get_existing_issue(self, client):
        create_resp = await client.post("/api/issues", json={
            "app_name": "app",
            "issue_type": "t",
            "severity": "low",
            "title": "Check me",
        })
        issue_id = create_resp.json()["id"]
        resp = await client.get(f"/api/issues/{issue_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == issue_id

    async def test_get_nonexistent_issue_returns_404(self, client):
        resp = await client.get("/api/issues/99999")
        assert resp.status_code == 404


@pytest.mark.integration
class TestIssueTransitions:
    async def _create(self, client, **kwargs):
        defaults = {
            "app_name": "app",
            "issue_type": "t",
            "severity": "medium",
            "title": "Test",
        }
        defaults.update(kwargs)
        resp = await client.post("/api/issues", json=defaults)
        return resp.json()["id"]

    async def test_acknowledge_sets_status(self, client):
        issue_id = await self._create(client)
        resp = await client.post(f"/api/issues/{issue_id}/acknowledge")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACKNOWLEDGED"

    async def test_escalate_increments_count(self, client):
        issue_id = await self._create(client)
        await client.post(f"/api/issues/{issue_id}/escalate")
        resp = await client.get(f"/api/issues/{issue_id}")
        assert resp.json()["escalation_count"] == 1

    async def test_resolve_sets_status_and_timestamp(self, client):
        issue_id = await self._create(client)
        resp = await client.post(f"/api/issues/{issue_id}/resolve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "RESOLVED"
        assert body["resolved_at"] is not None

    async def test_response_includes_sev_label(self, client):
        issue_id = await self._create(client, severity="critical")
        resp = await client.get(f"/api/issues/{issue_id}")
        assert resp.json()["sev_label"] == "SEV1"

    async def test_transition_nonexistent_issue_returns_404(self, client):
        resp = await client.post("/api/issues/99999/acknowledge")
        assert resp.status_code == 404


@pytest.mark.integration
class TestPatchIssue:
    async def test_patch_severity(self, client):
        create_resp = await client.post("/api/issues", json={
            "app_name": "app",
            "issue_type": "t",
            "severity": "low",
            "title": "Patchable",
        })
        issue_id = create_resp.json()["id"]
        resp = await client.patch(f"/api/issues/{issue_id}", json={"severity": "high"})
        assert resp.status_code == 200
        assert resp.json()["severity"] == "high"

    async def test_patch_invalid_status_returns_400(self, client):
        create_resp = await client.post("/api/issues", json={
            "app_name": "app",
            "issue_type": "t",
            "severity": "low",
            "title": "Patchable",
        })
        issue_id = create_resp.json()["id"]
        resp = await client.patch(f"/api/issues/{issue_id}", json={"status": "INVALID"})
        assert resp.status_code == 400
