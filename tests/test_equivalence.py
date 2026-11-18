"""Tests asserting consistent execution and contract compliance of the LangGraph orchestrator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def force_mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces mock LLM provider and disables sleep delays for high-throughput testing."""
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr("time.sleep", lambda _: None)


def _load_all_equivalence_cases() -> list[dict[str, str]]:
    """Loads all test cases across golden_set.yaml and embedding_eval.yaml."""
    cases: list[dict[str, str]] = []

    golden_file = Path(__file__).parent / "golden_set.yaml"
    with golden_file.open("r", encoding="utf-8") as fh:
        golden_data: list[dict[str, Any]] = yaml.safe_load(fh) or []
    for c in golden_data:
        cases.append({
            "id": f"golden_{c['id']}",
            "question": str(c["question"]),
            "role": str(c.get("role", "employee")),
        })

    eval_file = Path(__file__).parent / "embedding_eval.yaml"
    with eval_file.open("r", encoding="utf-8") as fh:
        eval_data: dict[str, list[dict[str, Any]]] = yaml.safe_load(fh) or {}
    for section in ("positives", "negatives", "access_tests"):
        for item in eval_data.get(section, []):
            cases.append({
                "id": f"eval_{item['id']}",
                "question": str(item["question"]),
                "role": str(item.get("role", "employee")),
            })
    return cases


_EQUIVALENCE_CASES = _load_all_equivalence_cases()


def _parse_sse(text: str) -> tuple[str, list[str]]:
    """Helper extracting concatenated tokens and source filenames from SSE payload."""
    tokens: list[str] = []
    sources: list[str] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "token" in payload:
                tokens.append(payload["token"])
            if payload.get("done") is True:
                sources = payload.get("sources", [])
    return "".join(tokens), sources


@pytest.mark.parametrize("case", _EQUIVALENCE_CASES, ids=lambda c: c["id"])
def test_langgraph_pipeline_execution(case: dict[str, str]) -> None:
    """Verifies that LangGraph orchestrator processes each case and returns valid SSE response."""
    token = create_access_token(user_id=f"user_{case['id']}", role=case["role"])
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": case["question"]}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)

    assert len(text) > 0, f"Case {case['id']}: response text must not be empty"
    if not sources:
        assert "enough information" in text, (
            f"Case {case['id']}: fallback response expected when no sources retrieved"
        )
