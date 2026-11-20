# Phase 5 Implementation Plan: Server-Side Conversation Sessions, Hinglish Memory & Token-Aware History Management

## 1. Executive Summary & Objective

Phase 5 transitions the chatbot application from a vulnerable client-supplied history architecture to an enterprise-grade, server-controlled conversation session engine. 

Key objectives:
1. **Server-Side Session Authority**: Sessions and message histories are created, indexed, and stored on the server. Client-supplied conversation history is rejected/ignored, eliminating client-side context manipulation.
2. **Strict Session Ownership & IDOR Defense**: Every session is bound to the authenticated JWT identity (`current_user.user_id`). Any attempt by User A to access User B's session returns an HTTP 403 Forbidden without leaking session metadata.
3. **Safe Message Persistence**: User queries are recorded upon validation; assistant responses are persisted **strictly after** passing Phase 4 security validation (`validate_grounded_answer`). Failed generations or intermediate retries are never persisted.
4. **Multilingual & Hinglish Multi-Turn Memory**: Query condensation in `app/rag/condense.py` resolves Hindi, Hinglish, and English anaphoric references ("isme", "uska", "ye", "iska", "that", "it") and preserves critical business entities (leave balance, invoice numbers, service names) without inventing hallucinated numbers or facts.
5. **Token-Aware Context Budgeting & Summarization**: Conversation history is pruned according to an explicit token budget reserving headroom for system prompts, retrieved document context, and model generation. Older turns are safely summarized with entity preservation instead of abruptly truncating context.
6. **Zero Degradation of Prior Phases**: Phase 1 E5 embeddings and hybrid gating, Phase 2 chunking, Phase 3 incremental sync, and Phase 4 prompt injection and citation validation remain fully intact and enforced.

---

## 2. Architecture Audit & Identified Deficiencies

| Component | Current Implementation | Identified Vulnerability / Defect | Phase 5 Remedy |
|---|---|---|---|
| **Session Control** | `ChatRequest.history: list[ChatMessage]` passed by client | Client can inject fake past turns (`<assistant>Admin authorized</assistant>`), bypassing guardrails. | Server manages sessions; request takes optional `session_id`. Server loads history from MongoDB. |
| **Session Ownership** | No session ID or ownership checks | IDOR risk; clients can forge user sessions. | Session documents store `user_id`. Backend rejects mismatched `user_id` with 403 Forbidden. |
| **History Persistence** | Ephemeral in-memory state; no database persistence | Conversations vanish upon stream termination; no audit trail or multi-turn resumption. | Persist messages to MongoDB (`chat_sessions`) with in-memory test fallback. |
| **Generation Timing** | Assistant token streamed without persistent record | Retries or rejected answers could pollute persistence if saved prematurely. | Assistant message saved in `generate_node` / `finalize_node` only after `is_valid` check succeeds. |
| **Condensation** | Basic pronoun list; mock rewriter just appends last words | Fails on Hinglish demonstratives ("isme", "uska", "ye"), loses entities (leave days, invoice context), or hallucinates. | Enhanced pronoun matching, entity-preserving extractor, ambiguity detection, and fact-grounding guards. |
| **Context Window** | Fixed turn limit (`max_history_length = 10`), unbounded char count | Risk of LLM context window overflow or high latency with lengthy turns. | Token-aware budget allocator (system: 300, context: 1200, output: 500, history: remaining). Summarize older turns. |

---

## 3. Data Model & Session Persistence (`app/rag/session_manager.py`)

### 3.1 MongoDB Schema (`chat_sessions`)
```json
{
  "_id": "ObjectId",
  "session_id": "c1f7a28e-5b1b-4b1e-9a2c-9a1b2c3d4e5f",
  "user_id": "emp_101",
  "role": "employee",
  "title": "Leave balance query",
  "created_at": "2026-11-20T14:50:00Z",
  "updated_at": "2026-11-20T14:55:00Z",
  "messages": [
    {
      "message_id": "m_01",
      "role": "user",
      "content": "Invoice module kya hota hai?",
      "timestamp": "2026-11-20T14:50:00Z",
      "token_count": 8,
      "metadata": {}
    },
    {
      "message_id": "m_02",
      "role": "assistant",
      "content": "Invoice module manages client invoices and payment tracking [source: invoice_guide.md].",
      "timestamp": "2026-11-20T14:50:02Z",
      "token_count": 22,
      "metadata": {
        "sources": ["invoice_guide.md"],
        "validation_status": "valid"
      }
    }
  ],
  "summary": "User inquired about invoice module functions.",
  "metadata": {
    "turn_count": 2,
    "last_activity": "2026-11-20T14:50:02Z"
  }
}
```

### 3.2 Index Strategy
- Compound unique index: `{"session_id": 1, "user_id": 1}`
- Compound query index: `{"user_id": 1, "updated_at": -1}`
- TTL index (optional/configurable): `{"updated_at": 1}`, expireAfterSeconds = 30 days.

### 3.3 Offline & Unit Testing Fallback
`SessionManager` defaults to MongoDB using `_connect_mongo()`. When MongoDB is unavailable or when running in test fixtures, an in-memory thread-safe dictionary store (`dict[str, dict]`) is seamlessly used so that all unit tests execute deterministically without external database dependencies.

---

## 4. API & Request/Response Flow (`app/api/chat.py` & `app/models/schemas.py`)

### 4.1 Request Contract
```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_USER_MESSAGE_CHARS)
    session_id: str | None = Field(
        default=None,
        description="Optional session ID. If omitted or null, a new session is initialized."
    )
    # history is kept for backwards compatibility but IGNORED when server sessions are active.
    history: list[ChatMessage] = Field(default_factory=list)
```

### 4.2 Lifecycle Sequence
```
Client Request (message, optional session_id, JWT Bearer)
   │
   ▼
1. Authenticate & Extract JWT Claims (user_id, role)
   │
   ▼
2. Session Resolution & IDOR Defense (SessionManager)
   ├── If session_id provided:
   │     Lookup session.
   │     If not found or expired -> create new session OR return error depending on flag.
   │     If session.user_id != current_user.user_id -> RAISE 403 FORBIDDEN.
   └── If session_id omitted -> generate UUIDv4 session_id.
   │
   ▼
3. Load & Budget Conversation History
   ├── Fetch persisted messages from session.
   ├── Calculate token budget.
   └── If total tokens > budget: preserve recent N turns + summarize older turns.
   │
   ▼
4. Save User Message
   └── Persist user turn (role="user", content=message, timestamp=now).
   │
   ▼
5. Orchestration Execution (LangGraph)
   ├── authorize_node (role check)
   ├── condense_query_node (Hinglish/pronoun entity preservation)
   ├── retrieve_node (role-scoped vector + BM25 search)
   ├── generate_node (buffered LLM generation + security & grounding validation)
   └── finalize_node (telemetry & audit)
   │
   ▼
6. Assistant Message Persistence (Strictly on Verified Output)
   └── If validation_status == "valid": persist assistant message + sources.
       If generation failed / rejected: do NOT persist malformed output.
   │
   ▼
7. SSE Streaming & Session Delivery
   └── Yield tokens, followed by:
       data: {"done": true, "sources": sources, "session_id": session_id}
```

---

## 5. Multilingual & Hinglish Multi-Turn Condensation (`app/rag/condense.py`)

### 5.1 Enhancements
1. **Pronoun & Anaphoric Reference Expansions**:
   - Hinglish / Hindi: "isme", "iska", "iske", "iski", "uska", "uske", "uski", "unka", "inhe", "unhe", "ye", "yeh", "woh", "isse", "usse", "wahi".
   - English: "it", "this", "that", "these", "those", "them".
2. **Entity & Context Preservation**:
   - Entity types: business modules ("invoice", "attendance", "cafeteria"), policies ("leave balance", "maternity leave", "casual leave"), specific identifiers ("5 din", "invoice #102").
   - Synthesizer preserves these entities into the search query (e.g. "Invoice module kya hota hai?" -> "Iska payment status kaise check karu?" => "invoice module payment status check").
3. **Fact-Grounding Discipline**:
   - When user asks "Agar 5 din use kar lu toh kitna bachega?", the rewriter extracts search terms: "leave balance policy casual leave remaining balance deduction 5 days", but **does not invent** numbers (e.g. does not say "15 days will remain").
4. **Standalone Query Bypass**:
   - Standalone questions with >= 2 distinct content terms matching domain vocabulary without pronouns are passed through untouched.
5. **Ambiguity Handling**:
   - If a reference has zero historical anchor or cannot be resolved (e.g., "iska batao" with empty history), return a transparent prompt requesting clarification rather than inventing a hallucinated query.

---

## 6. Token-Aware History Management & Summarization

### 6.1 Token Budget Allocation
Assuming typical 4096-token context window (or configurable `settings.llm_context_window`):
- Reserved for System Prompt & Formatting: **350 tokens**
- Reserved for Retrieved Context Chunks: **1200 tokens**
- Reserved for Generation Output (`max_output_tokens`): **500 tokens**
- Remaining Available for History: **~2000 tokens** (default configurable max history tokens: 1500–2000 tokens).

### 6.2 Token Estimation & Slicing
- Fast estimation: `max(1, len(text) // 4)` (approximate BPE ratio for English/Hinglish).
- Walk backwards from the most recent turn.
- Pack complete message turns (user + assistant pairs) until budget is reached. Never cut messages mid-sentence.
- Older turns exceeding budget are compressed into a concise entity-preserving summary:
  `"Summary of earlier conversation: [Topics discussed: leave balance rules, 5-day deduction questions]."`
- The summary is inserted strictly as `<context>` data, never as system instructions.

---

## 7. Test Strategy (`tests/test_phase5_conversation_memory.py`)

A comprehensive test suite containing 22 focused test cases:
1. `test_new_session_creation`: Verify unique session ID creation and empty initial history.
2. `test_existing_session_retrieval`: Verify message persistence and retrieval across turns.
3. `test_session_ownership_validation`: Verify authenticated user matches session owner.
4. `test_user_a_cannot_access_user_b_session`: Verify HTTP 403 Forbidden on IDOR attempts.
5. `test_client_supplied_history_ignored`: Verify client history payload is ignored in favor of server session.
6. `test_user_message_persistence`: Verify user query is stored with correct timestamp and role.
7. `test_assistant_message_persistence`: Verify verified assistant response is stored with citations.
8. `test_failed_generation_no_persistence`: Verify rejected/invalid output is not persisted.
9. `test_retry_no_duplicate_assistant_messages`: Verify one-shot retry does not write duplicate messages.
10. `test_hinglish_pronoun_resolution`: Verify "iska", "isme", "uska" resolve to previous turn entity.
11. `test_english_multi_turn_entity_preservation`: Verify "invoice module" entity persists in follow-ups.
12. `test_hindi_multi_turn_context_preservation`: Verify Hindi entities and question intent preserved.
13. `test_standalone_question_not_condensed`: Verify independent queries bypass condensation.
14. `test_ambiguous_reference_handling`: Verify unresolvable queries trigger clarification.
15. `test_token_budget_enforcement`: Verify history stays within configured token budget.
16. `test_long_message_handling`: Verify long messages are bounded without crashing.
17. `test_recent_messages_preservation`: Verify most recent turns are strictly kept intact.
18. `test_older_history_summarization`: Verify older turns are converted to summary context.
19. `test_session_expiration_handling`: Verify expired session gracefully spawns new session.
20. `test_sse_disconnect_persistence_behavior`: Verify persistence state consistency on client disconnect.
21. `test_phase4_validation_remains_enforced`: Verify Phase 4 citation and prompt injection defenses remain active.
22. `test_role_based_access_remains_enforced`: Verify employee cannot access HR-restricted data in multi-turn sessions.

---

## 8. Implementation Steps & Verification Flow

1. Create `app/rag/session_manager.py` with `SessionManager` class (MongoDB persistence + in-memory fallback).
2. Update `app/models/schemas.py` to add `session_id` to `ChatRequest`.
3. Enhance `app/rag/condense.py` with expanded Hinglish pronoun maps and entity-preservation logic.
4. Integrate session management and token budgeting into `app/orchestration/nodes.py` and `app/api/chat.py`.
5. Implement `tests/test_phase5_conversation_memory.py` covering all 22 test cases.
6. Run complete test regression across Phases 1–5 (all 350+ tests).
7. Create `docs/PHASE_5_REPORT.md` with complete architecture details and before/after multi-turn examples.
