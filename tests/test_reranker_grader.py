"""Tests for cross-encoder reranking and non-LLM confidence grader."""
from __future__ import annotations

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app
from app.rag.reranker import get_reranker, rerank_chunks
from app.rag.retriever import RetrievedChunk, retrieve


@pytest.mark.slow
def test_employee_reranker_never_leaks_hr_content() -> None:
    """Ensures employee role cannot see HR content before or after reranking."""
    chunks = retrieve(
        "L1/L2 band salary ranges and compensation guidelines",
        role="employee",
        hybrid_search=True,
        reranker=True,
        reranker_model="b",
    )
    assert all(c.source != "hr_benefits.md" for c in chunks)
    assert all(c.access_level != "hr" for c in chunks)


def test_reranker_never_receives_unauthorized_chunks() -> None:
    """Verifies with a fake reranker that the reranker input pool contains strictly role-filtered passages."""
    observed_passages: list[str] = []

    class FakeReranker:
        """Lightweight non-model reranker capturing passages without disk or ONNX loading."""

        def score_pairs(self, query: str, passages: list[str]) -> list[float]:
            """Captures candidate passages and returns dummy scores."""
            observed_passages.extend(passages)
            return [0.5] * len(passages)

    with patch("app.rag.reranker.get_reranker", return_value=FakeReranker()):
        retrieve(
            "L1 salary band details",
            role="employee",
            hybrid_search=True,
            reranker=True,
            reranker_model="b",
        )

    assert len(observed_passages) > 0
    # No HR-only terms from hr_benefits.md should be in the evaluated passages
    assert not any("Salary Bands & Levels" in p for p in observed_passages)
    assert not any("L1: Junior Engineer" in p for p in observed_passages)


def test_flags_off_matches_unreranked_behavior() -> None:
    """Confirms RERANKER=off gives output identical to standard retrieval."""
    q = "What is the probation period?"
    unreranked = retrieve(q, role="employee", reranker=False)
    with patch.object(settings, "reranker", False):
        default_res = retrieve(q, role="employee")

    assert [c.text for c in unreranked] == [c.text for c in default_res]
    assert [c.source for c in unreranked] == [c.source for c in default_res]


def test_unknown_role_rejected_with_reranker() -> None:
    """Verifies unknown roles cannot access retrieval or reranking."""
    from app.rag.retriever import _build_where_clause

    with pytest.raises(PermissionError):
        _build_where_clause("guest")

    assert retrieve("VPN setup", role="guest", reranker=True) == []


@pytest.mark.slow
def test_reranker_model_b_and_c_both_rank_chunks() -> None:
    """Verifies that both Model B and Model C execute scoring and rank candidates."""
    chunks = [
        RetrievedChunk("BitLocker full disk encryption", "it_policies.md", "it", "all", 0.80, 0),
        RetrievedChunk("Annual paid leave entitlement is 20 days", "leave.md", "hr", "all", 0.79, 1),
    ]
    ranked_b = rerank_chunks("leave entitlement", chunks, model_key="b")
    assert len(ranked_b) == 2
    assert ranked_b[0].source == "leave.md"
    assert ranked_b[0].rerank_score is not None

    ranked_c = rerank_chunks("leave entitlement", chunks, model_key="c")
    assert len(ranked_c) == 2
    assert ranked_c[0].source == "leave.md"
    assert ranked_c[0].rerank_score is not None


def test_grader_no_passing_chunk_skips_llm_langgraph() -> None:
    """Asserts that in LangGraph mode with GRADER=on, a non-passing query skips the LLM call."""
    client = TestClient(app)
    token = create_access_token("test_user", "employee")
    headers = {"Authorization": f"Bearer {token}"}

    with patch("app.orchestration.nodes.stream_llm") as mock_llm:
        with patch.object(settings, "orchestrator", "langgraph"):
            with patch.object(settings, "reranker", True):
                with patch.object(settings, "reranker_model", "c"):
                    with patch.object(settings, "grader", True):
                        with patch.object(settings, "rerank_min_score", 0.999):
                            resp = client.post(
                                "/chat",
                                json={"message": "What is the capital of Mars?"},
                                headers=headers,
                            )
                            assert resp.status_code == 200
                            mock_llm.assert_not_called()
                            assert "enough information" in resp.text
