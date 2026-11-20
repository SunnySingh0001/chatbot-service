# Phase 2 Implementation Plan: MongoDB Ingestion, Structured Serialization & Data Integrity

## 1. Objectives & Scope
Phase 2 resolves the data integrity and ingestion bottlenecks identified during the technical audit:
1. **Lossy MongoDB Document Serialization**: Replace crude string concatenation and partial field extraction with recursive, relationship-preserving structured serialization.
2. **Chunk Boundary Shattering**: Replace blind word-window slicing with record-aware chunking that keeps atomic business records intact and repeats parent identifiers on large records.
3. **Incomplete Collection Ingestion & Catalog Discovery**: Audit all 103 collections discovered in MongoDB Atlas `fakhri2_0`, establish a centralized collection catalog with strict access boundaries and sensitive-field redaction.
4. **Data Validation & Resilient Ingestion**: Implement safe BSON type handling (ObjectIds, UTC timestamps, decimals, nulls) and fail-soft record processing.
5. **Phase 1 Preservation**: 100% preservation of `multilingual-e5-small` embeddings (384-d, `passage: ` / `query: ` prefixes), dynamic thresholding, BM25 hybrid search, and RBAC security invariants.

---

## 2. Confirmed Root Causes

1. **Lossy String Formatting & Arbitrary Hard Limits**:
   - In `app/rag/mongo_ingest.py`, formatters like `_format_invoices()` clamped queries to `limit(30)`, only extracted `items[0]`, and dropped timestamps, taxes, and client IDs.
   - Nested arrays and referenced ObjectIds were either cast via `str(doc)` or discarded.
2. **Chunk Boundary Shattering**:
   - `_upsert_category()` concatenated multiple records into monolithic Markdown documents and passed them to `create_chunks()`.
   - Fixed character/word windows split invoices, candidate resumes, and orders across arbitrary boundaries, separating child items from parent totals and names.
3. **Missing Collection Catalog & Ungoverned Collections**:
   - While MongoDB Atlas contains 103 collections, only 37 were hardcoded into `categories`.
   - There was no automated collection discovery, missing-collection auditing, or centralized schema governance specifying sensitive field exclusions and role access levels.
4. **Lack of Enriched Record-Level Metadata**:
   - ChromaDB chunks only stored `source`, `department`, `access_level`, and `chunk_index`.
   - Essential business traceability fields (`record_id`, `collection_name`, `created_at`, `updated_at`, `content_hash`) were absent.

---

## 3. Architecture & Affected Files

```
app/rag/
├── mongo_serializer.py    [NEW]  - Recursive BSON serialization, relationship preservation, sensitive redaction
├── record_chunker.py      [NEW]  - Record-aware atomic chunker with header propagation & hash tracking
├── mongo_catalog.py       [NEW]  - Centralized catalog of 103 collections (approved, sensitive, unreviewed)
├── mongo_ingest.py        [MOD]  - Ingestion pipeline integrating catalog, serializer, and chunker
└── (embedder.py, retriever.py, ingest.py) [UNTOUCHED] - Phase 1 functionality strictly preserved
```

---

## 4. Detailed Component Design

### A. Structured Document Serialization (`app/rag/mongo_serializer.py`)
- **Field Normalization**:
  - `ObjectId` $\rightarrow$ hex string (`str(oid)`).
  - `datetime` $\rightarrow$ ISO 8601 UTC string (`dt.isoformat()`).
  - Numbers, booleans, and nested structures preserved cleanly in YAML-like indented markdown.
  - `None` values represented as `null` or omitted based on relevance.
- **Sensitive Field Redaction**:
  - Automatically redacts fields matching sensitive patterns: `password`, `passwordHash`, `hash`, `salt`, `token`, `secret`, `apiKey`, `otp`, `accessToken`, `refreshToken`, `ssn`, `bankAccountNumber`.

### B. Record-Aware Chunking (`app/rag/record_chunker.py`)
- **Atomic Record Preservation**:
  - If a formatted record is $\le$ target word budget (~180 words), it remains a single chunk.
- **Header Propagation for Large Records**:
  - If a large document exceeds the limit, it is split by logical sections/blocks while repeating the primary record header (`[Record: <ID> | <Title>]`) in child chunks.
- **Enriched Metadata**:
  - Every chunk receives: `source`, `record_id`, `collection_name`, `department`, `access_level`, `chunk_index`, `created_at`, `updated_at`, `content_hash`.

### C. Collection Discovery & Catalog Governance (`app/rag/mongo_catalog.py`)
- Scans `db.list_collection_names()`.
- Compares against `COLLECTION_CATALOG`:
  1. **Approved Collections**: Ingested with defined department, access level, and field mappings.
  2. **Sensitive Excluded Collections**: Formally blocked from vector indexing (e.g. `otps`, `salaryslips`, `banktransactions`, `ssoauthorizationcodes`, `ssosigningkeys`).
  3. **Operational / System Excluded**: Blocked from indexing (e.g. `cronstates`, `ratelimits`, `migration_snapshots`, `gridfs`).
  4. **Unreviewed / Unknown Collections**: Logged for administrative review; never ingested silently.

---

## 5. Test Strategy
1. **Unit Tests (`tests/test_phase2_mongo_integrity.py`)**:
   - Simple document serialization.
   - Deeply nested objects and arrays of dicts.
   - BSON type conversion (`ObjectId`, `datetime`, Decimals).
   - Sensitive field exclusion verification.
   - Record-aware chunking without shattering atomic records.
   - Header propagation on large records.
   - Metadata generation and SHA-256 content hashing.
   - Resilient error handling on malformed documents.
   - Collection catalog discovery and validation.
2. **Phase 1 Regression Testing**:
   - Re-run all 70 Phase 1 tests (`test_phase1_embeddings_retrieval.py`, `test_hybrid.py`, `test_retrieval.py`, `test_access.py`, `test_invariants.py`).
   - Zero regressions tolerated.
