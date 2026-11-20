# Phase 3 — Incremental MongoDB -> ChromaDB Sync & Race Condition Fixes
## Implementation Plan & Technical Architecture

---

## 1. Executive Summary & Current Pipeline Inspection

The existing chatbot implementation synchronizes MongoDB Atlas operational records into ChromaDB using a full-collection purge-and-reload mechanism (`ingest_collection_structured` in `app/rag/mongo_ingest.py`). 

### Current Pipeline Mechanics:
1. **Full-Scan Fetch**: `db[spec.name].find({})` pulls all documents from MongoDB in an unbounded query.
2. **Whole-Collection Purge**: `chroma_col.delete(where={"source": f"mongo:{spec.name}"})` completely purges all chunks for that collection before indexing new documents.
3. **Unconditional Re-embedding**: Every single record is serialized and re-chunked regardless of whether it was modified, incurring high compute costs and latency.
4. **No State / Checkpoints**: There is no tracking of `updatedAt` timestamps or document revision hashes.
5. **No Concurrency Guard**: Multiple concurrent requests to `/sync/mongo` can run simultaneously, racing each other, issuing duplicate delete-and-upsert cycles, and causing temporary index emptiness or data corruption during search queries.
6. **Orphaned Chunk Risk**: If a document shrinks or is deleted between manual syncs, chunks can be orphaned or temporarily missing during collection-wide purges.

---

## 2. Root Cause Analysis of Sync Defects

| Defect | Root Cause | Impact |
|---|---|---|
| **Zero-Downtime Failure** | `delete(where={"source": ...})` wipes collection chunks prior to upserting. | Search queries executing during sync return 0 results (blackout window). |
| **High Compute / API Cost** | All records are re-embedded on every sync call. | Re-calculates 384-d embeddings for thousands of identical documents. |
| **Race Conditions** | No mutex or distributed lock on `/sync/mongo` execution. | Two simultaneous syncs interleave chunk writes and deletions. |
| **Orphaned Chunks** | Without record-scoped chunk tracking, updated documents that shrink in size retain stale high-index chunks. | Retrieval retrieves obsolete text and outdated business data. |
| **No Deletion Sync** | Deletions are only caught by full-wipe; no incremental deletion reconciliation exists. | Stale documents remain indexed indefinitely unless a dangerous full wipe is run. |
| **No Crash Recovery** | Failures mid-sync leave partial data without resuming where left off. | Sync must be restarted from zero every time. |

---

## 3. Proposed Incremental Synchronization Architecture

```
                       ┌─────────────────────────┐
                       │   Incremental Trigger   │
                       │ (/sync/mongo/incremental)│
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │     SyncLockManager     │
                       │  (TTL lock: sync.lock)  │
                       └────────────┬────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │       CheckpointManager       │
                    │ (data/sync_checkpoints.json)  │
                    └───────────────┬───────────────┘
                                    │
              ┌─────────────────────┴─────────────────────┐
              ▼                                           ▼
   ┌───────────────────────┐                  ┌───────────────────────┐
   │ Timestamp Collections │                  │ Non-Timestamp Colls   │
   │   (updatedAt/createdAt)│                 │ (Full diff with hash) │
   └──────────┬────────────┘                  └───────────┬───────────┘
              │                                           │
              └─────────────────────┬─────────────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │ 1. Change Detection     │
                       │ - Lookback window (60s) │
                       │ - Content Hash check    │
                       └────────────┬────────────┘
                                    │
         ┌──────────────────────────┼──────────────────────────┐
         ▼                          ▼                          ▼
   [ NEW DOC ]                [ MODIFIED DOC ]           [ UNCHANGED ]
   - Serialize                - Purge old chunks via      - Skip embedding
   - Chunk & Hash               doc_key where-clause      - Zero compute
   - Upsert ChromaDB          - Serialize & Chunk
                              - Upsert ChromaDB
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │ 2. Deletion Reconcile   │
                       │ - Query Mongo active IDs│
                       │ - Query Chroma index IDs│
                       │ - Delete stale doc_keys │
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │ 3. Checkpoint Advance   │
                       │ - Save high-watermark   │
                       │ - Update content hashes │
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │   Release Sync Lock     │
                       │  Invalidate BM25 Cache  │
                       └─────────────────────────┘
```

### Key Technical Pillars:

### 3.1 Stable Chunk Identity and the `doc_key` Pattern
In ChromaDB v0.4+, compound `$and` queries in `delete(where=...)` can fail with `ValueError`.
To ensure atomic chunk cleanup per record without wiping entire collections:
- Every chunk metadata includes:
  `doc_key = f"{collection_name}::{record_id}"`
- When a document is modified or deleted:
  `chroma_col.delete(where={"doc_key": f"{collection_name}::{record_id}"})`
- This immediately and safely removes all chunks belonging to that specific document (e.g. `chunk_0`, `chunk_1`, `chunk_2`), eliminating chunk shrinkage orphans and stale data with zero impact on other documents in the collection.

### 3.2 Change Detection: High-Watermark + Content Hashing
1. **Timestamp Watermark**:
   - Checkpoint stores `last_sync_timestamp` (ISO UTC).
   - In incremental mode, query MongoDB with lookback window (default: 60 seconds) to account for clock skew and long transactions:
     `{"updatedAt": {"$gte": last_sync_time - 60s}}`
2. **Content Hash Check**:
   - Compute SHA-256 hash of serialized record (`compute_content_hash`).
   - If `doc_hash == checkpoint.record_hashes.get(record_id)`:
     Mark as **UNCHANGED**; skip re-serialization and skip embedding.
3. **Fallback for Collections Without Timestamps**:
   - Fetch active documents; compare content hash directly against the checkpoint hash map. Only re-index if content hash differs.

### 3.3 Obsolete Chunk Pruning (Document Shrinkage & Updates)
- If a document originally had 5 chunks and updates to produce only 2 chunks:
  - Step 1: `chroma_col.delete(where={"doc_key": doc_key})` purges all 5 previous chunks.
  - Step 2: `chroma_col.upsert(...)` writes the 2 new chunks (`chunk_0`, `chunk_1`).
  - No stale `chunk_2`, `chunk_3`, or `chunk_4` can ever remain in ChromaDB.

### 3.4 Deletion Reconciliation
- For each collection, active MongoDB document IDs are compared against indexed IDs in ChromaDB (or the checkpoint index).
- Any `record_id` in the checkpoint/index that no longer exists in MongoDB is identified as **DELETED**.
- All chunks for deleted records are pruned via `chroma_col.delete(where={"doc_key": doc_key})`.
- The record is removed from the checkpoint's hash map.

### 3.5 Persistent Checkpoint State Management (`app/rag/sync_state.py`)
- Checkpoints stored in `data/sync_checkpoints.json`.
- Structure per collection:
  ```json
  {
    "collection_name": "leave_policy",
    "last_sync_timestamp": "2026-11-20T14:30:00Z",
    "last_sync_status": "success",
    "total_records": 12,
    "total_chunks": 24,
    "record_hashes": {
      "LP-001": "a1b2c3d4e5f6...",
      "LP-002": "f6e5d4c3b2a1..."
    }
  }
  ```
- **Atomicity**: The checkpoint file is written atomically (write to temp file then rename) only *after* successful ChromaDB writes.

### 3.6 Concurrency & Race Condition Prevention (`SyncLockManager`)
- Lock file: `data/sync.lock` containing:
  - `owner_id`: unique UUID
  - `acquired_at`: ISO timestamp
  - `ttl_seconds`: lock expiration (default: 600s / 10 minutes)
- **Lock Acquisition**:
  - If lock file exists and is active ($\text{age} < \text{TTL}$): raise `SyncLockError("Sync already in progress")`.
  - If lock file exists but is expired ($\text{age} \ge \text{TTL}$): log warning and recover (stolen lock for crash recovery).
- **Lock Release**: Cleanly removed upon completion or via `try...finally` context manager.

---

## 4. Affected Files & Proposed Changes

1. **`app/rag/record_chunker.py`**:
   - Add `doc_key: f"{collection_name}::{record_id}"` into `_build_base_metadata()` so all chunks produced carry the unified record key.
2. **`app/rag/sync_state.py`** (NEW):
   - `SyncLockManager`: Context-managed file-based locking with TTL and crash recovery.
   - `CheckpointManager`: Checkpoint persistence, serialization, watermark retrieval, and hash tracking.
3. **`app/rag/incremental_sync.py`** (NEW):
   - `sync_collection_incremental`: Incremental change detection (new, modified, unchanged, deleted), obsolete chunk pruning, and watermark advancement.
   - `sync_mongodb_incremental`: Full multi-collection incremental orchestrator with lock protection and telemetry stats.
   - `reconcile_collection_deletions`: Compares MongoDB live IDs against ChromaDB / checkpoint IDs and deletes pruned records.
4. **`app/rag/mongo_ingest.py`**:
   - Integrate `doc_key` pattern.
   - Refactor `ingest_collection_structured` to avoid full collection wipe when called directly.
   - Expose both incremental and full sync entry points.
5. **`app/api/sync.py`**:
   - Expose `POST /sync/mongo/incremental` (default, fast, non-blocking) and `POST /sync/mongo/full` (explicit full re-index).
   - Return structured audit telemetry (`new`, `modified`, `unchanged`, `deleted`, `elapsed_ms`).
6. **`tests/test_phase3_incremental_sync.py`** (NEW):
   - 19 targeted test cases validating all incremental sync scenarios, chunk pruning, locks, and recovery.

---

## 5. Verification & Test Plan

1. **New Documents**: Insert into mock Mongo -> incremental sync indexes chunk and updates checkpoint.
2. **Modified Documents**: Modify record -> old chunks deleted via `doc_key`, new chunks upserted.
3. **Unchanged Documents**: Unchanged record -> skipped, embedding function not called.
4. **Deleted Documents**: Delete in Mongo -> incremental sync deletes from ChromaDB and removes hash.
5. **Document Shrinkage**: Record drops from 5 chunks to 2 -> chunks 2, 3, 4 pruned; chunks 0, 1 updated.
6. **Lock Contention**: Second concurrent sync raises `SyncLockError`; first finishes cleanly.
7. **Crash / Stale Lock Recovery**: Stale lock expired after TTL is safely recovered without human intervention.
8. **Regression**: Run all 84 existing Phase 1 & 2 tests + 19 Phase 3 tests (103 total tests).
