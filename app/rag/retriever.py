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
    score: float        # cosine similarity ∈ [0, 1] — higher is more relevant
    chunk_index: int


def _build_where_clause(role: str) -> dict[str, object]:
    """Builds the ChromaDB metadata filter; raises PermissionError on unknown roles."""
    if role not in ROLE_ACCESS:
        _logger.warning("Unrecognized role %r provided to retriever; denying access", role)
        raise PermissionError(f"Access denied: role {role!r} is not recognized")
    allowed = ROLE_ACCESS[role]
    if len(allowed) == 1:
        return {"access_level": allowed[0]}
    return {"access_level": {"$in": allowed}}


_COLLECTION_CACHE: dict[tuple[str, str], chromadb.Collection] = {}


def _open_collection() -> chromadb.Collection:
    """Opens and caches existing ChromaDB collection, verifying model compatibility."""
    key = (settings.chroma_persist_dir, settings.chroma_collection)
    if key in _COLLECTION_CACHE:
        return _COLLECTION_CACHE[key]

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_collection(
        name=settings.chroma_collection,
        embedding_function=get_embedding_function(),
    )
    stored_model = collection.metadata.get("embedding_model") if collection.metadata else None
    if stored_model and stored_model != settings.embedding_model:
        raise RuntimeError(
            f"Collection {settings.chroma_collection!r} was created with model {stored_model!r}, "
            f"which does not match configured {settings.embedding_model!r}. Never mix embeddings!"
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


def retrieve(
    query: str,
    role: str,
    score_threshold: float | None = None,
) -> list[RetrievedChunk]:
    """
    Queries ChromaDB for top-k chunks matching the query with role permissions.
    Returns [] if no chunk meets the similarity threshold or role is invalid.
    """
    from app.rag.prompt import is_greeting  # noqa: PLC0415

    if is_greeting(query):
        return []

    try:
        where = _build_where_clause(role)
        collection = _open_collection()
    except (PermissionError, Exception):
        return []

    total_docs = collection.count()
    if total_docs == 0:
        return []

    n = min(settings.rag_top_k, total_docs)
    results = collection.query(
        query_texts=[query],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = _parse_results(results)  # type: ignore[arg-type]
    thresh = settings.rag_score_threshold if score_threshold is None else score_threshold
    if thresh > 0.0 and (not chunks or chunks[0].score < thresh):
        return []

    return chunks

