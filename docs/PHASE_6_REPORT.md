# PHASE 6: SSE EXCEPTION SAFETY, API ERROR HANDLING & STREAM RELIABILITY

**Role**: Senior Backend Engineer and Application Security Engineer  
**Scope**: Phase 6 — SSE Exception Leakage Hardening, Stream Reliability, Disconnect Handling, and Safe Observability  
**Date**: November 20, 2026  
**Status**: **COMPLETED & VERIFIED**

---

## 1. Executive Summary

Phase 6 addresses the remaining exception leakage and stream reliability vulnerabilities in the FastAPI SSE endpoint (`POST /chat`). In distributed RAG applications, uncaught exceptions during Server-Sent Event streaming can expose sensitive infrastructure topology, database credentials, internal file paths, LLM system prompts, or Python stack traces to API clients.

This phase guarantees that:
1. **Pre-Streaming Failures**: Fail closed with appropriate HTTP status codes (400, 401, 403, 422, 500, 503) and uniform safe JSON bodies containing unique `request_id`s.
2. **In-Streaming Failures**: When an exception occurs after SSE headers are sent (HTTP 200), the server emits a structured, safe SSE error event (`INTERNAL_STREAM_ERROR`, `LLM_TIMEOUT`, `DATABASE_ERROR`), followed by a backward-compatible user token and exactly one terminal `done` event containing `session_id`.
3. **No Sensitive Leakage**: Python stack traces, MongoDB connection URIs with embedded passwords, Bearer tokens, API keys, and internal filesystem paths are completely prevented from appearing in client responses and scrubbed from server logs.
4. **Client Disconnect & Cancellation**: Mid-stream socket resets and task cancellations abort processing immediately, without duplicate `done` events, without attempting writes to closed sockets, and without persisting partial or orphan assistant messages.
5. **Phase 4 & Phase 5 Invariant Preservation**: Output is strictly buffered and validated before streaming; Phase 4 prompt injection and citation validations remain mandatory; Phase 5 session ownership and server-controlled history remain fully authoritative.

All 21 test scenarios in `tests/test_phase6_sse_safety.py` and all 379 non-skipped tests across the repository pass with zero regressions.

---

## 2. Previous Exception Leakage Risks & Root Causes

| Risk ID | Vulnerability | Root Cause | Impact |
|---|---|---|---|
| **R-01** | Raw Exception & Credential Exposure | Uncaught pre-stream exceptions in `chat()` or `session_manager` bubbled to FastAPI without sanitized structure. | Clients could receive raw connection strings (e.g., `mongodb+srv://user:pass@host`), database errors, or stack traces. |
| **R-02** | HTTP 200 on Pre-Stream Failure | If streaming initialization failed before the generator began, some frameworks or routes would start a 200 OK stream before failing. | HTTP semantics broken; client unable to detect 4xx/5xx failure status at transport level. |
| **R-03** | In-Stream Error Schema Inconsistency | Streaming errors in `_stream_langgraph_sse` yielded an unstructured text token with no machine-readable error code or request ID. | Frontend clients could not programmatically distinguish a model response from an internal error, nor correlate failures with server logs. |
| **R-04** | Broken Socket Exception Storm | When clients disconnected midway, continued attempts to yield to the closed socket raised `BrokenPipeError` or `ConnectionResetError`. | Noisy stack traces in server logs, wasted worker compute, and potential unclosed stream tasks. |
| **R-05** | Credential Logging in Exception Handlers | Handlers logged `str(exc)` directly; PyMongo connection exceptions often contain the full MongoDB connection string with credentials. | Sensitive database credentials and Bearer tokens leaked into application log files and log aggregators. |
| **R-06** | Duplicate `done` Event on Error | If an error occurred after updates had yielded, both the error handler and the normal exit path could potentially emit `done: true`. | Broken SSE client protocol parsing (contract requires exactly one terminal done event). |

---

## 3. Safe Error-Handling Design

### A. Pre-Streaming Error Handling
Before `StreamingResponse` headers are committed to the client:
- Validation errors (JWT missing, signature invalid, role forbidden, message length exceeded, malformed `session_id`, cross-user IDOR attempt) return accurate HTTP status codes (400, 401, 403, 422).
- Production persistence failures return HTTP 503 Service Unavailable.
- Any unhandled `Exception` is caught by an explicit pre-stream handler and the FastAPI global exception handler, returning HTTP 500 with a sanitized JSON payload:
```json
{
  "error": true,
  "code": "INTERNAL_ERROR",
  "message": "Something went wrong. Please try again.",
  "request_id": "req-9c8f2b1a0e4d",
  "detail": "Something went wrong. Please try again."
}
```

### B. In-Streaming Error Handling
Once HTTP 200 headers have been sent over the wire, the HTTP status code cannot be modified. The stream safely transitions to an error state by emitting:
1. **Safe SSE Error Event**:
```sse
data: {"error": true, "code": "INTERNAL_STREAM_ERROR", "message": "An error occurred while processing your request. Please try again later.", "request_id": "req-9c8f2b1a0e4d"}
```
2. **Backward-Compatible Token Event** (for legacy UI clients that parse `token`):
```sse
data: {"token": "An error occurred while processing your request. Please try again later."}
```
3. **Terminal Done Event**:
```sse
data: {"done": true, "sources": [], "error": true, "request_id": "req-9c8f2b1a0e4d", "session_id": "d82f7c0a-..."}
```
4. **Stream Closure**: Generator terminates cleanly (`return`), emitting no further chunks.

### C. Standardized Error Classification

Streaming exceptions are classified deterministically without exposing internal types:
- `TimeoutError`, `asyncio.TimeoutError`, or timeout strings: `code = "LLM_TIMEOUT"`
- MongoDB/database exceptions: `code = "DATABASE_ERROR"`
- All other unhandled runtime exceptions: `code = "INTERNAL_STREAM_ERROR"`

---

## 4. SSE Lifecycle & Disconnect/Cancellation Behavior

```mermaid
flowchart TD
    Req[Client POST /chat] --> Auth[JWT & Request Validation]
    Auth -- Invalid / Missing --> Resp4xx[Return HTTP 401 / 403 / 422]
    Auth -- Valid --> PreStream[Resolve Session & Save User Message]
    PreStream -- DB Outage (Prod) --> Resp503[Return HTTP 503]
    PreStream -- Internal Error --> Resp500[Return HTTP 500 + request_id]
    PreStream -- Success --> StreamStart[Start StreamingResponse (HTTP 200)]
    StreamStart --> CheckDC{Client Disconnected?}
    CheckDC -- Yes --> CleanExit[Abort Stream Immediately, No Output, No Done]
    CheckDC -- No --> RunGraph[LangGraph Execution]
    RunGraph -- LLM Timeout / Crash --> SSEError[Emit Safe SSE Error + Done + req_id]
    RunGraph -- Validation Failure --> Fallback[Emit Clean Fallback, Skip Save]
    RunGraph -- Validation Success --> StreamTokens[Stream Verified Output]
    StreamTokens --> Persist[Persist Assistant Message with reply_to]
    Persist --> Done[Emit Single done: true with session_id]
```

### Disconnect Handling:
- **During Generation**: `await request.is_disconnected()` is checked before yielding any chunk. If True, the generator exits immediately with no `done` event.
- **During Delivery**: If the TCP connection is severed while writing bytes, `(ConnectionResetError, BrokenPipeError)` is caught, logged as an informational disconnect, and cleanly exits without cascading exceptions.
- **Task Cancellation**: `asyncio.CancelledError` is logged and re-raised (`raise`), ensuring ASGI server task cancellation cleanups operate properly without resource leaks.

---

## 5. Logging, Privacy & Observability

To prevent credential leakage through log aggregation pipelines:
1. **URI & Credential Scrubbing**: Implemented `sanitize_log_message` in [app/middleware/logging.py](file:///f:/FKH_PROJECTS/chatbot-service/app/middleware/logging.py) to mask URIs matching `://user:pass@host`, `Bearer ey...` tokens, and `gsk_` / `sk-` API keys.
2. **Exception Class Logging**: Refactored `session_manager.py` and `nodes.py` to log `type(exc).__name__` instead of raw `exc` / `str(exc)`, preventing PyMongo from writing database connection strings to stdout/stderr.
3. **Structured Event Logging**: Added `log_stream_event()` for recording SSE lifecycle transitions (`stream_started`, `stream_completed`, `stream_error`, `stream_cancelled`) containing `request_id`, `session_id`, `stage`, and `latency_ms` without logging private prompts or document contents.

---

## 6. Files Modified

| File | Changes Made |
|---|---|
| [app/api/chat.py](file:///f:/FKH_PROJECTS/chatbot-service/app/api/chat.py) | Added pre-stream try/except returning HTTP 500 JSON with `request_id`; added `_classify_stream_exception` and `_emit_sse_error`; updated `_stream_langgraph_sse` with `done_emitted` flag, `ConnectionResetError` handling, cancellation re-raising, and `X-Request-ID` / `X-Session-ID` headers. |
| [app/main.py](file:///f:/FKH_PROJECTS/chatbot-service/app/main.py) | Added `_handle_unhandled_exception` (500) and `_handle_http_exception` (sanitizing 5xx HTTPExceptions) to FastAPI application. |
| [app/middleware/logging.py](file:///f:/FKH_PROJECTS/chatbot-service/app/middleware/logging.py) | Added credential scrubber `sanitize_log_message` and structured SSE telemetry `log_stream_event`. |
| [app/orchestration/nodes.py](file:///f:/FKH_PROJECTS/chatbot-service/app/orchestration/nodes.py) | Sanitized persistence exception logging to record `type(exc).__name__` instead of raw `exc`. |
| [app/rag/session_manager.py](file:///f:/FKH_PROJECTS/chatbot-service/app/rag/session_manager.py) | Sanitized MongoDB exception logging across all operations to prevent URI credential leakage. |
| [tests/test_phase6_sse_safety.py](file:///f:/FKH_PROJECTS/chatbot-service/tests/test_phase6_sse_safety.py) | Created 21 automated test cases covering all 18 Phase 6 safety and reliability requirements. |

---

## 7. Test Execution & Results

### A. Phase 6 Dedicated Test Suite
Command executed:
```bash
.venv\Scripts\python.exe -m pytest tests/test_phase6_sse_safety.py -v
```
**Results**: **21 passed in 11.63s** (0 failed, 0 skipped)

#### Coverage of Required Scenarios:
1. `test_exception_before_sse_starts` — **PASSED** (Pre-stream crash returns HTTP 500 safe JSON with `request_id`).
2. `test_exception_after_sse_starts` — **PASSED** (In-stream crash yields safe SSE error event and closes).
3. `test_no_traceback_in_client_response` — **PASSED** (Zero Python traceback frames or lines in client output).
4. `test_no_database_details_in_client_response` — **PASSED** (MongoDB URIs and passwords never leak).
5. `test_no_secret_or_token_leakage` — **PASSED** (Bearer tokens and API keys never leak).
6. `test_valid_sse_event_formatting` — **PASSED** (Strict adherence to `data: <json>\n\n`).
7. `test_safe_error_event` — **PASSED** (Error event schema contains `error`, `code`, `message`, `request_id`).
8. `test_correct_http_status_before_streaming` — **PASSED** (401, 403, 422, 400 status codes correctly returned).
9. `test_client_disconnect_handling` — **PASSED** (Immediate generator termination, 0 done events).
10. `test_llm_timeout` — **PASSED** (Classifies as `LLM_TIMEOUT` with safe user message).
11. `test_mongodb_failure_handling` — **PASSED** (Production DB outage returns HTTP 503 without URI leak).
12. `test_retrieval_failure_handling` — **PASSED** (Retrieval crash fails closed to fallback without DB paths).
13. `test_validation_failure_handling` — **PASSED** (Phase 4 rejection is omitted from persistent session history).
14. `test_no_partial_assistant_persistence` — **PASSED** (Aborted generation leaves 0 assistant messages).
15. `test_no_duplicate_done_event` — **PASSED** (Exactly one `done: true` event across stream lifecycle).
16. `test_session_id_still_returned` — **PASSED** (Terminal event retains `session_id` even on stream errors).
17. `test_phase4_validation_still_runs_before_output` — **PASSED** (System prompt extraction attempt blocked).
18. `test_phase5_session_authorization_remains_enforced` — **PASSED** (IDOR session access returns HTTP 403).

### B. Full Repository Regression Suite
Command executed:
```bash
.venv\Scripts\python.exe -m pytest
```
**Results**: **379 passed, 27 skipped, 0 failures** in 51.78s.
*(The 27 skipped tests are live Groq and live LLM integration tests requiring external API credentials).*

---

## 8. Remaining Limitations

1. **Client Reconnection State Synchronization**:
   - *Limitation*: If a client disconnects midway and reconnects with a fresh request, the aborted turn is not replayed; only completed turns exist in session history.
   - *Status*: Standard industry practice for stateless SSE connections.
2. **Distributed Trace Propagation**:
   - *Limitation*: `request_id` is generated locally on the API gateway instance. In a microservices mesh (e.g. OpenTelemetry, Jaeger), incoming `X-Request-ID` or `traceparent` headers should be extracted and propagated across downstream services.

---

## 9. Conclusion

Phase 6 is complete and verified. The SSE streaming pipeline and API error handling are now hardened, leak-free, production-ready, and resilient against abrupt disconnections and unexpected runtime exceptions.
