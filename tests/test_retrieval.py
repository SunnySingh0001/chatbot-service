"""Golden dataset retrieval verification tests asserting source recall and relevance filtering."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.rag.retriever import retrieve

GOLDEN_FILE = Path(__file__).parent / "golden_set.yaml"


def _load_golden_cases() -> list[dict[str, Any]]:  # type: ignore[misc]
    """Loads all golden test cases from YAML definition file."""
    with GOLDEN_FILE.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


_GOLDEN_CASES = _load_golden_cases()


@pytest.mark.parametrize("case", _GOLDEN_CASES, ids=lambda c: f"Q{c['id']}-{c['type']}")
def test_golden_retrieval(case: dict[str, Any]) -> None:  # type: ignore[misc]
    """Verifies that top-k retrieval meets expected and must-not-retrieve constraints."""
    if case.get("requires_llm"):
        pytest.skip(case.get("skip_reason", "Requires LLM or document grader"))

    question: str = case["question"]
    role: str = case["role"]
    expected_sources: list[str] = case.get("expected_sources", [])
    must_not: list[str] = case.get("must_not_retrieve", [])
    case_type: str = case.get("type", "")

    chunks = retrieve(query=question, role=role)
    retrieved_sources = list(dict.fromkeys(c.source for c in chunks))

    # Verify that forbidden sources never appear
    for forbidden in must_not:
        assert (
            forbidden not in retrieved_sources
        ), f"Forbidden source '{forbidden}' was retrieved for Q{case['id']} by role '{role}'"

    # Verify that expected sources are found in top-k
    for expected in expected_sources:
        assert (
            expected in retrieved_sources
        ), f"Expected source '{expected}' missing from top-k {retrieved_sources} for Q{case['id']}"

    # Verify that off_topic and not_in_docs return zero chunks above threshold
    if case_type in ("not_in_docs", "off_topic"):
        assert (
            len(chunks) == 0
        ), f"Query '{question}' of type '{case_type}' should return 0 chunks, got {len(chunks)}"
