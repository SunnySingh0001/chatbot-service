"""Equivalence tests asserting identical behavior between classic and langgraph orchestrators."""
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
def test_orchestrator_equivalence(case: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that classic and langgraph return identical sources and no-context decision."""
    token = create_access_token(user_id=f"user_{case['id']}", role=case["role"])
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": case["question"]}

    # Execute under classic orchestrator
    monkeypatch.setattr(settings, "orchestrator", "classic")
    resp_classic = client.post("/chat", json=payload, headers=headers)
    assert resp_classic.status_code == 200
    text_classic, sources_classic = _parse_sse(resp_classic.text)

    # Execute under langgraph orchestrator
    monkeypatch.setattr(settings, "orchestrator", "langgraph")
    resp_langgraph = client.post("/chat", json=payload, headers=headers)
    assert resp_langgraph.status_code == 200
    text_langgraph, sources_langgraph = _parse_sse(resp_langgraph.text)

    # 1. Sources citation list must be completely identical
    assert sources_classic == sources_langgraph, (
        f"Case {case['id']} mismatch: classic={sources_classic} vs langgraph={sources_langgraph}"
    )

    # 2. No-context fallback decision must be completely identical
    no_ctx_classic = "enough information" in text_classic or len(sources_classic) == 0
    no_ctx_langgraph = "enough information" in text_langgraph or len(sources_langgraph) == 0
    assert no_ctx_classic == no_ctx_langgraph, (
        f"Case {case['id']} no-context decision mismatch: classic={no_ctx_classic} vs langgraph={no_ctx_langgraph}"
    )
