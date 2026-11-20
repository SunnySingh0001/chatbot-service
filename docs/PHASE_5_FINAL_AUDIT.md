# PHASE 5 — FINAL CODE-LEVEL SECURITY & RELIABILITY AUDIT REPORT

**Auditor Role**: Senior Backend Engineer, Application Security Engineer & Production Code Reviewer  
**Audit Scope**: Server-Side Conversation Sessions, MongoDB Persistence, JWT Session Ownership, IDOR Protection, Token-Aware History Budgeting, Hinglish Memory, History Summary Security, and SSE Integration  
**Date**: November 20, 2026  
**Final Verdict**: **CONDITIONAL PASS**

---

## 1. Executive Summary

Phase 5 introduces server-managed conversational sessions and memory to replace client-supplied conversation history. This audit verified the implementation against critical application security and distributed systems criteria:

1. **IDOR & Session Hijacking**: Every access requires authenticated JWT context; session lookups are strictly bound to both `session_id` and `user_id`. Client-supplied history in request bodies is discarded.
2. **Persistence Integrity**: Persistent storage in MongoDB `chat_sessions` is enforced. In production, database outages fail closed (HTTP 503) rather than silently falling back to volatile in-memory storage.
3. **Session Lifecycle & Expiry**: Server-side TTL enforcement prevents stale session reuse; expired sessions are never resurrected under old IDs.
4. **Token Budget & Multilingual Safety**: Script-aware token budgeting accounts for the higher token density of Devanagari Hindi (~1.2 chars/token) and Hinglish (~3.2 chars/token), preventing context window clipping.
5. **Prompt Injection & History Defense**: Conversation history summaries and past turns are treated strictly as untrusted data, enclosed in `<prior_context>` XML delimiters with full character escaping.
6. **Phase 4 Invariant Preservation**: Assistant messages are persisted **strictly after** passing Phase 4 grounded answer and citation validation (`is_valid=True`).

All 32 tests in the Phase 5 test suite and all 358 automated tests across the repository pass with zero regressions.

---

## 2. Files Inspected

| File Path | Role in Phase 5 |
|---|---|
| `app/rag/session_manager.py` | Core session persistence, IDOR verification, MongoDB operations, session lifecycle, and message linking. |
| `app/api/chat.py` | Chat endpoint, JWT token resolution, session ID resolution, streaming SSE generator, and terminal event metadata. |
| `app/rag/history_budget.py` | Script-aware token estimation, message budget enforcement, recent message preservation, and entity-preserving summarization. |
| `app/rag/condense.py` | Multilingual query condensation, Hinglish/Hindi/English anaphoric pronoun resolution, and ambiguous query handling. |
| `app/rag/prompt.py` | Prompt construction, escaping untrusted history and summaries inside `<prior_context>` blocks. |
| `app/orchestration/nodes.py` | LangGraph nodes (`generate_node`, `no_context_node`) enforcing validation before message persistence. |
| `app/orchestration/state.py` | State definitions including `session_id`, `user_message_id`, and `history_summary`. |
| `app/config.py` | Configuration settings for `session_ttl_hours`, `history_token_budget`, and environment modes. |
| `tests/test_phase5_conversation_memory.py` | Dedicated test suite covering session security, IDOR, token budgets, condensation, and error scenarios. |

---

## 3. Defects Identified & Confirmed Fixes

During the code audit, 6 defects were identified, reproduced, and remediated:

### F-01: Silent In-Memory Fallback in Production (Severity: Critical)
- **Vulnerability**: `app/rag/session_manager.py` caught MongoDB connection and query exceptions and silently stored sessions in `_MEMORY_SESSIONS` dictionary, even when `APP_ENV=production`.
- **Impact**: In a multi-worker production cluster, database failures caused split-brain session state across workers and silent message loss on worker restarts.
- **Fix**: Added `_enforce_production_persistence_check()`. When `settings.app_env == "production"`, any failure to acquire MongoDB or write messages raises an immediate `HTTPException(503, "Database persistence unavailable: session operation cannot proceed in production")`. In-memory storage is strictly gated to `development` and `test` environments.

### F-02: Absence of Server-Side Session TTL & Session Resurrect Bug (Severity: High)
- **Vulnerability**: `get_session()` did not verify session age against a TTL policy. Furthermore, when an expired session was requested, `create_session(session_id, user_id, role)` was invoked, resurrecting the expired session ID as an empty conversation.
- **Impact**: Stale sessions could be kept alive indefinitely. Expired session IDs could be re-registered, potentially causing session confusion.
- **Fix**: Added `session_ttl_hours` (default 24h) to `app/config.py` and `is_session_expired()`. Requesting an expired session rejects access; `get_or_create_session` assigns a brand new UUIDv4 rather than reusing or resurrecting the old session ID.

### F-03: Devanagari Hindi Token Underestimation (Severity: Medium)
- **Vulnerability**: `estimate_tokens()` used a naive `len(text) // 4` heuristic.
- **Impact**: Devanagari Hindi text average ~1.2 characters per token in standard BPE tokenizers. A 44-character Hindi sentence was estimated at 11 tokens instead of ~32 tokens (a 65% under-count), risking context window overflow on long Hindi multi-turn sessions.
- **Fix**: Upgraded `estimate_tokens()` in `app/rag/history_budget.py` to be script-aware: Devanagari codepoints (`\u0900-\u097f`) are budgeted at ~1.2 chars/token, and Latin/Hinglish characters at ~3.2 chars/token (incorporating a 25% safety margin).

### F-04: Malformed Session ID & Path Traversal Vulnerability (Severity: Medium)
- **Vulnerability**: Client-supplied `session_id` strings were passed into database operations without length limits or character whitelisting.
- **Impact**: Path-traversal sequences (`../../etc/passwd`) or oversized strings (10,000+ chars) could enter database queries or log files.
- **Fix**: Implemented `validate_session_id()`, requiring strings to match `^[a-zA-Z0-9_-]+$` and capping length at 128 characters. Malformed IDs immediately return HTTP 400 Bad Request.

### F-05: Missing User-Assistant Message Turn Correlation (Severity: Low)
- **Vulnerability**: Assistant responses were appended to the session array without linking to the specific user turn that triggered them.
- **Impact**: In scenarios with rapid or concurrent user requests within the same session, assistant responses could not be unambiguously paired with their prompt.
- **Fix**: `save_user_message()` generates and returns a unique `message_id`. This ID is tracked through `ChatState.user_message_id` and stored in the assistant message metadata as `metadata["reply_to"] = user_message_id`.

### F-06: Potential History Summary XML Delimiter Breakout (Severity: Medium)
- **Vulnerability**: Summarized older dialogue history was not rigorously escaped before being passed into the prompt generation template.
- **Impact**: Malicious user turns containing `</prior_context>` could attempt delimiter breakout to inject unauthorized instructions into the prompt.
- **Fix**: Updated `build_messages()` in `app/rag/prompt.py` to sanitize `history_summary`, escape XML delimiters, and enclose the summary in `<prior_context>` with explicit `<!-- Untrusted dialogue summary -->` comments.

---

## 4. Test Execution & Verification

### A. Phase 5 Dedicated Test Suite
Command executed:
```bash
.venv\Scripts\python.exe -m pytest tests/test_phase5_conversation_memory.py -v
```
**Result**: **32 passed in 12.27s** (0 failed, 0 skipped)

#### Breakdown: Unit vs. Integration Tests

| Category | Count | Tests Included |
|---|---|---|
| **Unit Tests** | **28** | `test_new_session_creation`, `test_existing_session_retrieval`, `test_session_ownership_validation`, `test_user_a_cannot_access_user_b_session`, `test_client_supplied_history_ignored`, `test_user_message_persistence`, `test_assistant_message_persistence`, `test_failed_generation_no_persistence`, `test_retry_no_duplicate_assistant_messages`, `test_hinglish_pronoun_resolution`, `test_english_multi_turn_entity_preservation`, `test_hindi_multi_turn_context_preservation`, `test_standalone_question_not_condensed`, `test_ambiguous_reference_handling`, `test_token_budget_enforcement`, `test_long_message_handling`, `test_recent_messages_preservation`, `test_older_history_summarization`, `test_session_expiration_handling`, `test_malformed_session_id_rejected`, `test_production_mongodb_failure_does_not_silently_use_memory`, `test_concurrent_messages_in_same_session`, `test_expired_session_cannot_be_reused`, `test_token_budget_hindi_hinglish_technical`, `test_summary_preserves_ids_numbers_dates_entities`, `test_untrusted_history_cannot_become_system_instructions`, `test_malicious_content_inside_older_summary_stay_escaped`, `test_session_history_chronological_ordering` |
| **Integration Tests** | **4** | `test_missing_or_invalid_jwt_fails_closed` (FastAPI TestClient auth rejection), `test_sse_disconnect_persistence_behavior[asyncio]` (Async SSE stream termination lifecycle), `test_phase4_validation_remains_enforced` (Full pipeline prompt injection rejection), `test_role_based_access_remains_enforced` (Full pipeline vector store RBAC isolation) |

### B. Full Repository Regression Suite
Command executed:
```bash
.venv\Scripts\python.exe -m pytest
```
**Result**: **358 passed, 27 skipped, 0 failures** in 41.48s.
*(Skipped tests are 2 live LLM tests and 25 live query-rewrite tests requiring active Groq API keys).*

---

## 5. Security & Invariant Analysis

### 1. IDOR Defense & Session Isolation
- **Authentication**: `POST /chat` resolves identity via `get_current_user` (`app/auth/jwt.py`). Unauthenticated or forged tokens fail immediately with HTTP 401.
- **Authorization**: Session lookup enforces `{"session_id": sid, "user_id": current_user.user_id}`.
- **Cross-User Access**: When User A attempts to access User B's `session_id`, `get_session()` raises HTTP 403 Forbidden without leaking session existence or metadata.
- **Client Body Disregard**: Request body `history` or `user_id` fields are completely ignored. Only server-side messages in MongoDB are loaded.

### 2. Token Budget & Context Management
- **Headroom Protection**: The 1500-token history budget guarantees ample room for system prompts (800 tokens), retrieved chunks (1500 tokens), and LLM generation (1000 tokens) within standard 4k/8k context windows.
- **Recent Turn Priority**: Truncation and summarization prune from the oldest turns forward, ensuring the immediate conversational context is preserved verbatim.
- **Entity Preservation**: Summaries retain critical entities including invoice numbers (`#INV-2026-99`), dates, policy names, and monetary values.

### 3. Factual Safety & Anaphora Resolution
- **Pronoun Resolution**: Hinglish pronouns ("uska", "iska", "ye") are correctly mapped to prior topic entities without hallucinating unmentioned terms.
- **Ambiguity Guard**: Ambiguous or ungrounded queries are preserved as standalone queries rather than incorrectly condensed.

### 4. Persistence Gating & Failure Modes
- **Grounding Validation Gate**: Assistant messages are saved **only** when `validate_grounded_answer()` confirms `is_valid=True`. Hallucinated or injection-tainted outputs are never written to the session database.
- **Retry Safety**: Generation retries inside LangGraph do not create duplicate or orphan assistant records.
- **Production Fail-Closed**: In `production` environment mode, unreachable MongoDB clusters raise HTTP 503 rather than silently degrading to volatile RAM.

---

## 6. Remaining Limitations & Deployment Risks

1. **Heuristic vs. Exact Tokenizer**:
   - *Limitation*: Token counting relies on character-to-token ratio heuristics (Devanagari ~1.2, Latin/Hinglish ~3.2) rather than an exact BPE model tokenizer.
   - *Mitigation*: The 25% safety margin prevents overflow in typical usage. If the LLM context window is reduced below 4,000 tokens, a exact tokenizer library (`tiktoken` or HuggingFace) should be integrated.
2. **MongoDB Replica Set Indexing**:
   - *Risk*: At high concurrency, missing compound indexes could cause query latency spikes.
   - *Action*: In production MongoDB Atlas, ensure compound indexes `{"session_id": 1, "user_id": 1}` and `{"updated_at": 1}` are created.
3. **SSE Exception Leakage Hardening**:
   - *Limitation*: Abrupt socket disconnections during generation log a warning and exit cleanly, but edge-case client reconnects are not tracked.
   - *Status*: SSE exception leakage hardening is explicitly allocated to Phase 6.

---

## 7. Production Deployment Conditions

The following conditions must be satisfied in production environments:

1. **`APP_ENV=production`**: Must be explicitly set in container environment variables to enforce fail-closed persistence checks.
2. **`MONGODB_URI`**: Must point to an authenticated MongoDB replica set with network firewall access from chatbot service instances.
3. **`SESSION_TTL_HOURS`**: Must be tuned according to company session retention compliance policies (default: 24 hours).
4. **Client Widget Integration**: Frontend widgets must store and send the `session_id` returned in the terminal SSE payload (`{"done": true, "session_id": "..."}`) or response header `X-Session-ID`.

---

## 8. Final Verdict

### **CONDITIONAL PASS**

Phase 5 meets all security, architectural, and reliability requirements for server-side conversation sessions, Hinglish conversation memory, and token-aware history management. Deployment to production is approved subject to fulfilling the deployment conditions outlined in Section 7 and completing Phase 6 (SSE Exception Leakage Hardening).
