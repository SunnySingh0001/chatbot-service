# RAG Architecture & Production Configuration Directives

This document specifies the recommended production configuration settings, architectural decisions, and operational flags for the chatbot retrieval-augmented generation service.

---

## 1. Recommended Production Settings

| Setting Key | Environment Variable | Recommended Value | Rationale |
| :--- | :--- | :--- | :--- |
| **Chunking Mode** | `CHUNKING_MODE` | `hierarchical` | Preserves hierarchical section breadcrumbs (`# > ## > ###`) and table metadata across chunk boundaries. |
| **Table Splitting** | `TABLE_MAX_ROWS_PER_CHUNK` | `4` | Splits large markdown tables into 4-row slices with repeated header rows, preventing table truncations. |
| **Hybrid Search** | `HYBRID_SEARCH` | `true` | Merges dense vector embeddings with sparse BM25 scoring via Reciprocal Rank Fusion (RRF), capturing both semantic intent and exact alphanumeric codes (`P1`, `12 characters`). |
| **Hybrid Candidates** | `HYBRID_CANDIDATES` | `8` | Gathers top 8 dense and top 8 sparse candidates before RRF rank fusion. |
| **Retrieval Top-K** | `RAG_TOP_K` | `5` | Delivers top 5 ranked chunks to the answer generation prompt. |
| **Similarity Threshold** | `RAG_SCORE_THRESHOLD` | `0.80` | Optimal operating threshold balancing positive acceptance (97.3%) and negative filtering. |
| **Query Rewriter** | `QUERY_REWRITE` | `true` | Synthesizes standalone search queries from conversational multi-turn history. |
| **Rewrite Backend** | `REWRITE_BACKEND` | `llm_then_heuristic` | Calls cloud LLM with fast 3s timeout; on rate-limit, empty output, or timeout, immediately falls back to local 0.1 ms heuristic. |
| **Rewrite Model** | `REWRITE_MODEL` | `qwen/qwen3.8-27b` | Fast non-reasoning instruction model with warm p50 latency under 380 ms. |
| **Rewrite Mode** | `REWRITE_MODE` | `replace` | Replaces ambiguous pronouns with standalone queries; automatically fuses original query in `both` mode if a code-like stub is detected. |
| **Heuristic Variant** | `HEURISTIC_VARIANT` | `f` | Reference marker check or $\le 2$ content terms; selectively extracts assistant domain nouns when the user's latest message has no domain context (e.g. conversational acknowledgments), boosting multi-turn recall to 100% while preventing topic dilution. |
| **Rewrite Timeout** | `REWRITE_TIMEOUT_S` | `3.0` | Bounds rewriter wait time well below user perception thresholds. |
| **Reranker** | `RERANKER` | `false` | Disabled by default for sub-50 ms retrieval latency; enable with `RERANKER_MODEL=b` for high-precision workflows. |
| **Grader** | `GRADER` | `false` | Post-retrieval confidence gate; keep disabled unless running reranker in strict automated QA pipelines. |

---

## 2. Feature Flags & Operational Analysis

### A. Query Rewriting (`QUERY_REWRITE=true`)
- **Problem**: Follow-up inquiries like *"Can I carry them forward?"* or *"How many days notice?"* fail dense retrieval without conversational context.
- **Solution**: Follow-up questions are detected using reference markers (`them`, `they`, `iska`, `uska`) or content term sparsity ($\le 2$ terms).
- **Backend Hierarchy (`llm_then_heuristic`)**:
  1. Calls fast cloud LLM (`qwen/qwen3.8-27b`) with escaped XML data tags.
  2. If the LLM times out ($>3.0$ s), errors, or returns invalid text, execution seamlessly falls back to the deterministic local heuristic ($\le 0.1$ ms).
  3. Never delays or crashes user requests.

### B. Keyword-Stub Guard
- **Problem**: Short search queries containing codes or acronyms (e.g. `VPN`, `SLA P1`, `Section 2.3`) were previously corrupted by heuristic enrichment appending unrelated prior history words (e.g. `VPN days paid leave carry forward`).
- **Solution**:
  - Any query with $\le 2$ terms containing uppercase acronyms (`[A-Z]{2,}`), digits, decimal section numbers (`2.3`), or alphanumeric codes (`P1`, `L1`) is classified as a code stub.
  - Heuristic enrichment is **strictly blocked** on code stubs.
  - LLM rewrites on code stubs are forced into multi-query fusion (`REWRITE_MODE=both`), preserving the raw search tokens in the final RRF candidate pool.

### C. Similarity Gating Choices
1. **Choice 1 (Enriched Query Gate)**: Evaluates vector similarity on the rewritten query. Highest positive recall (97.3% at $\tau=0.80$), but can elevate scores of off-topic follow-ups.
2. **Choice 2 (Original Query Gate)**: Evaluates vector similarity on the raw user question. Rejects 80.0% of off-topic negatives at $\tau=0.82$, but penalizes short follow-up questions.
3. **Choice 3 (Enriched + Lexical Guard)**: Requires best chunk to have lexical overlap with original query. Prevents drift but drops legitimate pronoun-based follow-ups.
- **Recommended Balance**: Choice 1 with `RAG_SCORE_THRESHOLD=0.80` retains 109/112 positives (+8 over Choice 2).

### D. Model Execution Framework
- Dense embeddings (`multilingual-e5-small`) and cross-encoder rerankers (`gte-multilingual-reranker-base` / `mmarco-mMiniLMv2`) execute locally via **ONNX Runtime** on CPU (`CPUExecutionProvider`).
- Fast unit test runs exclude real ONNX model loading using `-m "not slow"`, running in ~67 seconds.
