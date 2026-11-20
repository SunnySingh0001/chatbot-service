"""Tests for BM25 keyword search, reciprocal rank fusion, and hybrid role isolation."""
from __future__ import annotations

import pytest

from app.config import settings
from app.rag.bm25 import get_bm25_index, tokenize_bm25
from app.rag.retriever import get_collection_name, retrieve


def test_bm25_tokenization_preserves_codes_and_numbers() -> None:
    """Verifies that the BM25 tokenizer keeps dotted numbers, codes, and Hindi words intact."""
    tokens = tokenize_bm25("Section 2.3: Work from Home (WFH) policy. SLA P1 and L1 band. छुट्टी")
    assert "2.3" in tokens
    assert "wfh" in tokens
    assert "sla" in tokens
    assert "p1" in tokens
    assert "l1" in tokens
    assert "band" in tokens
    assert "छुट्टी" in tokens


def test_employee_bm25_index_excludes_hr_chunks() -> None:
    """Asserts that HR-only documents never enter an employee's BM25 index corpus."""
    col_name = get_collection_name("hierarchical", 4)
    emp_index = get_bm25_index(col_name, role="employee")

    assert emp_index.n_docs > 0
    assert all(c.access_level != "hr" for c in emp_index.corpus)
    assert all(c.source != "hr_benefits.md" for c in emp_index.corpus)

    # Keyword search for HR-only terms yields zero hits
    assert emp_index.search("L1 band") == []
    assert emp_index.search("CTC ranges") == []


def test_employee_hybrid_retrieval_never_leaks_hr_content() -> None:
    """Ensures employee role cannot retrieve HR chunks even when searching exact HR keywords."""
    chunks = retrieve(
        "L1/L2 band salary ranges and compensation guidelines",
        role="employee",
        hybrid_search=True,
    )
    assert all(c.source != "hr_benefits.md" for c in chunks)
    assert all(c.access_level != "hr" for c in chunks)


def test_hr_role_retrieves_hr_chunks_with_hybrid_search() -> None:
    """Ensures HR role successfully retrieves HR chunks when querying HR keywords."""
    chunks = retrieve(
        "L1/L2 band salary ranges and compensation guidelines",
        role="hr",
        hybrid_search=True,
    )
    assert len(chunks) > 0
    assert any(c.source == "hr_benefits.md" for c in chunks)


def test_unknown_role_rejected_by_hybrid_retrieval() -> None:
    """Verifies unknown roles raise PermissionError or return empty results without leaking."""
    with pytest.raises(PermissionError):
        get_bm25_index(get_collection_name("hierarchical", 4), role="contractor")

    assert retrieve("VPN GlobalProtect", role="contractor", hybrid_search=True) == []


def test_hybrid_search_keyword_targets() -> None:
    """Verifies that keyword and code queries hit the expected source documents with hybrid search."""
    vpn_chunks = retrieve("VPN", role="employee", hybrid_search=True)
    assert len(vpn_chunks) > 0
    assert vpn_chunks[0].source == "it_policies.md"

    sla_chunks = retrieve("SLA P1", role="employee", hybrid_search=True)
    assert len(sla_chunks) > 0
    assert sla_chunks[0].source == "it_policies.md"

    s23_chunks = retrieve("Section 2.3", role="employee", hybrid_search=True)
    assert any(c.source == "employee_handbook.md" for c in s23_chunks[:3])


def test_hybrid_search_off_matches_vector_results() -> None:
    """Confirms that HYBRID_SEARCH=off gives results identical to pure vector retrieval."""
    query = "What is the notice period for confirmed staff?"
    v_chunks = retrieve(query, role="employee", hybrid_search=False)
    default_chunks = retrieve(query, role="employee")

    assert [c.text for c in v_chunks] == [c.text for c in default_chunks]
    assert [c.source for c in v_chunks] == [c.source for c in default_chunks]
    assert [c.score for c in v_chunks] == [c.score for c in default_chunks]


def test_bm25_chunk_score_provenance_never_inherits() -> None:
    """Verifies that chunks discovered via BM25 carry their own true vector similarity or None."""
    import numpy as np
    from app.rag.embedder import get_embedding_function
    from app.rag.retriever import _fuse_reciprocal_rank, RetrievedChunk

    c1 = RetrievedChunk("vec1", "doc1.md", "eng", "all", score=0.88, chunk_index=0)
    c2 = RetrievedChunk("bm1", "doc2.md", "eng", "all", score=None, chunk_index=1)

    q_emb = [1.0, 0.0]
    doc_embs = {("doc2.md", 1): [0.75, 0.0]}

    # c2 must compute 0.75 from dot product, NOT inherit c1's score (0.88)
    fused = _fuse_reciprocal_rank(
        [c1], [c2], query_embedding=q_emb, chunk_embeddings=doc_embs
    )
    c2_fused = next(c for c in fused if c.source == "doc2.md")
    assert c2_fused.score == 0.75
    assert c2_fused.score != c1.score

    # Without embedding, chunk score must be None, never an inherited float
    fused_no_emb = _fuse_reciprocal_rank([c1], [c2], query_embedding=q_emb, chunk_embeddings={})
    c2_no_emb = next(c for c in fused_no_emb if c.source == "doc2.md")
    assert c2_no_emb.score is None


def test_hybrid_search_bypasses_low_vector_similarity_when_bm25_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that low vector similarity score (< threshold) does not gate out BM25 exact matches."""
    import app.rag.retriever as ret_mod

    low_score_vector_chunk = ret_mod.RetrievedChunk(
        text="Generic IT support instructions.",
        source="general_faq.md",
        department="it",
        access_level="all",
        score=0.42,
        chunk_index=0,
    )
    monkeypatch.setattr(
        ret_mod,
        "_query_vector_store",
        lambda *args, **kwargs: [low_score_vector_chunk],
    )

    chunks = ret_mod.retrieve("VPN", role="employee", score_threshold=0.80, hybrid_search=True)
    assert len(chunks) > 0
    assert any("VPN" in c.text or c.source == "it_policies.md" for c in chunks)

    pure_vec_chunks = ret_mod.retrieve("VPN", role="employee", score_threshold=0.80, hybrid_search=False)
    assert pure_vec_chunks == []


def test_hybrid_search_fuses_both_vector_and_bm25_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that RRF fuses both vector candidates and BM25 candidates into the result pool."""
    import app.rag.retriever as ret_mod

    high_score_vector_chunk = ret_mod.RetrievedChunk(
        text="Standard laptop request process.",
        source="laptop_policy.md",
        department="it",
        access_level="all",
        score=0.89,
        chunk_index=0,
    )
    monkeypatch.setattr(
        ret_mod,
        "_query_vector_store",
        lambda *args, **kwargs: [high_score_vector_chunk],
    )

    chunks = ret_mod.retrieve("Section 2.3", role="employee", score_threshold=0.80, hybrid_search=True)
    sources = [c.source for c in chunks]
    assert "laptop_policy.md" in sources
    assert any("employee_handbook.md" in s or "it_policies.md" in s for s in sources)


def test_hybrid_search_gates_when_both_vector_low_and_bm25_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies that retrieval returns empty when vector score < threshold and BM25 finds 0 matches."""
    import app.rag.retriever as ret_mod

    low_score_chunk = ret_mod.RetrievedChunk(
        text="Unrelated company overview.",
        source="overview.md",
        department="all",
        access_level="all",
        score=0.35,
        chunk_index=0,
    )
    monkeypatch.setattr(
        ret_mod,
        "_query_vector_store",
        lambda *args, **kwargs: [low_score_chunk],
    )

    chunks = ret_mod.retrieve(
        "xyznonexistenttoken999qqq",
        role="employee",
        score_threshold=0.80,
        hybrid_search=True,
    )
    assert chunks == []


def test_fetch_candidate_pool_does_not_gate_bm25_when_vector_below_thresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies _fetch_candidate_pool delegates to hybrid fusion even when vector score < threshold."""
    from unittest.mock import MagicMock
    import app.rag.retriever as ret_mod

    mock_col = MagicMock()
    mock_col.count.return_value = 10
    low_chunk = ret_mod.RetrievedChunk("txt", "src.md", "dep", "all", score=0.40, chunk_index=0)
    mock_bm25_chunk = ret_mod.RetrievedChunk("bm25 txt", "bm25.md", "dep", "all", score=None, chunk_index=0)

    monkeypatch.setattr(ret_mod, "_query_vector_store", lambda *a, **k: [low_chunk])
    res_pure = ret_mod._fetch_candidate_pool(
        mock_col, "test", {}, "employee", None, None, False, 5, 10, thresh=0.80
    )
    assert res_pure == []

    monkeypatch.setattr(ret_mod, "_execute_hybrid_fusion", lambda *a, **k: [mock_bm25_chunk])
    res_hyb = ret_mod._fetch_candidate_pool(
        mock_col, "test", {}, "employee", None, None, True, 5, 10, thresh=0.80
    )
    assert res_hyb == [mock_bm25_chunk]
