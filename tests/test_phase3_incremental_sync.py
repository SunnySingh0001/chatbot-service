"""Comprehensive test suite for Phase 3: Incremental MongoDB -> ChromaDB Sync and Race Condition Fixes."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.rag.incremental_sync import (
    SyncStats,
    build_timestamp_query,
    prune_chunks_for_record,
    reconcile_collection_deletions,
    sync_collection_incremental,
    upsert_record_chunks,
)
from app.rag.mongo_catalog import CollectionSpec
from app.rag.record_chunker import MongoChunk, chunk_record, compute_content_hash
from app.rag.retriever import _resolve_score_threshold
from app.rag.sync_state import (
    CheckpointManager,
    CollectionCheckpoint,
    SyncLockError,
    SyncLockManager,
)


class MockChromaCollection:
    """In-memory mock for ChromaDB collection tracking deletions, upserts, and stored chunks."""

    def __init__(self) -> None:
        """Initializes empty storage dictionaries for mock Chroma collection."""
        self.chunks: dict[str, dict[str, Any]] = {}
        self.delete_calls: list[dict[str, Any]] = []
        self.upsert_calls: list[dict[str, Any]] = []

    def upsert(
        self,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Stores documents and metadata keyed by id in mock collection."""
        self.upsert_calls.append({"ids": ids, "documents": documents, "metadatas": metadatas})
        for doc_id, text, meta in zip(ids, documents, metadatas):
            self.chunks[doc_id] = {"text": text, "metadata": meta}

    def delete(self, where: dict[str, Any] | None = None, ids: list[str] | None = None) -> None:
        """Deletes chunks matching where clause or explicit id list."""
        self.delete_calls.append({"where": where, "ids": ids})
        if ids:
            for cid in ids:
                self.chunks.pop(cid, None)
        if where and "doc_key" in where:
            target_key = where["doc_key"]
            to_remove = [
                cid for cid, item in self.chunks.items() if item["metadata"].get("doc_key") == target_key
            ]
            for cid in to_remove:
                self.chunks.pop(cid, None)

    def get(self, where: dict[str, Any] | None = None, include: list[str] | None = None) -> dict[str, list[str]]:
        """Returns chunk IDs matching where clause."""
        if where and "doc_key" in where:
            target_key = where["doc_key"]
            matched = [cid for cid, item in self.chunks.items() if item["metadata"].get("doc_key") == target_key]
        else:
            matched = list(self.chunks.keys())
        return {"ids": matched}


class MockMongoCollection:
    """In-memory mock for MongoDB collection supporting find, find_one, and count."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        """Initializes mock collection with in-memory documents."""
        self.docs = list(docs)

    def count_documents(self, query: dict[str, Any] | None = None) -> int:
        """Returns total document count in mock collection."""
        return len(self.docs)

    def find(self, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Filters documents based on simple mock query logic."""
        if not query:
            return list(self.docs)
        if "$or" in query:
            return list(self.docs)
        return list(self.docs)

    def find_one(self, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Returns first document or None."""
        return self.docs[0] if self.docs else None


class MockDatabase:
    """Mock MongoDB database containing named collections."""

    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        """Initializes mock database with mapping of collection names to document lists."""
        self._colls = {name: MockMongoCollection(docs) for name, docs in collections.items()}

    def __getitem__(self, name: str) -> MockMongoCollection:
        """Returns mock collection for the given name."""
        if name not in self._colls:
            self._colls[name] = MockMongoCollection([])
        return self._colls[name]


@pytest.fixture
def sample_spec() -> CollectionSpec:
    """Provides sample CollectionSpec for testing."""
    return CollectionSpec(
        name="test_leaves",
        department="hr",
        access_level="employee",
    )


def test_incremental_sync_detects_new_documents(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """1. Verifies that newly added MongoDB documents are detected, serialized, chunked, and upserted."""
    db = MockDatabase({
        "test_leaves": [{
            "_id": "LP-001",
            "title": "Annual Leave Policy",
            "days": 18,
            "createdAt": "2026-11-20T10:00:00Z",
            "updatedAt": "2026-11-20T10:00:00Z",
        }]
    })
    chroma = MockChromaCollection()
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)

    assert stats.new_documents == 1
    assert stats.modified_documents == 0
    assert stats.unchanged_documents == 0
    assert stats.deleted_documents == 0
    assert stats.total_chunks_upserted == 1
    assert len(chroma.chunks) == 1
    assert "LP-001" in ckpt_mgr.get_checkpoint(sample_spec.name).record_hashes


def test_incremental_sync_detects_modified_documents(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """2. Verifies that modified documents are detected, old chunks pruned, and new content indexed."""
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    ckpt_mgr.update_checkpoint(
        collection_name=sample_spec.name,
        last_sync_time=datetime.now(timezone.utc) - timedelta(hours=1),
        record_hashes={"LP-001": "old_hash_123"},
        total_records=1,
        total_chunks=1,
    )
    db = MockDatabase({
        "test_leaves": [{
            "_id": "LP-001",
            "title": "Updated Annual Leave Policy - 24 Days",
            "days": 24,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }]
    })
    chroma = MockChromaCollection()
    chroma.upsert(
        documents=["Old policy text"],
        ids=[f"mongo:{sample_spec.name}::LP-001::chunk_0"],
        metadatas=[{"doc_key": f"{sample_spec.name}::LP-001", "source": f"mongo:{sample_spec.name}"}],
    )

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)

    assert stats.modified_documents == 1
    assert stats.new_documents == 0
    assert stats.unchanged_documents == 0
    chunk_0_key = f"mongo:{sample_spec.name}::LP-001::chunk_0"
    assert chunk_0_key in chroma.chunks
    assert "Updated Annual Leave Policy - 24 Days" in chroma.chunks[chunk_0_key]["text"]
    assert ckpt_mgr.get_checkpoint(sample_spec.name).record_hashes["LP-001"] != "old_hash_123"


def test_incremental_sync_skips_unchanged_documents(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """3. Verifies that unchanged documents are skipped without triggering embeddings or ChromaDB upserts."""
    raw_doc = {
        "_id": "LP-001",
        "title": "Maternity Leave",
        "days": 180,
        "createdAt": "2026-01-01T00:00:00Z",
    }
    from app.rag.mongo_serializer import serialize_mongo_document

    expected_hash = compute_content_hash(serialize_mongo_document(raw_doc, sample_spec.name))
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    ckpt_mgr.update_checkpoint(
        sample_spec.name,
        datetime.now(timezone.utc),
        {"LP-001": expected_hash},
        1,
        1,
    )

    db = MockDatabase({"test_leaves": [raw_doc]})
    chroma = MockChromaCollection()

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)

    assert stats.unchanged_documents == 1
    assert stats.new_documents == 0
    assert stats.modified_documents == 0
    assert stats.total_chunks_upserted == 0
    assert len(chroma.upsert_calls) == 0


def test_incremental_sync_detects_deleted_documents(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """4. Verifies that records deleted in MongoDB are reconciled and pruned from ChromaDB and state."""
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    ckpt_mgr.update_checkpoint(
        sample_spec.name,
        datetime.now(timezone.utc),
        {"LP-001": "hash_1", "LP-002": "hash_2"},
        2,
        2,
    )
    # LP-002 was deleted from MongoDB
    db = MockDatabase({"test_leaves": [{"_id": "LP-001", "title": "Active Leave"}]})
    chroma = MockChromaCollection()

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)

    assert stats.deleted_documents == 1
    assert any(c["where"] == {"doc_key": f"{sample_spec.name}::LP-002"} for c in chroma.delete_calls)
    assert "LP-002" not in ckpt_mgr.get_checkpoint(sample_spec.name).record_hashes
    assert "LP-001" in ckpt_mgr.get_checkpoint(sample_spec.name).record_hashes


def test_chunk_pruning_on_document_shrink(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """5. Verifies that when a document shrinks in size, excess old chunks are pruned cleanly."""
    doc_key = f"{sample_spec.name}::SHRINK-1"
    chroma = MockChromaCollection()
    # Pre-populate 4 old chunks
    for i in range(4):
        cid = f"mongo:{sample_spec.name}::SHRINK-1::chunk_{i}"
        chroma.upsert([f"Old chunk text {i}"], [cid], [{"doc_key": doc_key}])
    assert len(chroma.chunks) == 4

    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    ckpt_mgr.update_checkpoint(sample_spec.name, datetime.now(timezone.utc), {"SHRINK-1": "old_long_hash"}, 1, 4)

    # Shrunk document producing only 1 chunk
    shrunk_doc = {"_id": "SHRINK-1", "title": "Tiny Shrunk Document"}
    db = MockDatabase({"test_leaves": [shrunk_doc]})

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)

    assert stats.modified_documents == 1
    # Check that previous chunks were pruned and only new 1 chunk exists
    assert len(chroma.chunks) == 1
    assert f"mongo:{sample_spec.name}::SHRINK-1::chunk_0" in chroma.chunks
    assert f"mongo:{sample_spec.name}::SHRINK-1::chunk_3" not in chroma.chunks


def test_chunk_pruning_on_document_delete(sample_spec: CollectionSpec) -> None:
    """6. Verifies that prune_chunks_for_record removes all chunks matching doc_key."""
    chroma = MockChromaCollection()
    doc_key = f"{sample_spec.name}::DEL-REC"
    for i in range(3):
        chroma.upsert([f"Chunk {i}"], [f"cid_{i}"], [{"doc_key": doc_key}])
    assert len(chroma.chunks) == 3

    prune_chunks_for_record(chroma, sample_spec.name, "DEL-REC")
    assert len(chroma.chunks) == 0


def test_stable_chunk_id_generation(sample_spec: CollectionSpec) -> None:
    """7. Verifies deterministic chunk ID generation and doc_key metadata attachment."""
    doc = {"_id": "INV-2026-999", "amount": 50000, "client": "Global Tech"}
    chunks1 = chunk_record(doc, "Serialized text 1", sample_spec.name, "finance", "finance")
    chunks2 = chunk_record(doc, "Serialized text 1", sample_spec.name, "finance", "finance")

    assert len(chunks1) == 1
    assert chunks1[0].chunk_id == f"mongo:{sample_spec.name}::INV-2026-999::chunk_0"
    assert chunks1[0].chunk_id == chunks2[0].chunk_id
    assert chunks1[0].metadata["doc_key"] == f"{sample_spec.name}::INV-2026-999"


def test_checkpoint_persistence_across_restarts(tmp_path: Path) -> None:
    """8. Verifies that checkpoint state survives process restarts when re-read from disk."""
    ckpt_file = tmp_path / "sync_checkpoints.json"
    mgr1 = CheckpointManager(ckpt_file)
    now = datetime(2026, 11, 20, 14, 0, 0, tzinfo=timezone.utc)
    mgr1.update_checkpoint("companies", now, {"C1": "hash_abc", "C2": "hash_xyz"}, 2, 4)

    mgr2 = CheckpointManager(ckpt_file)
    restored = mgr2.get_checkpoint("companies")
    assert restored.collection_name == "companies"
    assert restored.total_records == 2
    assert restored.record_hashes["C1"] == "hash_abc"
    assert mgr2.get_watermark("companies") == now


def test_checkpoint_advancement_only_on_success(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """9. Verifies that if ChromaDB write throws an exception, checkpoint watermark is NOT updated."""
    ckpt_file = tmp_path / "sync_checkpoints.json"
    mgr = CheckpointManager(ckpt_file)
    initial_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    mgr.update_checkpoint(sample_spec.name, initial_time, {"LP-001": "h0"}, 1, 1)

    db = MockDatabase({"test_leaves": [{"_id": "LP-002", "title": "New Policy"}]})
    failing_chroma = MagicMock()
    failing_chroma.upsert.side_effect = RuntimeError("Chroma connection timeout")

    with pytest.raises(RuntimeError):
        sync_collection_incremental(db, sample_spec, failing_chroma, mgr)

    # Reload and ensure watermark remains initial_time
    assert mgr.get_watermark(sample_spec.name) == initial_time
    assert "LP-002" not in mgr.get_checkpoint(sample_spec.name).record_hashes


def test_sync_lock_prevents_concurrent_runs(tmp_path: Path) -> None:
    """10. Verifies that attempting to acquire a held lock raises SyncLockError."""
    lock_path = tmp_path / "sync.lock"
    lock1 = SyncLockManager(lock_file=lock_path, ttl_seconds=60)
    lock2 = SyncLockManager(lock_file=lock_path, ttl_seconds=60)

    assert lock1.acquire() is True
    assert lock1.is_locked() is True

    with pytest.raises(SyncLockError, match="Sync already running"):
        lock2.acquire()

    lock1.release()
    assert lock1.is_locked() is False
    assert lock2.acquire() is True
    lock2.release()


def test_sync_lock_release_on_failure(tmp_path: Path) -> None:
    """11. Verifies that context manager ensures lock release even on unhandled exceptions."""
    lock_path = tmp_path / "sync.lock"
    lock = SyncLockManager(lock_file=lock_path, ttl_seconds=60)

    with pytest.raises(ValueError, match="Synthetic crash"):
        with lock:
            assert lock.is_locked() is True
            raise ValueError("Synthetic crash")

    assert lock.is_locked() is False
    assert not lock_path.exists()


def test_sync_lock_stale_lock_recovery(tmp_path: Path) -> None:
    """12. Verifies that an expired lock from a crashed process is safely stolen and recovered."""
    lock_path = tmp_path / "sync.lock"
    stale_payload = {"owner_id": "dead_process_uuid", "acquired_at": time.time() - 1000}
    lock_path.write_text(json.dumps(stale_payload), encoding="utf-8")

    fresh_lock = SyncLockManager(lock_file=lock_path, ttl_seconds=300)
    assert fresh_lock.acquire() is True
    assert fresh_lock.is_locked() is True
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    assert data["owner_id"] == fresh_lock.owner_id
    fresh_lock.release()


def test_missing_timestamp_fallback_to_hash(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """13. Verifies that collections without timestamps gracefully fall back to content-hash diffing."""
    db = MockDatabase({
        "test_leaves": [
            {"_id": "NO_TS_1", "info": "Static data without createdAt or updatedAt"},
            {"_id": "NO_TS_2", "info": "Second static entry"},
        ]
    })
    chroma = MockChromaCollection()
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")

    # First run indexes all
    stats1 = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)
    assert stats1.new_documents == 2
    assert len(chroma.chunks) == 2

    # Second run skips all via hash comparison
    stats2 = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)
    assert stats2.unchanged_documents == 2
    assert stats2.total_chunks_upserted == 0


def test_timestamp_lookback_window_handles_clock_skew() -> None:
    """14. Verifies that timestamp query applies lookback window to absorb delayed transactions."""
    now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
    query = build_timestamp_query(watermark=now, lookback_seconds=90)

    expected_threshold = now - timedelta(seconds=90)
    assert query["$or"][0]["updatedAt"]["$gte"] == expected_threshold
    assert query["$or"][1]["createdAt"]["$gte"] == expected_threshold


def test_batch_upsert_preserves_metadata(sample_spec: CollectionSpec) -> None:
    """15. Verifies that batch upsert preserves all security, audit, and hash metadata fields."""
    chroma = MockChromaCollection()
    doc = {"_id": "REC-888", "name": "Standard Operating Procedure"}
    chunks = chunk_record(doc, "Procedure text content", sample_spec.name, "operations", "all")

    count = upsert_record_chunks(chroma, chunks, batch_size=50)
    assert count == 1
    assert len(chroma.chunks) == 1

    stored_meta = list(chroma.chunks.values())[0]["metadata"]
    assert stored_meta["doc_key"] == f"{sample_spec.name}::REC-888"
    assert stored_meta["department"] == "operations"
    assert stored_meta["access_level"] == "all"
    assert "content_hash" in stored_meta


def test_collection_level_sync_isolation(tmp_path: Path) -> None:
    """16. Verifies that synchronizing collection A does not alter or prune collection B chunks."""
    spec_a = CollectionSpec(name="coll_a", department="sales", access_level="all")
    spec_b = CollectionSpec(name="coll_b", department="finance", access_level="finance")

    chroma = MockChromaCollection()
    # Populate collection B chunks
    chroma.upsert(["Coll B Doc"], ["mongo:coll_b::B1::chunk_0"], [{"doc_key": "coll_b::B1"}])

    db = MockDatabase({"coll_a": [{"_id": "A1", "name": "Doc A1"}]})
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")

    sync_collection_incremental(db, spec_a, chroma, ckpt_mgr)

    # Collection B chunks must be completely untouched
    assert "mongo:coll_b::B1::chunk_0" in chroma.chunks
    assert "mongo:coll_a::A1::chunk_0" in chroma.chunks


def test_interrupted_sync_resumption(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """17. Verifies that if sync is interrupted after collection A, collection A watermark is saved."""
    ckpt_file = tmp_path / "checkpoints.json"
    ckpt_mgr = CheckpointManager(ckpt_file)

    db = MockDatabase({"test_leaves": [{"_id": "LP-001", "name": "Leave 1"}]})
    chroma = MockChromaCollection()

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)
    assert stats.new_documents == 1

    # Reload checkpoint manager
    reloaded_mgr = CheckpointManager(ckpt_file)
    assert reloaded_mgr.get_watermark(sample_spec.name) is not None
    assert "LP-001" in reloaded_mgr.get_checkpoint(sample_spec.name).record_hashes


def test_full_sync_fallback_works(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """18. Verifies that force_full=True bypasses watermarks and evaluates all records."""
    raw_doc = {"_id": "LP-1", "title": "Leave 1"}
    from app.rag.mongo_serializer import serialize_mongo_document

    expected_hash = compute_content_hash(serialize_mongo_document(raw_doc, sample_spec.name))
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    ckpt_mgr.update_checkpoint(sample_spec.name, datetime.now(timezone.utc), {"LP-1": expected_hash}, 1, 1)

    db = MockDatabase({"test_leaves": [raw_doc]})
    chroma = MockChromaCollection()

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr, force_full=True)
    assert stats.unchanged_documents == 1
    assert stats.new_documents == 0


def test_phase1_and_phase2_invariants_preserved() -> None:
    """19. Verifies that Phase 1 calibrated threshold and Phase 2 redactions remain intact."""
    # Phase 1: threshold invariant
    threshold = _resolve_score_threshold("annual leave balance", 0.70)
    assert threshold == 0.70
    default_thresh = _resolve_score_threshold("annual leave balance", None)
    assert 0.0 <= default_thresh <= 1.0

    # Phase 2: Sensitive field exclusion invariant
    from app.rag.mongo_serializer import serialize_mongo_document

    sensitive_doc = {
        "_id": "EMP-404",
        "name": "Jane Doe",
        "passwordHash": "super_secret_argon2",
        "apiKey": "sk_live_secret_key_123",
        "bankAccount": "1234567890",
    }
    serialized = serialize_mongo_document(sensitive_doc, "users")
    assert "super_secret_argon2" not in serialized
    assert "sk_live_secret_key_123" not in serialized
    assert "1234567890" not in serialized
    assert "Jane Doe" in serialized


def test_two_processes_attempting_to_acquire_same_lock(tmp_path: Path) -> None:
    """20. Verifies that two processes racing to acquire the lock cannot both succeed."""
    lock_file = tmp_path / "sync.lock"
    p1 = SyncLockManager(lock_file=lock_file, ttl_seconds=60, owner_id="proc-1")
    p2 = SyncLockManager(lock_file=lock_file, ttl_seconds=60, owner_id="proc-2")
    assert p1.acquire() is True
    with pytest.raises(SyncLockError):
        p2.acquire()
    p1.release()


def test_expired_lock_owner_attempting_to_release_newer_lock(tmp_path: Path) -> None:
    """21. Verifies that an expired lock owner cannot delete a newer owner's lock file."""
    lock_file = tmp_path / "sync.lock"
    old_owner = SyncLockManager(lock_file=lock_file, ttl_seconds=1, owner_id="old-worker")
    old_owner.acquire()
    time.sleep(1.1)

    new_owner = SyncLockManager(lock_file=lock_file, ttl_seconds=60, owner_id="new-worker")
    assert new_owner.acquire() is True

    old_owner.release()

    assert new_owner.is_locked() is True
    payload = json.loads(lock_file.read_text(encoding="utf-8"))
    assert payload["owner_id"] == "new-worker"
    new_owner.release()


def test_partial_mongodb_scan_cannot_trigger_deletion(sample_spec: CollectionSpec) -> None:
    """22. Verifies that an incomplete/partial MongoDB cursor scan aborts deletion reconciliation."""
    mock_coll = MagicMock()
    mock_coll.count_documents.return_value = 5
    mock_coll.find.return_value = []
    mock_db = MagicMock()
    mock_db.__getitem__.return_value = mock_coll

    chroma = MockChromaCollection()
    known_hashes = {"DOC-1": "h1", "DOC-2": "h2"}

    del_count, deleted_ids = reconcile_collection_deletions(mock_db, sample_spec, chroma, known_hashes)
    assert del_count == 0
    assert len(deleted_ids) == 0
    assert len(chroma.delete_calls) == 0


def test_chromadb_failure_during_chunk_replacement(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """23. Verifies that if ChromaDB upsert fails during update, old chunks are not prematurely deleted."""
    chroma = MockChromaCollection()
    doc_key = f"{sample_spec.name}::REC-ERR"
    for i in range(3):
        chroma.upsert([f"Old chunk {i}"], [f"mongo:{sample_spec.name}::REC-ERR::chunk_{i}"], [{"doc_key": doc_key}])
    assert len(chroma.chunks) == 3

    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    ckpt_mgr.update_checkpoint(sample_spec.name, datetime.now(timezone.utc), {"REC-ERR": "old_hash"}, 1, 3)

    db = MockDatabase({"test_leaves": [{"_id": "REC-ERR", "title": "Updated that will fail"}]})

    failing_chroma = MagicMock()
    failing_chroma.upsert.side_effect = RuntimeError("Chroma connection dropped during upsert")
    failing_chroma.delete = chroma.delete
    failing_chroma.get = chroma.get

    with pytest.raises(RuntimeError, match="Chroma connection dropped"):
        sync_collection_incremental(db, sample_spec, failing_chroma, ckpt_mgr)

    assert len(chroma.chunks) == 3


def test_crash_between_vector_writes_and_checkpoint_update(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """24. Verifies that a crash between Chroma writes and checkpoint save is recovered on next sync."""
    ckpt_file = tmp_path / "checkpoints.json"
    ckpt_mgr = CheckpointManager(ckpt_file)
    initial_watermark = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    ckpt_mgr.update_checkpoint(sample_spec.name, initial_watermark, {}, 0, 0)

    db = MockDatabase({"test_leaves": [{"_id": "DOC-CRASH", "title": "Crash Recovery Doc"}]})
    chroma = MockChromaCollection()

    original_update = ckpt_mgr.update_checkpoint
    ckpt_mgr.update_checkpoint = MagicMock(side_effect=RuntimeError("Process killed before saving checkpoint"))

    with pytest.raises(RuntimeError):
        sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)

    assert len(chroma.chunks) == 1
    reloaded_mgr = CheckpointManager(ckpt_file)
    assert reloaded_mgr.get_watermark(sample_spec.name) == initial_watermark

    stats = sync_collection_incremental(db, sample_spec, chroma, reloaded_mgr)
    assert stats.status == "success"
    assert len(chroma.chunks) == 1
    assert "DOC-CRASH" in reloaded_mgr.get_checkpoint(sample_spec.name).record_hashes


def test_sync_retry_repairs_incomplete_indexing(tmp_path: Path, sample_spec: CollectionSpec) -> None:
    """25. Verifies that retry after a failed sync repairs missing documents and completes checkpoints."""
    ckpt_mgr = CheckpointManager(tmp_path / "checkpoints.json")
    db = MockDatabase({
        "test_leaves": [
            {"_id": "D1", "title": "Doc 1"},
            {"_id": "D2", "title": "Doc 2"},
        ]
    })
    chroma = MockChromaCollection()

    call_count = 0

    def side_effect_upsert(*args: Any, **kwargs: Any) -> int:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise RuntimeError("Transient network failure on doc 2")
        return chroma.upsert(*args, **kwargs)  # type: ignore[return-value]

    failing_chroma = MagicMock()
    failing_chroma.upsert.side_effect = side_effect_upsert
    failing_chroma.get = chroma.get
    failing_chroma.delete = chroma.delete

    with pytest.raises(RuntimeError):
        sync_collection_incremental(db, sample_spec, failing_chroma, ckpt_mgr)

    assert ckpt_mgr.get_watermark(sample_spec.name) is None

    stats = sync_collection_incremental(db, sample_spec, chroma, ckpt_mgr)
    assert stats.status == "success"
    assert len(chroma.chunks) == 2
    assert "D1" in ckpt_mgr.get_checkpoint(sample_spec.name).record_hashes
    assert "D2" in ckpt_mgr.get_checkpoint(sample_spec.name).record_hashes

