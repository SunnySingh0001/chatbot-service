"""Phase 1 Automated Test Suite: E5 Asymmetric Embeddings, Threshold Calibration, and Hybrid Preservation."""
from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock

from app.config import settings
from app.rag.embedder import MultilingualE5EmbeddingFunction, get_embedding_function
from app.rag.retriever import (
    RetrievedChunk,
    _parse_results,
    _query_vector_store,
    _resolve_score_threshold,
    retrieve,
)


# ── A. Embeddings Tests ────────────────────────────────────────────────────────


def test_document_receives_passage_prefix() -> None:
    """Verifies that embed_documents prepends 'passage: ' and generates 384-d normalized vectors."""
    ef = get_embedding_function()
    assert isinstance(ef, MultilingualE5EmbeddingFunction)

    docs = ["Employee leave balance is 39 days annually."]
    embs = ef.embed_documents(docs)

    assert len(embs) == 1
    assert len(embs[0]) == 384
    norm = float(np.linalg.norm(embs[0]))
    assert pytest.approx(norm, rel=1e-3) == 1.0


def test_query_receives_query_prefix() -> None:
    """Verifies that embed_query prepends 'query: ' and handles both str and list[str]."""
    ef = get_embedding_function()
    assert isinstance(ef, MultilingualE5EmbeddingFunction)

    embs_str = ef.embed_query("leave policy kya hai?")
    embs_list = ef.embed_query(["leave policy kya hai?"])

    assert len(embs_str) == 1
    assert len(embs_list) == 1
    assert np.allclose(embs_str[0], embs_list[0], atol=1e-5)
    assert len(embs_str[0]) == 384


def test_no_double_prefixing_on_documents_or_queries() -> None:
    """Verifies that passing text that already has passage: or query: prefix prevents duplication."""
    ef = get_embedding_function()
    assert isinstance(ef, MultilingualE5EmbeddingFunction)

    raw_doc = "Standard company policy document."
    prefixed_doc = f"passage: {raw_doc}"
    multi_prefixed_doc = f"passage: passage: {raw_doc}"
    cross_prefixed_doc = f"query: {raw_doc}"

    emb_raw = ef.embed_documents([raw_doc])
    emb_prefixed = ef.embed_documents([prefixed_doc])
    emb_multi = ef.embed_documents([multi_prefixed_doc])
    emb_cross = ef.embed_documents([cross_prefixed_doc])

    assert np.allclose(emb_raw[0], emb_prefixed[0], atol=1e-5)
    assert np.allclose(emb_raw[0], emb_multi[0], atol=1e-5)
    assert np.allclose(emb_raw[0], emb_cross[0], atol=1e-5)

    raw_query = "What is the notice period?"
    prefixed_query = f"query: {raw_query}"
    multi_prefixed_query = f"query: query: {raw_query}"
    cross_prefixed_query = f"passage: {raw_query}"

    emb_q_raw = ef.embed_query([raw_query])
    emb_q_prefixed = ef.embed_query([prefixed_query])
    emb_q_multi = ef.embed_query([multi_prefixed_query])
    emb_q_cross = ef.embed_query([cross_prefixed_query])

    assert np.allclose(emb_q_raw[0], emb_q_prefixed[0], atol=1e-5)
    assert np.allclose(emb_q_raw[0], emb_q_multi[0], atol=1e-5)
    assert np.allclose(emb_q_raw[0], emb_q_cross[0], atol=1e-5)


def test_embedding_dimensions_and_l2_normalization() -> None:
    """Verifies all output vectors have exact dimension 384 and unit length."""
    ef = get_embedding_function()
    texts = ["Sample passage one", "Sample query two", "Hindi: छुट्टी की नीति"]
    doc_embs = ef.embed_documents(texts)
    q_embs = ef.embed_query(texts)

    for emb in doc_embs + q_embs:
        assert len(emb) == 384
        assert pytest.approx(float(np.linalg.norm(emb)), rel=1e-3) == 1.0


def test_ingestion_path_consistency() -> None:
    """Verifies ChromaDB __call__ and embed_documents produce identical vectors."""
    ef = get_embedding_function()
    chunks = ["HR benefits manual section 4.", "IT security guidelines."]
    call_embs = ef(chunks)
    doc_embs = ef.embed_documents(chunks)

    for c_emb, d_emb in zip(call_embs, doc_embs):
        assert np.allclose(c_emb, d_emb, atol=1e-6)
    assert ef.name() == "multilingual-e5-small"


# ── B. Retrieval Tests ────────────────────────────────────────────────────────


def test_distance_to_similarity_score_conversion() -> None:
    """Verifies score conversion accurately transforms cosine distance to similarity (1.0 - dist)."""
    raw_results = {
        "documents": [["Chunk text A", "Chunk text B"]],
        "metadatas": [[{"source": "docA.md"}, {"source": "docB.md"}]],
        "distances": [[0.1500, 0.2850]],
    }
    parsed = _parse_results(raw_results)  # type: ignore[arg-type]
    assert len(parsed) == 2
    assert parsed[0].score == 0.8500
    assert parsed[1].score == 0.7150


def test_known_cosine_distance_to_similarity_boundary_cases() -> None:
    """Verifies ChromaDB cosine distance conversion across all canonical geometric angles."""
    # ChromaDB hnsw:space='cosine' defines distance d = 1 - cos(theta) in [0, 2]
    # Similarity score S = 1.0 - d = cos(theta) in [-1, 1]
    raw_results = {
        "documents": [["exact", "high", "threshold", "orthogonal", "obtuse", "opposite"]],
        "metadatas": [[
            {"source": "m1.md"}, {"source": "m2.md"}, {"source": "m3.md"},
            {"source": "m4.md"}, {"source": "m5.md"}, {"source": "m6.md"},
        ]],
        "distances": [[0.0000, 0.1000, 0.2000, 1.0000, 1.5000, 2.0000]],
    }
    parsed = _parse_results(raw_results)  # type: ignore[arg-type]
    assert len(parsed) == 6
    # 0.0 distance (identical vectors, theta=0) -> score 1.0000
    assert parsed[0].score == 1.0000
    # 0.1000 distance (cos=0.9) -> score 0.9000
    assert parsed[1].score == 0.9000
    # 0.2000 distance (cos=0.8, baseline threshold) -> score 0.8000
    assert parsed[2].score == 0.8000
    # 1.0000 distance (orthogonal vectors, theta=90 deg) -> score 0.0000
    assert parsed[3].score == 0.0000
    # 1.5000 distance (obtuse angle, cos=-0.5) -> score -0.5000
    assert parsed[4].score == -0.5000
    # 2.0000 distance (opposite vectors, theta=180 deg) -> score -1.0000
    assert parsed[5].score == -1.0000


def test_hnsw_cosine_metric_dot_product_equivalence() -> None:
    """Verifies that 1.0 - distance is mathematically identical to unit dot product."""
    u = np.array([0.6, 0.8, 0.0], dtype=np.float32)
    v = np.array([0.0, 0.8, 0.6], dtype=np.float32)
    # Both are unit normalized: ||u|| = 1.0, ||v|| = 1.0
    assert pytest.approx(float(np.linalg.norm(u)), rel=1e-5) == 1.0
    assert pytest.approx(float(np.linalg.norm(v)), rel=1e-5) == 1.0

    dot_similarity = float(np.dot(u, v))  # 0.6*0 + 0.8*0.8 + 0*0.6 = 0.64
    assert pytest.approx(dot_similarity, rel=1e-4) == 0.64

    # HNSW cosine distance: d = 1 - dot_similarity
    chroma_distance = 1.0 - dot_similarity  # 0.36
    score_from_distance = round(1.0 - chroma_distance, 4)

    assert score_from_distance == 0.6400
    assert pytest.approx(score_from_distance, rel=1e-4) == dot_similarity


def test_query_vector_store_uses_query_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies _query_vector_store computes query embeddings with embed_query before querying Chroma."""
    mock_collection = MagicMock()
    mock_collection.query.return_value = {
        "documents": [["text"]],
        "metadatas": [[{"source": "test.md", "department": "all", "access_level": "all", "chunk_index": 0}]],
        "distances": [[0.12]],
    }
    mock_ef = MagicMock()
    mock_ef.embed_query.return_value = [[0.1] * 384]
    monkeypatch.setattr("app.rag.retriever.get_embedding_function", lambda: mock_ef)

    chunks = _query_vector_store(mock_collection, "sample question", {}, 5)

    assert len(chunks) == 1
    mock_ef.embed_query.assert_called_once_with(["sample question"])
    mock_collection.query.assert_called_once()
    assert "query_embeddings" in mock_collection.query.call_args.kwargs


def test_language_aware_threshold_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies language-aware threshold selects correct values for English, Hinglish, and Hindi."""
    monkeypatch.setattr(settings, "rag_score_threshold", 0.78)
    monkeypatch.setattr(settings, "rag_score_threshold_hinglish", None)
    monkeypatch.setattr(settings, "rag_score_threshold_hindi", None)

    # English uses base threshold
    assert _resolve_score_threshold("What is the company leave policy?", None) == 0.78
    # Hinglish defaults to min(base, 0.75) when not explicitly set
    assert _resolve_score_threshold("leave policy kya hai aur kitni chutti milti hai?", None) == 0.75
    # Hindi defaults to min(base, 0.75)
    assert _resolve_score_threshold("वार्षिक अवकाश नीति क्या है?", None) == 0.75

    # Explicit environment overrides take precedence
    monkeypatch.setattr(settings, "rag_score_threshold_hinglish", 0.72)
    monkeypatch.setattr(settings, "rag_score_threshold_hindi", 0.70)
    assert _resolve_score_threshold("leave policy kya hai?", None) == 0.72
    assert _resolve_score_threshold("वार्षिक अवकाश नीति क्या है?", None) == 0.70

    # Explicit parameter overrides everything
    assert _resolve_score_threshold("leave policy kya hai?", 0.88) == 0.88


def test_english_hindi_hinglish_retrieval() -> None:
    """Verifies that English, Hindi, and Hinglish queries successfully retrieve relevant documents."""
    en_chunks = retrieve("What is the notice period for confirmed employees?", role="employee")
    assert len(en_chunks) > 0
    assert any("notice" in c.text.lower() or "policy" in c.source.lower() for c in en_chunks)

    hi_chunks = retrieve("कर्मचारियों के लिए नोटिस अवधि क्या है?", role="employee")
    assert len(hi_chunks) > 0

    hing_chunks = retrieve("confirmed staff ka notice period kitna hai?", role="employee")
    assert len(hing_chunks) > 0


def test_off_topic_query_rejected() -> None:
    """Verifies that completely off-topic questions return no chunks above threshold."""
    chunks = retrieve(
        "What is the authentic recipe for baking traditional Italian sourdough bread from scratch?",
        role="employee",
        score_threshold=0.77,
    )
    assert chunks == []


# ── C. Regression & Hybrid Preservation Tests ─────────────────────────────────


def test_exact_invoice_and_order_ids_via_bm25_hybrid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies exact identifiers (invoice numbers / order IDs) are retrieved via BM25 even with low vector score."""
    import app.rag.retriever as ret_mod

    # Vector store returns poor unrelated match below threshold
    low_vec_chunk = ret_mod.RetrievedChunk(
        text="General corporate background info.",
        source="general_faq.md",
        department="finance",
        access_level="finance",
        score=0.41,
        chunk_index=0,
    )
    monkeypatch.setattr(ret_mod, "_query_vector_store", lambda *args, **kwargs: [low_vec_chunk])

    # Querying exact term 'VPN' with employee role retrieves it via BM25
    chunks = ret_mod.retrieve("VPN GlobalProtect", role="employee", score_threshold=0.80, hybrid_search=True)
    assert len(chunks) > 0
    assert any("vpn" in c.text.lower() or c.source == "it_policies.md" for c in chunks)


def test_hybrid_gating_preserves_task1_fix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirms Task 1 fix is intact: low vector score does not terminate hybrid candidate generation."""
    import app.rag.retriever as ret_mod

    mock_col = MagicMock()
    mock_col.count.return_value = 5
    low_vec = ret_mod.RetrievedChunk("txt", "src.md", "dep", "all", score=0.45, chunk_index=0)
    bm25_match = ret_mod.RetrievedChunk("exact match", "bm25.md", "dep", "all", score=None, chunk_index=1)

    monkeypatch.setattr(ret_mod, "_query_vector_store", lambda *a, **k: [low_vec])
    monkeypatch.setattr(ret_mod, "_execute_hybrid_fusion", lambda *a, **k: [bm25_match])

    # Pure vector gates out 0.45 < 0.78
    res_vec = ret_mod._fetch_candidate_pool(
        mock_col, "query", {}, "employee", None, None, False, 5, 10, thresh=0.78
    )
    assert res_vec == []

    # Hybrid delegates to fusion and returns the candidate
    res_hyb = ret_mod._fetch_candidate_pool(
        mock_col, "query", {}, "employee", None, None, True, 5, 10, thresh=0.78
    )
    assert res_hyb == [bm25_match]
