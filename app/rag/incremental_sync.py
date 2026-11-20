"""Incremental synchronization pipeline between MongoDB Atlas and ChromaDB vector store."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.database import Database

from app.rag.bm25 import invalidate_bm25_cache
from app.rag.ingest import _get_collection
from app.rag.mongo_catalog import APPROVED_COLLECTIONS, CollectionSpec, audit_mongo_collections
from app.rag.mongo_ingest import _connect_mongo
from app.rag.mongo_serializer import extract_record_identifier, serialize_mongo_document
from app.rag.record_chunker import MongoChunk, chunk_record, compute_content_hash
from app.rag.retriever import invalidate_collection_cache
from app.rag.sync_state import CheckpointManager, SyncLockError, SyncLockManager

_logger = logging.getLogger("chatbot.rag.incremental_sync")


@dataclass
class SyncStats:
    """Detailed telemetry and mutation counts for a single collection sync run."""

    collection_name: str
    new_documents: int = 0
    modified_documents: int = 0
    unchanged_documents: int = 0
    deleted_documents: int = 0
    total_chunks_upserted: int = 0
    total_chunks_pruned: int = 0
    status: str = "success"
    elapsed_seconds: float = 0.0


@dataclass
class OverallSyncResult:
    """Overall summary report across all synchronized MongoDB collections."""

    collections_synced: list[SyncStats] = field(default_factory=list)
    total_new: int = 0
    total_modified: int = 0
    total_unchanged: int = 0
    total_deleted: int = 0
    total_chunks: int = 0
    status: str = "ok"
    message: str = ""
    elapsed_seconds: float = 0.0


def build_timestamp_query(
    watermark: datetime | None,
    lookback_seconds: int = 60,
    timestamp_field: str = "updatedAt",
) -> dict[str, Any]:
    """Constructs MongoDB query filter applying lookback window for clock skew tolerance."""
    if watermark is None:
        return {}
    threshold = watermark - timedelta(seconds=lookback_seconds)
    return {
        "$or": [
            {timestamp_field: {"$gte": threshold}},
            {"createdAt": {"$gte": threshold}},
        ]
    }


def prune_chunks_for_record(chroma_col: Any, collection_name: str, record_id: str) -> None:
    """Deletes all chunks belonging to a specific record using atomic doc_key matching."""
    doc_key = f"{collection_name}::{record_id}"
    try:
        chroma_col.delete(where={"doc_key": doc_key})
    except Exception as exc:
        _logger.debug("Prune chunks for doc_key %r skipped or empty: %s", doc_key, exc)


def prune_orphaned_chunks(chroma_col: Any, doc_key: str, active_chunk_ids: set[str]) -> int:
    """Deletes old chunk IDs for a record that are not present in the new chunk set."""
    try:
        res = chroma_col.get(where={"doc_key": doc_key})
        existing_ids = set(res.get("ids", []))
        orphaned = list(existing_ids - active_chunk_ids)
        if orphaned:
            chroma_col.delete(ids=orphaned)
            return len(orphaned)
    except Exception as exc:
        _logger.debug("Orphaned chunk cleanup skipped for %s: %s", doc_key, exc)
    return 0


def upsert_record_chunks(chroma_col: Any, chunks: list[MongoChunk], batch_size: int = 100) -> int:
    """Upserts generated record chunks into ChromaDB in configured batch sizes."""
    if not chunks:
        return 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        chroma_col.upsert(
            documents=[c.text for c in batch],
            ids=[c.chunk_id for c in batch],
            metadatas=[c.metadata for c in batch],
        )
    return len(chunks)


def reconcile_collection_deletions(
    db: Database[dict[str, Any]],
    spec: CollectionSpec,
    chroma_col: Any,
    known_hashes: dict[str, str],
) -> tuple[int, list[str]]:
    """Identifies and purges ChromaDB chunks for documents removed from MongoDB collection."""
    if not known_hashes:
        return 0, []
    try:
        total_count = db[spec.name].count_documents({})
    except Exception as exc:
        _logger.error("Failed counting docs in %s; aborting deletion reconciliation: %s", spec.name, exc)
        return 0, []

    active_cursor = db[spec.name].find({}, {"_id": 1, "recordId": 1, "invoiceNumber": 1, "code": 1})
    active_ids = {extract_record_identifier(doc) for doc in active_cursor if isinstance(doc, dict)}

    if total_count > 0 and len(active_ids) == 0:
        _logger.error(
            "Safety guard triggered: %s has %d docs in Mongo, but cursor returned 0. Aborting deletion reconciliation.",
            spec.name,
            total_count,
        )
        return 0, []

    deleted_ids = [rid for rid in known_hashes if rid not in active_ids]
    for rid in deleted_ids:
        prune_chunks_for_record(chroma_col, spec.name, rid)

    return len(deleted_ids), deleted_ids


def _collection_has_timestamps(db: Database[dict[str, Any]], collection_name: str) -> bool:
    """Checks whether at least one document in collection has updatedAt or createdAt."""
    sample = db[collection_name].find_one(
        {"$or": [{"updatedAt": {"$exists": True}}, {"createdAt": {"$exists": True}}]},
        {"_id": 1},
    )
    return sample is not None


def _process_candidate_doc(
    doc: dict[str, Any],
    spec: CollectionSpec,
    chroma_col: Any,
    current_hashes: dict[str, str],
    stats: SyncStats,
) -> list[MongoChunk]:
    """Evaluates a single document for changes, prunes stale chunks if modified, and returns new chunks."""
    record_id = extract_record_identifier(doc)
    serialized = serialize_mongo_document(doc, collection_name=spec.name)
    doc_hash = compute_content_hash(serialized)

    if record_id in current_hashes and current_hashes[record_id] == doc_hash:
        stats.unchanged_documents += 1
        return []

    is_modified = record_id in current_hashes
    chunks = chunk_record(
        doc,
        serialized,
        collection_name=spec.name,
        department=spec.department,
        access_level=spec.access_level,
    )

    upsert_record_chunks(chroma_col, chunks)

    if is_modified:
        stats.modified_documents += 1
        doc_key = f"{spec.name}::{record_id}"
        pruned = prune_orphaned_chunks(chroma_col, doc_key, {c.chunk_id for c in chunks})
        stats.total_chunks_pruned += pruned
    else:
        stats.new_documents += 1

    stats.total_chunks_upserted += len(chunks)
    current_hashes[record_id] = doc_hash
    return chunks


def sync_collection_incremental(
    db: Database[dict[str, Any]],
    spec: CollectionSpec,
    chroma_col: Any,
    checkpoint_mgr: CheckpointManager,
    lookback_seconds: int = 60,
    force_full: bool = False,
) -> SyncStats:
    """Incrementally synchronizes a single collection detecting inserts, updates, and deletions."""
    start_time = time.time()
    sync_run_time = datetime.now(timezone.utc)
    checkpoint = checkpoint_mgr.get_checkpoint(spec.name)
    watermark = None if force_full else checkpoint_mgr.get_watermark(spec.name)

    stats = SyncStats(collection_name=spec.name)
    current_hashes = dict(checkpoint.record_hashes)
    has_ts = _collection_has_timestamps(db, spec.name)
    mongo_filter = build_timestamp_query(watermark, lookback_seconds) if (watermark and has_ts) else {}

    for doc in db[spec.name].find(mongo_filter):
        if isinstance(doc, dict) and doc:
            _process_candidate_doc(doc, spec, chroma_col, current_hashes, stats)

    del_count, deleted_ids = reconcile_collection_deletions(db, spec, chroma_col, current_hashes)
    stats.deleted_documents = del_count
    for rid in deleted_ids:
        current_hashes.pop(rid, None)

    checkpoint_mgr.update_checkpoint(
        spec.name,
        sync_run_time,
        current_hashes,
        len(current_hashes),
        stats.total_chunks_upserted,
    )
    stats.elapsed_seconds = round(time.time() - start_time, 3)
    return stats


def _sync_all_collections(
    db: Database[dict[str, Any]],
    chroma_col: Any,
    checkpoint_mgr: CheckpointManager,
    lock_mgr: SyncLockManager,
    force_full: bool,
) -> OverallSyncResult:
    """Iterates through approved collections executing incremental sync and renewing lock heartbeat."""
    res = OverallSyncResult()
    for spec in APPROVED_COLLECTIONS.values():
        try:
            stats = sync_collection_incremental(db, spec, chroma_col, checkpoint_mgr, force_full=force_full)
            res.collections_synced.append(stats)
            res.total_new += stats.new_documents
            res.total_modified += stats.modified_documents
            res.total_unchanged += stats.unchanged_documents
            res.total_deleted += stats.deleted_documents
            res.total_chunks += stats.total_chunks_upserted
            lock_mgr.heartbeat()
        except Exception as exc:
            _logger.error("Failed syncing collection %s: %s", spec.name, exc)
            res.collections_synced.append(SyncStats(collection_name=spec.name, status=f"error: {exc}"))
    return res


def sync_mongodb_incremental(
    purge_legacy: bool = False,
    force_full: bool = False,
    lock_ttl: int = 600,
) -> OverallSyncResult:
    """Coordinates full or incremental synchronization across all approved MongoDB collections."""
    start_time = time.time()
    lock_mgr = SyncLockManager(ttl_seconds=lock_ttl)
    lock_mgr.acquire()

    try:
        db = _connect_mongo()
        chroma_col = _get_collection()
        checkpoint_mgr = CheckpointManager()
        audit_mongo_collections(db)

        res = _sync_all_collections(db, chroma_col, checkpoint_mgr, lock_mgr, force_full)
        invalidate_collection_cache()
        invalidate_bm25_cache()

        res.elapsed_seconds = round(time.time() - start_time, 3)
        res.message = (
            f"Synced {len(res.collections_synced)} collections: "
            f"{res.total_new} new, {res.total_modified} modified, "
            f"{res.total_unchanged} unchanged, {res.total_deleted} deleted. "
            f"Total chunks upserted: {res.total_chunks}."
        )
        return res
    finally:
        lock_mgr.release()
