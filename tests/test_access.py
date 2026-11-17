"""Access control, role isolation, and authentication enforcement tests."""
from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.main import app
from app.rag.retriever import retrieve

client = TestClient(app)


def test_employee_never_retrieves_hr_chunks() -> None:
    """Verifies that employee role queries never retrieve confidential HR documents."""
    paraphrased_queries = [
        "What are the salary bands for engineering teams?",
        "How much CTC does an L4 tech lead earn annually?",
        "What is the bonus pool percentage for an outstanding rating?",
        "What are the promotion criteria from L2 to L3 level?",
        "How are bi-annual performance ratings calibrated with HR?",
        "Who is eligible for the annual April bonus pool payout?",
    ]
    for query in paraphrased_queries:
        chunks = retrieve(query, role="employee")
        sources = [c.source for c in chunks]
        assert "hr_benefits.md" not in sources, f"Employee leaked hr_benefits.md on query: {query}"


def test_expired_token_returns_401() -> None:
    """Verifies that an expired JWT token returns HTTP 401 Unauthorized."""
    expired_token = create_access_token(
        user_id="emp_expired",
        role="employee",
        expires_delta=timedelta(minutes=-15),
    )
    headers = {"Authorization": f"Bearer {expired_token}"}
    resp = client.post("/chat", json={"message": "What is the leave policy?"}, headers=headers)

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired authentication credentials"


def test_unknown_role_returns_403() -> None:
    """Verifies that a JWT token with an unpermitted role returns HTTP 403 Forbidden."""
    unknown_role_token = create_access_token(
        user_id="contractor_01",
        role="contractor",
    )
    headers = {"Authorization": f"Bearer {unknown_role_token}"}
    resp = client.post("/chat", json={"message": "What is the leave policy?"}, headers=headers)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access forbidden: role is not permitted"


def test_role_in_request_body_is_ignored() -> None:
    """Verifies that injecting role in the request JSON payload is completely ignored."""
    employee_token = create_access_token(user_id="emp_alice", role="employee")
    headers = {"Authorization": f"Bearer {employee_token}"}
    payload = {
        "message": "What are the salary bands for L1?",
        "role": "hr",  # Malicious attempt to escalate privileges
    }
    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert "hr_benefits.md" not in resp.text


def test_hr_can_read_both_all_and_hr_docs() -> None:
    """Verifies that HR role can read both general company docs and confidential HR docs."""
    general_chunks = retrieve("What is the leave policy for paid leaves?", role="hr")
    hr_chunks = retrieve("What are the salary bands for L1 through L3?", role="hr")

    general_sources = [c.source for c in general_chunks]
    hr_sources = [c.source for c in hr_chunks]

    assert "employee_handbook.md" in general_sources
    assert "hr_benefits.md" in hr_sources


def test_unknown_role_retriever_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies that calling retrieve with an unrecognized role logs a warning and returns []."""
    with caplog.at_level(logging.WARNING, logger="chatbot.retriever"):
        chunks = retrieve("What is the leave policy?", role="guest_role")

    assert chunks == []
    assert any("Unrecognized role 'guest_role'" in record.message for record in caplog.records)
