"""Live LLM query rewriting test suite evaluated on multi-turn benchmark cases."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.config import settings
from app.rag.condense import condense_query

EVAL_YAML_PATH = Path("tests/rag_eval.yaml")
_LIVE_REASON: str = "Live LLM query rewrite tests run only when RUN_LIVE_LLM_TESTS=1 is set"


def _load_multiturn_cases() -> list[dict[str, Any]]:
    """Loads all multi-turn benchmark cases from tests/rag_eval.yaml."""
    if not EVAL_YAML_PATH.exists():
        return []
    with EVAL_YAML_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = data if isinstance(data, list) else data.get("cases", [])
    return [c for c in cases if c.get("category") == "multi_turn"]


_MULTITURN_CASES = _load_multiturn_cases()


@pytest.mark.requires_llm
@pytest.mark.skipif(not os.getenv("RUN_LIVE_LLM_TESTS"), reason=_LIVE_REASON)
@pytest.mark.parametrize("case", _MULTITURN_CASES, ids=lambda c: str(c.get("id")))
def test_live_rewrite_multiturn_case(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that the live LLM rewrites multi-turn follow-ups into standalone search queries."""
    monkeypatch.setattr(settings, "query_rewrite", True)
    q: str = case["question"]
    history: list[dict[str, str]] = case.get("history", [])

    rewritten = condense_query(q, history=history)

    assert len(rewritten) > 0
    assert len(rewritten.split()) <= 40
    assert "<" not in rewritten and ">" not in rewritten
    # Rewritten query must not be a refusal
    assert "cannot" not in rewritten.lower() and "sorry" not in rewritten.lower()
