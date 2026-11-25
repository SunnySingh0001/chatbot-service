"""Tests for admin on-demand MongoDB synchronization endpoint."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.main import app

client = TestClient(app)


def test_sync_unauthorized() -> None:
    """Verifies that unauthenticated requests to /sync/mongo return 401."""
    resp = client.post("/sync/mongo")
    assert resp.status_code == 401


def test_sync_forbidden_for_employee() -> None:
    """Verifies that employee role is forbidden from triggering sync."""
    token = create_access_token("emp_test", "employee")
    resp = client.post("/sync/mongo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert "Forbidden" in resp.json()["detail"]


def test_sync_forbidden_for_client() -> None:
    """Verifies that client role is forbidden from triggering sync."""
    token = create_access_token("client_test", "client")
    resp = client.post("/sync/mongo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_sync_allowed_for_admin_or_founder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that admin/founder can trigger sync, calling sync function correctly."""
    def fake_sync(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        return 160

    monkeypatch.setattr("app.api.sync.sync_mongodb_to_rag", fake_sync)

    token = create_access_token("founder_user", "founder")
    resp = client.post("/sync/mongo", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_chunks"] == 160


def test_sync_mongodb_alias_allowed_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that admin/founder can trigger sync via /sync/mongodb alias."""
    def fake_sync(*args, **kwargs) -> int:  # type: ignore[no-untyped-def]
        return 200

    monkeypatch.setattr("app.api.sync.sync_mongodb_to_rag", fake_sync)

    token = create_access_token("admin_user", "admin")
    resp = client.post("/sync/mongodb", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["total_chunks"] == 200


@pytest.mark.anyio
async def test_check_and_trigger_startup_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that startup check triggers background sync if collection has fewer than 50 chunks."""
    from app.main import _check_and_trigger_startup_sync

    sync_called: list[bool] = []

    def mock_sync(purge_legacy: bool = False) -> int:
        sync_called.append(True)
        return 100

    class MockCollection:
        def count(self) -> int:
            return 10

    class MockClient:
        def get_or_create_collection(self, name: str) -> MockCollection:
            return MockCollection()

    monkeypatch.setattr("chromadb.PersistentClient", lambda **kwargs: MockClient())
    monkeypatch.setattr("app.rag.mongo_ingest.sync_mongodb_to_rag", mock_sync)

    await _check_and_trigger_startup_sync()
    assert len(sync_called) == 1

