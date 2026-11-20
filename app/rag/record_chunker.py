"""Record-aware chunking preserving atomic business records, repeating headers, and tracking metadata."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app.rag.mongo_serializer import extract_record_identifier, normalize_bson_value

_TARGET_MAX_WORDS: int = 180


@dataclass(frozen=True)
class MongoChunk:
    """Container for a record chunk and its enriched audit metadata."""

    text: str
    metadata: dict[str, str | int]
    chunk_id: str


def compute_content_hash(text: str) -> str:
    """Computes a 16-character SHA-256 content hash for chunk deduplication and tracking."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _build_base_metadata(
    doc: dict[str, Any],
    collection_name: str,
    department: str,
    access_level: str,
    record_id: str,
) -> dict[str, str | int]:
    """Assembles base metadata dictionary from source document timestamps and identifiers."""
    meta: dict[str, str | int] = {
        "source": f"mongo:{collection_name}",
        "collection_name": collection_name,
        "record_id": record_id,
        "doc_key": f"{collection_name}::{record_id}",
        "department": department,
        "access_level": access_level,
    }
    if "createdAt" in doc and doc["createdAt"] is not None:
        meta["created_at"] = str(normalize_bson_value(doc["createdAt"]))
    if "updatedAt" in doc and doc["updatedAt"] is not None:
        meta["updated_at"] = str(normalize_bson_value(doc["updatedAt"]))
    return meta


def _split_into_blocks(text: str) -> list[str]:
    """Splits serialized document into logical block sections on double newlines."""
    raw_blocks = text.strip().split("\n\n")
    return [b.strip() for b in raw_blocks if b.strip()]


def _bundle_blocks(blocks: list[str], max_words: int) -> list[str]:
    """Groups consecutive blocks together up to max_words budget per slice."""
    slices: list[str] = []
    curr: list[str] = []
    curr_len = 0

    for block in blocks:
        b_len = len(block.split())
        if curr and (curr_len + b_len > max_words):
            slices.append("\n\n".join(curr))
            curr = [block]
            curr_len = b_len
        else:
            curr.append(block)
            curr_len += b_len

    if curr:
        slices.append("\n\n".join(curr))
    return slices


def chunk_record(
    doc: dict[str, Any],
    serialized_text: str,
    collection_name: str,
    department: str,
    access_level: str,
    max_words: int = _TARGET_MAX_WORDS,
) -> list[MongoChunk]:
    """Splits a serialized MongoDB document into atomic or header-propagated record chunks."""
    record_id = extract_record_identifier(doc)
    words = serialized_text.split()
    base_meta = _build_base_metadata(doc, collection_name, department, access_level, record_id)

    # Small records remain atomic in a single chunk
    if len(words) <= max_words:
        meta = {**base_meta, "chunk_index": 0, "content_hash": compute_content_hash(serialized_text)}
        chunk_id = f"mongo:{collection_name}::{record_id}::chunk_0"
        return [MongoChunk(text=serialized_text, metadata=meta, chunk_id=chunk_id)]

    # Large records: split with repeating header context
    header = f"[Record: {record_id} | Collection: {collection_name}]\n\n"
    blocks = _split_into_blocks(serialized_text)
    bundled = _bundle_blocks(blocks, max_words=max_words)
    chunks: list[MongoChunk] = []

    for idx, body in enumerate(bundled):
        chunk_text = body if body.startswith("# Record:") else f"{header}{body}"
        meta = {
            **base_meta,
            "chunk_index": idx,
            "content_hash": compute_content_hash(chunk_text),
        }
        chunk_id = f"mongo:{collection_name}::{record_id}::chunk_{idx}"
        chunks.append(MongoChunk(text=chunk_text, metadata=meta, chunk_id=chunk_id))

    return chunks
