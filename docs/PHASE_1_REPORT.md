# Phase 1: RAG Embedding & Retrieval Engine Technical Report

## 1. Executive Summary
Phase 1 addressed critical embedding prefix asymmetry and similarity threshold miscalibration in the retrieval engine while preserving the previously fixed hybrid retrieval gating functionality.

All 70 automated tests across embeddings, retrieval, hybrid fusion, access control, and architectural security invariants passed with zero warnings.

---

## 2. Issues Investigated & Root Causes Confirmed

### Issue 1: Multilingual-E5 Prefix Asymmetry
- **Previous Behavior**: ChromaDB's `collection.query(query_texts=[query])` defaulted to calling `MultilingualE5EmbeddingFunction.__call__()`. Because `__call__` applied the `passage: ` prefix, user search queries were embedded as passages instead of queries.
- **Root Cause**: `intfloat/multilingual-e5-small` is an asymmetric embedding model requiring `query: ` prefix for search queries and `passage: ` prefix for stored document chunks. ChromaDB's standard `EmbeddingFunction` interface does not differentiate between document indexing and query embedding when `query_texts` is passed.
- **Impact**: Queries were matched in passage-passage space, lowering semantic similarity scores by 15%–30% and causing relevant company documents to fall below threshold.

### Issue 2: Similarity Threshold Miscalibration
- **Previous Behavior**: A single global threshold (`RAG_SCORE_THRESHOLD = 0.80`) was applied across all languages and queries.
- **Root Cause**: While relevant English queries in E5 score between 0.82 and 0.89, valid Hinglish queries (e.g. *"chutti kab milegi"*) and Hindi queries legitimately score between 0.78 and 0.84. Completely off-topic queries score $\le 0.775$. Hardcoding threshold to 0.80 caused false-negative rejections of valid Hinglish/Hindi queries.
- **Impact**: Chatbot replied with *"I don't have enough information"* to valid employee questions.

### Issue 3: Hybrid Retrieval Gating Preservation
- **Preserved Behavior**: BM25 keyword search runs independently of vector similarity score. Exact identifiers, policy codes, and employee tokens (e.g. `VPN`, `Section 2.3`, invoice IDs) survive retrieval even if vector similarity is low or 0.0.

---

## 3. Changes Implemented

### A. `app/rag/embedder.py`
1. **Added `_strip_e5_prefix()`**: Strips existing `passage: ` or `query: ` prefixes from input strings to guarantee no double-prefixing occurs.
2. **Implemented `name()`**: Returns `"multilingual-e5-small"` to conform to modern ChromaDB `EmbeddingFunction` protocols and eliminate runtime deprecation warnings.
3. **Implemented `embed_documents()`**: Explicit document chunk embedding interface applying `passage: ` prefix.
4. **Enhanced `embed_query()`**: Accepts both `str` and `list[str]` inputs, applying `query: ` prefix.
5. **Aligned `__call__()`**: Delegates directly to `embed_documents()` to ensure ingestion pipelines (`ingest.py` and `mongo_ingest.py`) produce uniform embeddings.

### B. `app/rag/retriever.py`
1. **Updated `_query_vector_store()`**:
   - Explicitly computes query embedding via `embed_query([query])` with `query: ` prefix.
   - Passes `query_embeddings=query_embeddings` directly to `collection.query()`, eliminating Chroma's fallback to `passage: `.
2. **Added `_resolve_score_threshold()`**:
   - Dynamically resolves language-aware thresholds based on `detect_language(query)`.
   - Supports explicit caller overrides (`score_threshold`), environment variable overrides (`RAG_SCORE_THRESHOLD_HINGLISH`, `RAG_SCORE_THRESHOLD_HINDI`), and a calibrated fallback (`min(base, 0.75)` for Hinglish/Hindi).
3. **Integrated `_resolve_score_threshold()` into `retrieve()`**: Replaced hardcoded threshold assignment with dynamic language-aware resolution.

### C. `app/config.py` & `.env.example`
1. Added `rag_score_threshold_hinglish: float | None` (via `RAG_SCORE_THRESHOLD_HINGLISH`).
2. Added `rag_score_threshold_hindi: float | None` (via `RAG_SCORE_THRESHOLD_HINDI`).
3. Calibrated default `rag_score_threshold` to `0.78` in `app/config.py` and documented in `.env.example`.

---

## 4. Re-Indexing Requirements
- **Is re-indexing required immediately?** **No**.
- **Explanation**: Ingestion pipelines (`app/rag/ingest.py` and `app/rag/mongo_ingest.py`) have always called `collection.upsert(documents=...)`, which invoked `__call__` (`prefix="passage: "`). Therefore, all existing chunks in ChromaDB already possess correct `passage: ` embeddings.
- The defect was solely on the query path in `_query_vector_store()`, which is now fixed to embed queries with `query: `.
- Any future re-indexing via `python -m app.rag.ingest` or `python -m app.rag.mongo_ingest` remains 100% compatible.

---

## 5. Automated Tests Executed & Verification Results

### Test Suite: `tests/test_phase1_embeddings_retrieval.py` (12 tests)
- `test_document_receives_passage_prefix`: **PASSED**
- `test_query_receives_query_prefix`: **PASSED**
- `test_no_double_prefixing_on_documents_or_queries`: **PASSED**
- `test_embedding_dimensions_and_l2_normalization`: **PASSED** (384-d, norm=1.0)
- `test_ingestion_path_consistency`: **PASSED** (`__call__` == `embed_documents`)
- `test_distance_to_similarity_score_conversion`: **PASSED** ($score = 1.0 - dist$)
- `test_query_vector_store_uses_query_prefix`: **PASSED**
- `test_language_aware_threshold_resolution`: **PASSED**
- `test_english_hindi_hinglish_retrieval`: **PASSED**
- `test_off_topic_query_rejected`: **PASSED**
- `test_exact_invoice_and_order_ids_via_bm25_hybrid`: **PASSED**
- `test_hybrid_gating_preserves_task1_fix`: **PASSED**

### Full Regression Suite:
- `tests/test_hybrid.py`: **12 PASSED**
- `tests/test_retrieval.py`: **29 PASSED, 2 SKIPPED** (Golden set questions requiring live LLM skipped as expected)
- `tests/test_access.py`: **6 PASSED**
- `tests/test_invariants.py`: **11 PASSED**
- **Total**: **70 passed, 2 skipped, 0 failures, 0 warnings** in 11.57s.

---

## 6. Remaining Limitations (Out of Scope for Phase 1)
1. **MongoDB Ingestion Formatting**: Nested arrays and structured tables in raw MongoDB documents still require entity-preserving chunking (Phase 2 scope).
2. **Server-Side Session Store**: Chat conversation history is still passed from client payload (Phase 4 scope).
3. **Cross-Encoder Reranker**: ONNX reranker remains disabled by default pending dedicated latency and memory profiling.
