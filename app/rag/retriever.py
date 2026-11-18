"""
Top-k semantic search over ChromaDB with permission filtering at query time
and a minimum similarity threshold gate.

Permission filtering happens HERE — inside the ChromaDB where-clause —
never after the LLM has seen the results.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import chromadb

from app.config import settings
from app.rag.embedder import get_embedding_function

_logger = logging.getLogger("chatbot.retriever")

# Maps each permitted role to its allowed access levels.
# Single source of truth for all role definitions across the application.
ROLE_ACCESS: dict[str, list[str]] = {
    "employee": ["all"],
    "hr": ["all", "hr"],
}


@dataclass(frozen=True)
class RetrievedChunk:
    """Immutable container for a single retrieved document chunk."""

    text: str
    source: str
    department: str
    access_level: str
    score: float | None  # cosine similarity ∈ [0, 1] — higher is more relevant, or None
    chunk_index: int
    rerank_score: float | None = None  # cross-encoder score ∈ [0, 1], or None


def _build_where_clause(role: str) -> dict[str, object]:
    """Builds the ChromaDB metadata filter; raises PermissionError on unknown roles."""
    if role not in ROLE_ACCESS:
        _logger.warning("Unrecognized role %r provided to retriever; denying access", role)
        raise PermissionError(f"Access denied: role {role!r} is not recognized")
    allowed = ROLE_ACCESS[role]
    if len(allowed) == 1:
        return {"access_level": allowed[0]}
    return {"access_level": {"$in": allowed}}


def get_collection_name(
    mode: str | None = None, table_max_rows: int | None = None
) -> str:
    """Returns the ChromaDB collection name including embedding model and chunking mode."""
    active_mode = mode or settings.chunking_mode
    active_rows = (
        table_max_rows
        if table_max_rows is not None
        else settings.table_max_rows_per_chunk
    )
    base = settings.chroma_collection
    for m in ("section", "hierarchical_t4", "hierarchical"):
        if base.endswith(f"_{m}"):
            base = base[: -len(m)].rstrip("_")
            break
    suffix = (
        f"{active_mode}_t{active_rows}"
        if (active_mode == "hierarchical" and active_rows > 0)
        else active_mode
    )
    return f"{base}_{suffix}"


_COLLECTION_CACHE: dict[tuple[str, str], chromadb.Collection] = {}


def _open_collection(
    mode: str | None = None, table_max_rows: int | None = None
) -> chromadb.Collection:
    """Opens and caches existing ChromaDB collection, verifying model and mode compatibility."""
    active_mode = mode or settings.chunking_mode
    active_rows = (
        table_max_rows
        if table_max_rows is not None
        else settings.table_max_rows_per_chunk
    )
    col_name = get_collection_name(active_mode, table_max_rows=active_rows)
    key = (settings.chroma_persist_dir, col_name)
    if key in _COLLECTION_CACHE:
        return _COLLECTION_CACHE[key]

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_collection(
        name=col_name,
        embedding_function=get_embedding_function(),
    )
    if collection.metadata:
        stored_model = collection.metadata.get("embedding_model")
        if stored_model and stored_model != settings.embedding_model:
            raise RuntimeError(
                f"Collection {col_name!r} was created with model {stored_model!r}, "
                f"which does not match configured {settings.embedding_model!r}. Never mix embeddings!"
            )
        stored_mode = collection.metadata.get("chunking_mode")
        if stored_mode and stored_mode != active_mode:
            raise RuntimeError(
                f"Collection {col_name!r} was created with chunking mode {stored_mode!r}, "
                f"which does not match configured {active_mode!r}."
            )
    _COLLECTION_CACHE[key] = collection
    return collection


def _parse_results(
    results: dict[str, list[list[object]]],  # Chroma query results structure
) -> list[RetrievedChunk]:
    """Converts raw ChromaDB query results into a sorted list of RetrievedChunk objects."""
    documents: list[str] = results["documents"][0]  # type: ignore[assignment]
    metadatas: list[dict[str, object]] = results["metadatas"][0]  # type: ignore[assignment]
    distances: list[float] = results["distances"][0]  # type: ignore[assignment]

    chunks: list[RetrievedChunk] = []
    for doc, meta, dist in zip(documents, metadatas, distances):
        score = round(1.0 - dist, 4)
        chunks.append(
            RetrievedChunk(
                text=doc,
                source=str(meta.get("source", "unknown")),
                department=str(meta.get("department", "")),
                access_level=str(meta.get("access_level", "")),
                score=score,
                chunk_index=int(meta.get("chunk_index", 0)),  # type: ignore[arg-type]
            )
        )
    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks


def invalidate_collection_cache() -> None:
    """Clears in-memory ChromaDB collection handles and BM25 index cache."""
    _COLLECTION_CACHE.clear()
    from app.rag.bm25 import invalidate_bm25_cache  # noqa: PLC0415

    invalidate_bm25_cache()


def _build_bm25_chunk(
    chunk: RetrievedChunk,
    query_embedding: list[float] | None,
    chunk_embeddings: dict[tuple[str, int], list[float]] | None,
    key: tuple[str, int],
) -> RetrievedChunk:
    """Builds a RetrievedChunk with true vector similarity or None if uncomputed."""
    import numpy as np  # noqa: PLC0415

    true_score: float | None = None
    if (
        query_embedding is not None
        and chunk_embeddings is not None
        and key in chunk_embeddings
    ):
        true_score = round(float(np.dot(query_embedding, chunk_embeddings[key])), 4)
    return RetrievedChunk(
        text=chunk.text,
        source=chunk.source,
        department=chunk.department,
        access_level=chunk.access_level,
        score=true_score,
        chunk_index=chunk.chunk_index,
    )


def _fuse_reciprocal_rank(
    vector_chunks: list[RetrievedChunk],
    bm25_chunks: list[RetrievedChunk],
    query_embedding: list[float] | None = None,
    chunk_embeddings: dict[tuple[str, int], list[float]] | None = None,
    rrf_k: float = 60.0,
    candidate_limit: int = 8,
) -> list[RetrievedChunk]:
    """Combines vector and BM25 candidate lists using Reciprocal Rank Fusion."""
    from collections import Counter  # noqa: PLC0415

    rrf_scores: Counter[tuple[str, int]] = Counter()
    chunk_map: dict[tuple[str, int], RetrievedChunk] = {}

    for rank, chunk in enumerate(vector_chunks, start=1):
        key = (chunk.source, chunk.chunk_index)
        rrf_scores[key] += 1.0 / (rrf_k + rank)
        chunk_map[key] = chunk

    for rank, chunk in enumerate(bm25_chunks, start=1):
        key = (chunk.source, chunk.chunk_index)
        rrf_scores[key] += 1.0 / (rrf_k + rank)
        if key not in chunk_map:
            chunk_map[key] = _build_bm25_chunk(
                chunk, query_embedding, chunk_embeddings, key
            )

    fused_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
    return [chunk_map[k] for k in fused_keys[:candidate_limit]]


def _fuse_query_lists(
    list_a: list[RetrievedChunk],
    list_b: list[RetrievedChunk],
    candidate_limit: int,
    rrf_k: int = 60,
) -> list[RetrievedChunk]:
    """Combines two candidate lists from multi-query retrieval using Reciprocal Rank Fusion."""
    rrf_scores: dict[tuple[str, int], float] = {}
    chunk_map: dict[tuple[str, int], RetrievedChunk] = {}

    for rank, chunk in enumerate(list_a, start=1):
        key = (chunk.source, chunk.chunk_index)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
        chunk_map[key] = chunk

    for rank, chunk in enumerate(list_b, start=1):
        key = (chunk.source, chunk.chunk_index)
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
        if key not in chunk_map or (chunk.score is not None and (chunk_map[key].score is None or chunk.score > chunk_map[key].score)):
            chunk_map[key] = chunk

    fused_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)
    return [chunk_map[k] for k in fused_keys[:candidate_limit]]


def _query_vector_store(
    collection: chromadb.Collection, query: str, where: dict[str, object], n: int
) -> list[RetrievedChunk]:
    """Queries collection and parses results into sorted RetrievedChunk objects."""
    results = collection.query(
        query_texts=[query],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return _parse_results(results)  # type: ignore[arg-type]


def _execute_hybrid_fusion(
    vector_chunks: list[RetrievedChunk],
    query: str,
    role: str,
    mode: str | None,
    table_max_rows: int | None,
    cand_limit: int,
    req_k: int,
) -> list[RetrievedChunk]:
    """Retrieves BM25 candidates and fuses with vector candidates using RRF."""
    from app.rag.bm25 import get_bm25_index  # noqa: PLC0415

    col_name = get_collection_name(mode, table_max_rows=table_max_rows)
    bm25_index = get_bm25_index(col_name, role)
    bm25_chunks = bm25_index.search(query, top_n=settings.hybrid_candidates)

    embed_fn = get_embedding_function()
    embed_query = getattr(embed_fn, "embed_query", embed_fn)
    query_emb = list(embed_query([query])[0])

    fused = _fuse_reciprocal_rank(
        vector_chunks,
        bm25_chunks,
        query_embedding=query_emb,
        chunk_embeddings=bm25_index.embeddings,
        candidate_limit=cand_limit,
    )
    return fused[:req_k]


def _apply_rerank_and_grade(
    candidates: list[RetrievedChunk],
    query: str,
    reranker_model: str | None,
    is_grader: bool,
    rerank_min_score: float | None,
    thresh: float,
    req_k: int,
) -> list[RetrievedChunk]:
    """Reranks role-filtered candidate chunks and applies non-LLM confidence grader."""
    from app.rag.reranker import rerank_chunks  # noqa: PLC0415

    model_key = reranker_model or settings.reranker_model
    reranked = rerank_chunks(query, candidates, model_key=model_key, top_k=len(candidates))
    if not is_grader:
        return reranked[:req_k]

    min_score = (
        settings.rerank_min_score if rerank_min_score is None else rerank_min_score
    )
    graded = [
        c
        for c in reranked
        if (c.rerank_score is not None and c.rerank_score >= min_score)
        and (c.score is None or thresh <= 0.0 or c.score >= thresh)
    ]
    return graded[:req_k]


def _fetch_candidate_pool(
    collection: chromadb.Collection,
    query: str,
    where: dict[str, object],
    role: str,
    mode: str | None,
    table_max_rows: int | None,
    is_hybrid: bool,
    cand_count: int,
    cand_limit: int,
    thresh: float,
) -> list[RetrievedChunk]:
    """Retrieves and gates initial vector/hybrid candidate chunks."""
    vector_chunks = _query_vector_store(
        collection, query, where, min(cand_limit, collection.count())
    )
    if thresh > 0.0 and (not vector_chunks or vector_chunks[0].score is None or vector_chunks[0].score < thresh):
        return []
    if is_hybrid:
        return _execute_hybrid_fusion(
            vector_chunks, query, role, mode, table_max_rows, cand_limit, cand_count
        )
    return vector_chunks[:cand_count]


def _open_accessible_collection(
    role: str, mode: str | None, table_max_rows: int | None
) -> tuple[chromadb.Collection, dict[str, object]] | None:
    """Opens ChromaDB collection and builds role permission filter, returning None on access failure."""
    try:
        where = _build_where_clause(role)
        collection = _open_collection(mode=mode, table_max_rows=table_max_rows)
    except (PermissionError, Exception):
        return None
    return (collection, where) if collection.count() > 0 else None


def _fuse_secondary_query(
    candidates: list[RetrievedChunk],
    collection: chromadb.Collection,
    original_query: str | None,
    query: str,
    where: dict[str, object],
    role: str,
    mode: str | None,
    table_max_rows: int | None,
    is_hybrid: bool,
    cand_count: int,
    cand_limit: int,
    thresh: float,
) -> list[RetrievedChunk]:
    """Retrieves candidates for secondary original query and fuses via RRF in both mode."""
    from app.rag.prompt import is_greeting  # noqa: PLC0415

    if (
        settings.rewrite_mode == "both"
        and original_query
        and original_query.strip().lower() != query.strip().lower()
        and not is_greeting(original_query)
    ):
        orig_cands = _fetch_candidate_pool(
            collection, original_query, where, role, mode, table_max_rows, is_hybrid, cand_count, cand_limit, thresh
        )
        return _fuse_query_lists(candidates, orig_cands, candidate_limit=cand_count)
    return candidates


def retrieve(
    query: str,
    role: str,
    score_threshold: float | None = None,
    mode: str | None = None,
    table_max_rows: int | None = None,
    top_k: int | None = None,
    hybrid_search: bool | None = None,
    reranker: bool | None = None,
    reranker_model: str | None = None,
    grader: bool | None = None,
    rerank_min_score: float | None = None,
    original_query: str | None = None,
) -> list[RetrievedChunk]:
    """Queries ChromaDB with role permissions, fusing BM25, reranking, and grading."""
    from app.rag.prompt import is_greeting  # noqa: PLC0415

    col_info = _open_accessible_collection(role, mode, table_max_rows)
    if is_greeting(query) or col_info is None:
        return []
    collection, where = col_info

    is_hyb = settings.hybrid_search if hybrid_search is None else hybrid_search
    is_rr = settings.reranker if reranker is None else reranker
    is_gr = settings.grader if grader is None else grader

    req_k = top_k if top_k is not None else (settings.rerank_top_k if is_rr else settings.rag_top_k)
    cand_cnt = settings.rerank_candidates if is_rr else req_k
    cand_lim = max(cand_cnt, settings.hybrid_candidates) if is_hyb else cand_cnt
    thresh = settings.rag_score_threshold if score_threshold is None else score_threshold

    candidates = _fetch_candidate_pool(
        collection, query, where, role, mode, table_max_rows, is_hyb, cand_cnt, cand_lim, thresh
    )
    candidates = _fuse_secondary_query(
        candidates, collection, original_query, query, where, role, mode, table_max_rows, is_hyb, cand_cnt, cand_lim, thresh
    )
    if not candidates or not is_rr:
        return candidates[:req_k]
    return _apply_rerank_and_grade(candidates, query, reranker_model, is_gr, rerank_min_score, thresh, req_k)

