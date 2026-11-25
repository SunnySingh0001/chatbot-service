"""
Pure-Python Okapi BM25 implementation with role-based security isolation.

Role filtering happens BEFORE indexing and scoring: unauthorized chunks
never enter the BM25 index, candidate list, or score calculations.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.rag.retriever import ROLE_ACCESS, RetrievedChunk, _build_where_clause

TOKEN_PATTERN = re.compile(
    r"\d+(?:\.\d+)+|[\u0900-\u0963\u0970-\u097F\w]+", re.UNICODE
)


def tokenize_bm25(text: str) -> list[str]:
    """Tokenizes text preserving dot-separated numbers, acronyms, and Hindi characters."""
    return TOKEN_PATTERN.findall(text.lower())


class BM25Index:
    """In-memory Okapi BM25 index over a fixed set of pre-filtered chunks."""

    def __init__(
        self,
        corpus: list[RetrievedChunk],
        embeddings: dict[tuple[str, int], list[float]] | None = None,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """Initializes BM25 index with precomputed document frequencies and length statistics."""
        self.corpus = corpus
        self.embeddings: dict[tuple[str, int], list[float]] = embeddings or {}
        self.k1 = k1
        self.b = b
        self.doc_lens = [len(tokenize_bm25(c.text)) for c in corpus]
        self.avgdl = (
            sum(self.doc_lens) / len(self.doc_lens) if self.doc_lens else 0.0
        )
        self.n_docs = len(corpus)

        self.term_freqs: list[Counter[str]] = [
            Counter(tokenize_bm25(c.text)) for c in corpus
        ]
        self.doc_freqs: Counter[str] = Counter()
        for tf in self.term_freqs:
            for term in tf:
                self.doc_freqs[term] += 1

        self.idf: dict[str, float] = {
            t: math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))
            for t, df in self.doc_freqs.items()
        }

    def search(self, query: str, top_n: int = 8) -> list[RetrievedChunk]:
        """Scores corpus documents using BM25 and returns the top-n matching chunks."""
        tokens = tokenize_bm25(query)
        if not tokens or self.n_docs == 0:
            return []

        scored: list[tuple[int, float]] = []
        for idx, (tf, dl) in enumerate(zip(self.term_freqs, self.doc_lens)):
            score = 0.0
            norm = 1.0 - self.b + self.b * (dl / self.avgdl)
            for t in tokens:
                if t in tf:
                    numerator = tf[t] * (self.k1 + 1.0)
                    denominator = tf[t] + self.k1 * norm
                    score += self.idf.get(t, 0.0) * (numerator / denominator)
            if score > 0.0:
                scored.append((idx, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        return [self.corpus[idx] for idx, _ in scored[:top_n]]


_BM25_CACHE: dict[tuple[str, str, str, str], BM25Index] = {}


def _fetch_role_chunks(
    collection_name: str,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> tuple[list[RetrievedChunk], dict[tuple[str, int], list[float]]]:
    """Queries ChromaDB with role, client, and company filters before indexing BM25."""
    import chromadb  # noqa: PLC0415
    from app.config import settings  # noqa: PLC0415

    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_collection(name=collection_name)
    where = _build_where_clause(role, client_id=client_id, company_id=company_id)
    data = collection.get(
        where=where, include=["documents", "metadatas", "embeddings"]
    )

    chunks: list[RetrievedChunk] = []
    embeddings: dict[tuple[str, int], list[float]] = {}
    documents = data.get("documents") or []
    metadatas = data.get("metadatas") or []
    raw_embs = data.get("embeddings")
    embs_list = [] if raw_embs is None else raw_embs

    for doc, meta, emb in zip(documents, metadatas, embs_list):
        source = str(meta.get("source", "unknown"))
        idx = int(meta.get("chunk_index", 0))  # type: ignore[arg-type]
        chunks.append(
            RetrievedChunk(
                text=doc,
                source=source,
                department=str(meta.get("department", "")),
                access_level=str(meta.get("access_level", "")),
                score=None,
                chunk_index=idx,
            )
        )
        if emb is not None:
            embeddings[(source, idx)] = list(emb)
    return chunks, embeddings


def get_bm25_index(
    collection_name: str,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> BM25Index:
    """Retrieves or constructs the role- and tenant-isolated BM25 index from ChromaDB."""
    if role not in ROLE_ACCESS:
        raise PermissionError(f"Access denied: role {role!r} is not recognized")

    key = (collection_name, role, client_id or "", company_id or "")
    if key in _BM25_CACHE:
        return _BM25_CACHE[key]

    chunks, embeddings = _fetch_role_chunks(
        collection_name, role, client_id=client_id, company_id=company_id
    )
    index = BM25Index(corpus=chunks, embeddings=embeddings)
    _BM25_CACHE[key] = index
    return index


def invalidate_bm25_cache() -> None:
    """Clears all in-memory BM25 index instances across collections and roles."""
    _BM25_CACHE.clear()
