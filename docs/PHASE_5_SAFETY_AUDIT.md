# Phase 5 — Production Safety Audit Report

**Auditor Role**: Senior Backend Engineer & Security Reviewer  
**Audit Scope**: Server-Side Conversation Sessions, MongoDB Persistence, Hinglish Memory, Token-Aware History Management & SSE Lifecycle  
**Date**: November 20, 2026  
**Final Verdict**: **APPROVED WITH CONDITIONS**

---

## 1. Files Inspected

- `app/rag/session_manager.py`: Session store, ownership verification, MongoDB collection integration, in-memory fallback, and message persistence.
- `app/api/chat.py`: Authentication integration, session ID extraction, request authorization, history budgeting, SSE token streaming, and session return.
- `app/rag/history_budget.py`: Token calculation, script-aware estimation heuristic, context headroom reservation, and older turn summarization.
- `app/orchestration/nodes.py`: Validation-gated assistant message persistence, error handling, and user message turn correlation.
- `app/orchestration/state.py`: TypedDict state definition (`session_id`, `user_message_id`, `history_summary`).
- `app/rag/condense.py`: Multilingual anaphoric pronoun recognition, domain keyword extraction, and ambiguity detection.
- `app/config.py`: Environment configuration, session TTL settings, and context window bounds.
- `tests/test_phase5_conversation_memory.py`: Automated test suite covering 30 production safety and regression scenarios.

---

## 2. Architecture & Request Lifecycle Findings

### Lifecycle Flow
1. **Authentication**: `POST /chat` verifies the incoming Bearer JWT via `get_current_user` (`app/auth/jwt.py`). Unauthenticated, expired, or signature-invalid tokens fail closed immediately with HTTP 401.
2. **Session Identification & Ownership**: If `session_id` is supplied, `SessionManager.get_session()` validates that `session["user_id"] == current_user.user_id`. Any mismatch raises HTTP 403 Forbidden without exposing session metadata (IDOR defense).
3. **Session Expiry & Lifecycle**: Sessions are checked against `settings.session_ttl_hours` (default 24h). Expired sessions cannot be accessed or reused. Requesting an expired or non-existent session safely triggers the creation of a brand new session with a new UUIDv4, rather than resurrecting an expired session.
4. **History Budgeting**: Server-stored history is pruned using conservative script-aware token heuristics (Devanagari ~1.2 chars/token, Latin ~3.2 chars/token) ensuring system instructions, context chunks, and generation headroom are protected. Older turns exceeding the 1500-token budget are compressed into a summary block.
5. **Persistence Sequencing**:
   - The user query is persisted at the beginning of the request, returning a unique `user_message_id`.
   - The LangGraph pipeline retrieves chunks, rewrites anaphoric references, and generates a buffered candidate answer.
   - The assistant message is persisted **strictly after** `validate_grounded_answer` confirms `is_valid=True`, linking `metadata["reply_to"] = user_message_id`. Rejected or malicious tokens are discarded and never written to storage.
   - The terminal SSE event delivers `data: {"done": true, "sources": [...], "session_id": "..."}` and response header `X-Session-ID`.

---

## 3. Findings & Defect Classification

| ID | Category | Severity | Description | Status |
|---|---|---|---|---|
| **F-01** | Persistence Safety | **Critical** | Production silently fell back to ephemeral in-memory storage if MongoDB was unreachable or write failed. | **Fixed & Verified** |
| **F-02** | Session Lifecycle | **High** | Absence of server-side TTL expiry check allowed stale sessions to be loaded; expired session IDs were resurrected as empty sessions with the same ID. | **Fixed & Verified** |
| **F-03** | Token Estimation | **Medium** | Naive `len // 4` token estimation severely underestimated Devanagari Hindi text (60–70% under-count), risking context overflow. | **Fixed & Verified** |
| **F-04** | Input Validation | **Medium** | Malformed session IDs (path traversal characters, excessive length) were not strictly validated before querying. | **Fixed & Verified** |
| **F-05** | Message Ordering | **Low** | Concurrent requests on the same session did not explicitly link assistant responses to their triggering user questions. | **Fixed & Verified** |
| **F-06** | Exception Leakage | **Informational** | Raw exceptions during LangGraph streaming yield a standard client error token, but unhandled socket disconnects require phase 6 cleanup. | **Deferred to Phase 6** |

---

## 4. Evidence, Reproduction & Confirmed Fixes

### Finding F-01: Silent In-Memory Fallback in Production (Critical)
- **Vulnerable Code**: `app/rag/session_manager.py` previously caught all exceptions during MongoDB collection acquisition and write operations, falling back to global `_MEMORY_SESSIONS` dictionary even when `settings.app_env == "production"`.
- **Reproduction**:
  ```python
  settings.app_env = "production"
  settings.mongodb_uri = "mongodb://127.0.0.1:19999" # unreachable host
  sm = SessionManager()
  doc = sm.create_session("prod_sess", "user_1", "employee")
  assert "prod_sess" in _MEMORY_SESSIONS # Succeeded silently in memory!
  ```
- **Risk**: In a containerized multi-worker production cluster, database outages would silently write conversations to random worker memories, causing silent data loss on container restart and split-brain session reads.
- **Fix**: Implemented `_enforce_production_persistence_check()`. When `settings.app_env == "production"`, any failure to acquire MongoDB or write to the database raises an immediate `HTTPException(503, "Database persistence unavailable: session operation cannot proceed in production")`. In-memory storage is strictly gated to `development` and `test` environments.

### Finding F-02: Absence of Server-Side Session TTL & Resurrected Session IDs (High)
- **Vulnerable Code**: `get_session()` did not inspect `updated_at`. If MongoDB's background TTL monitor thread had not yet purged an expired document, the session was retrieved as active. Furthermore, requesting an expired session ID called `create_session(session_id, user_id, role)`, resurrecting the old expired session ID as an empty conversation.
- **Reproduction**:
  ```python
  # Set updated_at to 48 hours ago
  _MEMORY_SESSIONS[sid]["updated_at"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
  assert sm.get_session(sid, "user_1") is not None # Stale session returned!
  ```
- **Fix**:
  1. Added `session_ttl_hours: int = 24` to `app/config.py`.
  2. Implemented `is_session_expired(session_doc)` validating timestamp delta against configured TTL.
  3. Ownership verification is executed *first* (User B accessing User A's expired session receives 403 Forbidden).
  4. If an expired session is accessed by its owner, `get_session` returns `None`. `get_or_create_session` assigns a brand new UUIDv4 session ID, ensuring expired IDs are never resurrected as empty sessions.

### Finding F-03: Devanagari Hindi Token Underestimation (Medium)
- **Vulnerable Code**: `app/rag/history_budget.py` used `max(1, len(text) // 4)`.
- **Reproduction**:
  The 44-character Hindi sentence `"कार्यालय में आकस्मिक अवकाश के नियम क्या हैं?"` resulted in `estimate_tokens() == 11`. In standard BPE tokenizers (Groq, Llama, OpenAI), each Devanagari character averages ~1.2 characters per token (actual token count ~28–34). An estimate of 11 represents a 65% underestimation.
- **Fix**: Upgraded `estimate_tokens()` to detect Unicode codepoint ranges (`\u0900-\u097f`). Devanagari characters are budgeted at ~1.2 chars/token, while Latin and Hinglish characters include a 25% safety margin (~3.2 chars/token).

### Finding F-04: Malformed Session ID Validation (Medium)
- **Vulnerable Code**: `ChatRequest.session_id` accepted arbitrary strings, allowing path-traversal strings (`"../../etc/passwd"`) or oversized 10,000-character strings into storage queries.
- **Fix**: Implemented `validate_session_id()`, enforcing a maximum length of 128 characters and strict regex pattern `^[a-zA-Z0-9_-]+$`. Malformed inputs trigger an immediate HTTP 400 Bad Request.

### Finding F-05: Missing User-Assistant Message Correlation (Low)
- **Vulnerable Code**: Assistant messages were appended without a pointer to the user message turn they resolved.
- **Fix**: `save_user_message()` returns the generated `message_id`. This `user_message_id` is tracked in `ChatState` and passed to `save_assistant_message()`, which records `metadata["reply_to"] = user_message_id`.

---

## 5. Automated Test Coverage & Verification

30 comprehensive tests in `tests/test_phase5_conversation_memory.py` verify all audit requirements:

```bash
.venv\Scripts\python.exe -m pytest tests/test_phase5_conversation_memory.py -v
```
**Results**: **30 passed in 18.13s** (0 failed)

### Key Audit Test Verification:
1. `test_missing_or_invalid_jwt_fails_closed` — PASSED
2. `test_malformed_session_id_rejected` — PASSED (HTTP 400 on traversal/oversized IDs)
3. `test_production_mongodb_failure_does_not_silently_use_memory` — PASSED (HTTP 503 raised)
4. `test_user_a_cannot_access_user_b_session` — PASSED (IDOR defense HTTP 403)
5. `test_client_supplied_history_ignored` — PASSED (Authoritative server history enforced)
6. `test_concurrent_messages_in_same_session` — PASSED (`reply_to` correlation validated)
7. `test_retry_no_duplicate_assistant_messages` — PASSED (Single record on generation retries)
8. `test_expired_session_cannot_be_reused` — PASSED (Server-side TTL enforcement, new UUID assigned)
9. `test_token_budget_hindi_hinglish_technical` — PASSED (Script-aware token budgeting)
10. `test_summary_preserves_ids_numbers_dates_entities` — PASSED (#INV-2026-99 preserved)
11. `test_untrusted_history_cannot_become_system_instructions` — PASSED (XML escaping active)
12. `test_failed_generation_no_persistence` — PASSED (Rejected output discarded)
13. `test_phase4_validation_remains_enforced` — PASSED (Prompt injection defense active)
14. `test_role_based_access_remains_enforced` — PASSED (HR documents isolated from employees)

### Regression Suite (Phases 1–5):
```bash
.venv\Scripts\python.exe -m pytest tests/test_phase1_embeddings_retrieval.py tests/test_phase2_mongo_integrity.py tests/test_phase3_incremental_sync.py tests/test_phase4_llm_security.py tests/test_phase5_conversation_memory.py tests/test_sync.py tests/test_hybrid.py tests/test_retrieval.py tests/test_access.py tests/test_invariants.py
```
**Results**: **164 passed, 2 skipped (live LLM tests), 0 failures** in 19.18s.

### Full Repository Suite:
```bash
.venv\Scripts\python.exe -m pytest
```
**Results**: **356 passed, 27 skipped (live Groq / live LLM suites), 0 failures** in 37.03s.

---

## 6. Remaining Limitations & Production Deployment Conditions

1. **Exact Tokenizer vs. Heuristic**: While the script-aware heuristic eliminates undercounting risks for Hindi, Hinglish, and English, production deployments operating near the strict context boundary (e.g. 4096 tokens) should configure an exact tokenizer matching the deployed model (e.g., `tiktoken` for OpenAI, HuggingFace tokenizer for open weights).
2. **MongoDB Replica Set & Indexing**: In production, ensure MongoDB Atlas indexes on `(session_id, user_id)` and `(user_id, updated_at)` are created before high-concurrency traffic arrives.
3. **Phase 6 Scope Isolation**: Client socket disconnects during streaming emit a warning log and abort the generator. Edge-case exception leakage in SSE streaming is deferred to Phase 6 per project rules.

---

## 7. Explicit Audit Verdict

### **APPROVED WITH CONDITIONS**

**Conditions for Production Deployment**:
1. `APP_ENV=production` must be set in the production environment variables to activate fail-closed persistence enforcement.
2. `MONGODB_URI` must point to an active, authenticated MongoDB Atlas replica set with network access enabled.
3. Client frontend widgets must store and pass the server-issued `session_id` returned in the terminal SSE event.
4. Phase 6 (SSE exception leakage hardening) should proceed to finalize production streaming robustness.
