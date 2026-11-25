# Phase 8.1: Production Readiness Blocker Investigation and Resolution Report — Fakhri ERP AI

**Date:** 2026-11-25  
**Product:** Fakhri ERP AI (`chatbot-service` + `chatbot-widget`)  
**Previous Status:** `CONDITIONAL / STAGING READY — NOT YET CLEARED FOR PRODUCTION LAUNCH` (`docs/FINAL_ENTERPRISE_AI_PRODUCTION_READINESS_REPORT.md`)  
**Updated Classification:** `CONDITIONAL / STAGING-READY & REMEDIATION VERIFIED — PENDING HOST AUTH DECISION, SESSION/FEEDBACK BACKEND APIS & LIVE VISUAL QA`

---

## 1. Executive Summary

Following the Phase 8 audit, a controlled remediation and root-cause investigation was performed across all five identified production blockers:

1. **Blocker 1 (Live Browser QA Environment):** Diagnosed `ENOENT: C:\Users\Lenovo\AppData\Local\Google\Chrome\User Data\DevToolsActivePort`. Chrome (`158.0.8127.36`) is installed and launches cleanly with CDP HTTP debugging on an isolated temporary `--user-data-dir`, and both frontend (`http://localhost:3001/`) and backend (`http://127.0.0.1:8000/health`) are live (`200 OK`). The IDE `mcp_chrome_devtools` subagent cannot connect because it hardcodes the default user Chrome profile path while 25 existing Chrome user processes run without remote debugging. **Live visual browser QA remains pending manual or isolated Playwright execution and is not marked visual-PASS.**
2. **Blocker 2 (Production Authentication & Dev JWT Gating):** Confirmed zero `/auth/login` or OIDC/JWKS routes exist in `chatbot-service`; backend supports shared-secret `HS256` JWT validation (`app/auth/jwt.py`). Implemented build/runtime security gating (`src/services/securityGate.ts`, `enableDevAuth`) so production Vite builds (`import.meta.env.DEV === false` without explicit `VITE_ENABLE_DEV_AUTH="true"`) strip raw JWT pasting, offline role switching, and dev `"Sign In (Load Role)"` flows from the user UI without inventing a fake login form. Documented host SSO vs external IdP OIDC options for Product Owner decision.
3. **Blocker 3 (Enterprise Session REST API & `localStorage` Migration):** Documented that `SessionManager` (`app/rag/session_manager.py`) already persists sessions in MongoDB `chat_sessions` (`session_id`, `user_id`, `role`, timestamps, IDOR ownership checks, and indexes), while HTTP routes only expose `POST /chat`. Authored the complete REST API contract, strict `(user_id, role)` authorization invariants, pagination spec, and `localStorage` deprecation/migration plan prior to backend implementation.
4. **Blocker 4 (Message Feedback Persistence):** Confirmed no `POST /feedback` endpoint or schema exists. Remediated misleading UI state by hiding feedback thumbs-up/down controls unless an explicit persistence-backed `onPersistFeedback` handler is supplied (`shouldRenderFeedbackControls` defaults to `false`). Copy, Regenerate, Pagination cards, and Citations remain active. Documented the target `POST /feedback` schema and authorization rules.
5. **Blocker 5 (`scripts/verify_production.py` Failures):** Investigated all 4 failing checks:
   - Fixed a real defect in `app/rag/condense.py` where demonstrative determiners paired with explicit nouns (e.g., `"what is kpi as per this application"`) falsely triggered `is_ambiguous_reference(...) == True` (`CLARIFICATION_PROMPT`) instead of retrieving `mongo:kpidefinitions`.
   - Kept orphan anaphoric pronoun protection intact (`"how much its salary"` without history legitimately asks for clarification).
   - Fixed brittle delimiter assumptions (`atul_bangre`/`super-admin` vs `ATUL BANGRE`/`Super Admin`) and updated `scripts/verify_production.py` to test both ambiguity clarification and salary non-disclosure separately. All **9 checks pass** (`ALL 9 PRODUCTION DATA CHECKS PASSED`), and regression tests were added (`400 passed, 27 skipped` in `pytest`).

---

## 2. Blocker 1 — Live Browser QA Investigation & Reproducible QA Guide

### 2.1 Environment & Root-Cause Evidence

| Diagnostic Check | Observed Result |
|---|---|
| Chrome executable | `C:\Program Files\Google\Chrome\Application\chrome.exe` (`Exists: True`, `Chrome/158.0.8127.36`) |
| Edge executable | `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` (`Exists: True`) |
| Default profile port file | `C:\Users\Lenovo\AppData\Local\Google\Chrome\User Data\DevToolsActivePort` -> `Exists: False` (`RUNNING_CHROME_COUNT: 25`) |
| Isolated headless Chrome launch | Started cleanly on `--remote-debugging-port=9229 --user-data-dir=<temp>`; temp PID cleanly terminated & temp dir removed after verification |
| Isolated CDP endpoint | `http://127.0.0.1:9229/json/version` returned valid JSON with `"webSocketDebuggerUrl": "ws://127.0.0.1:9229/devtools/browser/..."` |
| Backend reachability | `http://127.0.0.1:8000/health` returned `{"status":"ok","version":"1.0.0","orchestrator":"langgraph","llm_provider":"gemini"}` |
| Frontend reachability | `http://localhost:3001/` returned HTML serving `/src/demo/main.tsx` |

**Root Cause:** `mcp_chrome_devtools` attempts to read `DevToolsActivePort` inside the default user profile directory (`%LOCALAPPDATA%\Google\Chrome\User Data`). Because standard user Chrome processes are already running without remote debugging enabled, Chrome does not write `DevToolsActivePort` there. Terminating existing user Chrome instances or modifying the user profile is prohibited, so IDE-managed browser subagent inspection remained blocked at the harness boundary.

### 2.2 Reproducible Live Browser QA Procedure (Human or External Playwright Runner)

Execute against running backend (`uv run uvicorn app.main:app --port 8000`) and frontend (`npm run dev -- --port 3001` inside `chatbot-widget/`), using an isolated browser profile so user data stays untouched:

```powershell
# Launch isolated Chrome instance for manual/CDP inspection
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --user-data-dir="$env:TEMP\fakhri-qa-profile" `
  --remote-debugging-port=9229 `
  --no-first-run --no-default-browser-check `
  "http://localhost:3001/"
```

**Required Manual / Playwright Verification Matrix (Open Status):**
1. **Desktop (`>=1440px` & `1024–1439px`):** Verify sidebar collapse/expand, Area C Contextual Panel toggle, header badge layout, message table horizontal scrolling (`chat-table-wrapper`), and absence of thumbs-up/down buttons when `onPersistFeedback` is unset.
2. **Tablet (`768–1023px`) & Mobile (`320–767px`):** Verify mobile slide-out drawer with backdrop overlay, `Esc`/backdrop dismiss, zero horizontal body overflow, composer sticky input above virtual keyboard simulation.
3. **Production Build Verification (`npm run preview`):** Verify `dist/` production bundle hides `"Set JWT Persona"`, `"Paste Raw JWT Token"`, `"Authorized Roles"` switcher, and `"Sign In (Load Role)"`, displaying `"Host Session Required"` / `"Sign in via host ERP portal"` for unauthenticated guests.
4. **Live SSE & Accessibility Walkthrough:** Load valid dev JWT tokens in `npm run dev`, test keyboard navigation (`Cmd/Ctrl+K`, `Cmd/Ctrl+F`, `Esc`, `Tab` focus rings), verify streaming SSE answer + AbortController Stop button + Copy + Regenerate + Search modal + Rename/Delete dialogs.

---

## 3. Blocker 2 — Production Authentication & Dev JWT Gating

### 3.1 Verified Repository Authentication State

- `app/auth/jwt.py` implements stateless `HS256` token validation using `settings.jwt_secret` (`decode_jwt_token`, `get_current_user`), extracting required `sub` and `role` (checked against `ALLOWED_ROLES = frozenset(ROLE_ACCESS.keys())`) and optional `exp`.
- `app/main.py` mounts only `health_router` (`GET /health`), `chat_router` (`POST /chat`), and `sync_router` (`POST /sync/mongo`). There are **no** `/auth/login`, `/auth/token`, `/auth/refresh`, `/auth/logout`, or OIDC/JWKS handlers in `chatbot-service`.
- Host ERP platform (`fakhri2_0`) SSO collections (`ssosigningkeys`, `ssoauthorizationcodes`, `ssoauditlogs`, `connectedapps`) are explicitly excluded from ingestion in `app/rag/mongo_catalog.py`.

### 3.2 Implemented Frontend Security Gating

To eliminate developer credential exposure in production bundles without fabricating a login screen:

- Added `chatbot-widget/src/services/securityGate.ts`:
  - `resolveDevAuthEnabled(isDevEnv, optInFlag)`: returns `true` only when `isDevEnv` (`import.meta.env.DEV`) is `true` OR `VITE_ENABLE_DEV_AUTH === "true"`. Defaults to **`false`** in `npm run build`.
  - `canOpenAdminWorkspaceSettings(enableDevAuth, activeRole)`: when `enableDevAuth === false`, restricts admin workspace modal access strictly to `super-admin` and `founder`.
- Added `VITE_ENABLE_DEV_AUTH?: string` declaration in `chatbot-widget/src/vite-env.d.ts`.
- Wired `enableDevAuth` across `DemoApp.tsx` -> `WorkspaceShell.tsx` -> `AdminSettingsModal.tsx` and `UserProfileModal.tsx` (library defaults fail closed to `enableDevAuth = false` in modals and `IS_DEV_ENV` in `WorkspaceShell`):
  - **When `enableDevAuth === false` (Production):**
    - `"Authorized Roles"` switcher tab, `"JWT Credentials"` tab, `"Paste Raw JWT Token"` textarea, and `"Manual Role Mapping"` chips are completely suppressed from `AdminSettingsModal`.
    - Modal title reads `"Admin Workspace Settings"` and opens directly to `"Knowledge Base Sync"`.
    - Sidebar/header developer terminal trigger and `"Settings & Roles"` button are hidden for non-admin users (`employee`, `hr`, `operations`, `it`, `finance`, `none`).
    - Unauthenticated header badge renders static `"Host Session Required"` instead of opening JWT paste controls.
    - `UserProfileModal` replaces `"Sign In (Load Role)"` with `"Sign in via host ERP portal"`.
    - `ContextualPanel.tsx` displays `"Authenticated Session"` instead of exposing `"Loaded (In-Memory HS256)"`.
- Verified both via unit tests (`tests/widget.test.ts`, 27 passed) and compiled TSX SSR checks via `esbuild` (`ADMIN_HAS_JWT_TAB: false`, `PROF_HAS_SIGNIN: false`, `PROF_HAS_HOST_NOTE: true`).

### 3.3 Backend Production Integration Options (Requires Product Owner Decision)

| Option | Architecture | Required Backend Changes | Required Host App / IdP Changes |
|---|---|---|---|
| **Option A: Host Portal Shared-Secret `HS256` (Lowest Friction)** | Main Fakhri 2.0 ERP portal authenticates user via its existing SSO/login, then mints short-lived (e.g., 15–60 min) `HS256` JWTs `{sub, role, exp}` via backend BFF for `ChatWidget.getToken`. | None in `app/auth/jwt.py` (enforce non-default strong `JWT_SECRET` in `app/config.py` at startup when `APP_ENV=production`). | Host ERP backend holds matching `JWT_SECRET` and injects/refreshes tokens seamlessly via `getToken()`. |
| **Option B: External IdP / OIDC `JWKS` (`RS256` / `ES256`)** | Browser obtains OIDC ID/Access Token from Microsoft Entra ID, Google Workspace, Auth0, or Keycloak. `chatbot-service` verifies signatures asynchronously against IdP `JWKS_URI`. | Add `PyJWKClient` verification in `app/auth/jwt.py`, enforce `iss`, `aud`, `exp`, algorithm allowlist (`["RS256", "ES256"]`), and claim-to-`ALLOWED_ROLES` mapping via env vars (`OIDC_JWKS_URL`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_ROLE_CLAIM`). | Configure OIDC app registration, RBAC group/role claims, and silent token refresh in frontend host shell. |

---

## 4. Blocker 3 — Session Management Architecture, Authorization & Migration Spec

> Per Phase 8.1 instructions, backend session CRUD endpoints were **not** pre-emptively implemented ahead of Product Owner schema & authorization sign-off.

### 4.1 Current Gap Analysis

- **Backend (`app/rag/session_manager.py`):** Already persists full conversation documents in MongoDB `chat_sessions` with `session_id`, `user_id`, `role`, `messages` (`role`, `content`, `sources`, `timestamp`), `summary`, `created_at`, `updated_at`, TTL cleanup (`session_ttl_hours=24`), IDOR ownership check `_verify_ownership`, and indexes `("session_id", 1), ("user_id", 1)` and `("user_id", 1), ("updated_at", -1)`. However, only `POST /chat` reads/writes sessions via `X-Session-ID`.
- **Frontend (`chatbot-widget/src/services/sessionStore.ts`):** Stores full `StoredConversation[]` arrays (including message content and role tags) in plaintext browser `localStorage` under `fakhri_chat_sessions_v1`, filtering client-side by `conv.role === activeRole`.
  - **Risks in Production:** Shared/kiosk workstations retain cached conversations in `localStorage` across browser reloads unless explicitly cleared on sign-out, title edits/pins/deletes do not propagate across devices, and browser storage (`~5MB`) overflows over heavy multi-month use.

### 4.2 Target REST API Contract & Security Invariants

| Endpoint | Purpose | Query / Request Schema | Response Schema & Limits |
|---|---|---|---|
| `GET /sessions` | List user's sessions for sidebar grouping | `limit: int = 20` (max `50`), `offset: int = 0`, optional `q: str` (max 100 chars) | `{ items: SessionSummary[], total: int, limit: int, offset: int }` (`session_id`, `title`, `pinned`, `created_at`, `updated_at`, `message_count`, `preview`) |
| `GET /sessions/{session_id}` | Hydrate full thread history into Area B canvas | Path param `session_id` (UUID format validated) | Full `messages[]`, `title`, `pinned`, `created_at`, `updated_at` (redacting internal metadata) |
| `PATCH /sessions/{session_id}` | Rename title or toggle pin | `{ title?: str (1..80 chars trimmed), pinned?: bool }` (Pydantic `extra="forbid"`) | Updated `SessionSummary` |
| `DELETE /sessions/{session_id}` | Delete user conversation | Path param `session_id` | HTTP `204 No Content` (soft delete `deleted_at` or atomic delete) |

**Mandatory Authorization & Security Controls:**
1. All four endpoints **must** require `user: AuthenticatedUser = Depends(get_current_user)`.
2. Every MongoDB query (`find`, `find_one`, `update_one`, `delete_one`) **must** scope strictly on **both** `{"user_id": user.user_id, "role": user.role}` (not `session_id` alone and not `user_id` alone). If a user's role changes between sessions (e.g., demotion/reassignment), cross-role threads must remain inaccessible (`404 Not Found` / `403 Forbidden`) rather than leaking stale privileged transcripts.
3. Add `(user_id, role, updated_at DESC)` compound index on `chat_sessions`, plus `pinned` and `title` fields (with server-derived default title from first user message capped at 80 chars).
4. Enforce `slowapi` rate limiting (e.g., `60/minute` per user) and input sanitization (`strip`, reject control characters on `title`).

### 4.3 `localStorage` Deprecation & Migration Plan

1. Replace persistent plaintext `localStorage` message caching (`fakhri_chat_sessions_v1`) with server-hydrated React state backed by `GET /sessions` + `GET /sessions/{id}`.
2. During initial migration rollout, treat any legacy `localStorage` entries strictly as ephemeral read-only fallback or clear `fakhri_chat_sessions_v1` explicitly on `onSignOut()`, expired JWT detection, or `sub`/`role` mismatch.
3. Never write bearer tokens or unredacted multi-tenant conversations into `localStorage`.

---

## 5. Blocker 4 — Message Feedback Persistence & UI Remediation

### 5.1 Root-Cause Finding

- Zero `/feedback` routes or MongoDB feedback collections exist in `app/`.
- Previously, `MessageItem.tsx` rendered clickable Thumbs Up (`Good response`) / Thumbs Down (`Bad response`) controls unconditionally via `ChatWidget.handleFeedback`, which only updated local React state and saved to `localStorage` without persisting anything to the backend.

### 5.2 Implemented Frontend Fix

- Made `onFeedback` optional on `MessageItemProps` (`MessageItem.tsx`) and `MessageListProps` (`MessageList.tsx`).
- Wrapped `.chat-feedback-group` inside `{onFeedback && (...)}` in `MessageItem.tsx`.
- Added optional `onPersistFeedback?: (messageId: string, type: "up" | "down" | null) => Promise<void> | void` to `ChatWidgetProps` (`src/types/chat.ts`) and wired `ChatWidget.tsx` to pass `onFeedback={onPersistFeedback ? handleFeedback : undefined}` in both embedded and floating modes.
- Result: No unpersisted/fake feedback controls appear anywhere in the default workspace (`MSG_HAS_FEEDBACK: false`), while `Copy` (`MSG_HAS_COPY: true`), `Regenerate`, Pagination Cards, Retry, and Source Citations remain intact.

### 5.3 Future `POST /feedback` Backend Specification

- **Endpoint:** `POST /feedback` (`Depends(get_current_user)`, `slowapi` rate-limited `30/minute`).
- **Request Payload (`Pydantic`, `extra="forbid"`):**
  - `session_id: UUID`
  - `message_id: str` (non-empty server-issued turn ID or bounded index key `<= 128` chars)
  - `rating: Literal["up", "down"] | null` (`null` clears previous rating)
  - `comment: str | None` (`max_length=500`)
- **Ownership & Authorization:** Verify session exists in `chat_sessions` matching `{session_id, user_id: user.user_id, role: user.role}` before accepting feedback (prevents forged feedback injection on another user's session).
- **Storage & Schema:** Idempotent `update_one(..., upsert=True)` into MongoDB `chat_feedback` collection keyed by unique compound index `(session_id, message_id, user_id)` storing `rating`, sanitized `comment`, `role`, `created_at`, `updated_at` (excluding raw LLM context payloads).

---

## 6. Blocker 5 — `scripts/verify_production.py` Root-Cause Investigation & Resolution

### 6.1 Initial Failure Analysis

Running `uv run python scripts/verify_production.py` initially yielded 4 passing checks (Checks 3–6) and 4 failing checks:

| Check ID & Query | Initial Output Summary | Verified Root Cause | Classification |
|---|---|---|---|
| **1. `"mere role kya h"`** | Answer returned `Full Name: ATUL BANGRE`, `Employee ID / Role: Super Admin (Platform Owner)`, `Designation: Graphics Designer`, `Department: IT and Development`. | Script asserted literal JWT `sub` string `"atul_bangre"` (with underscore), whereas the LLM formatted human display name **`ATUL BANGRE`** from `mongo:users`. | **Brittle Script Assertion** (not a RAG defect) |
| **2. `"mera name kya h"`** | Answer returned `"Aapka naam **ATUL BANGRE** hai... platform ke **Super Admin** hain!"`. | Script asserted literal hyphenated claim `"super-admin"`, failing against human-formatted prose **`Super Admin`**. | **Brittle Script Assertion** (not a RAG defect) |
| **7. `"how much its salary"`** | Returned `CLARIFICATION_PROMPT`: `"Could you please clarify what specific policy or module you are referring to?"` (`216.52ms`). | `"its"` is an orphan possessive pronoun with `history=[]` (whose salary is unspecified). Phase 5 `is_ambiguous_reference` intentionally intercepts it for clarification; script had stale pre-Phase-5 keyword expectation `["salary"]`. Testing `"what is employee salary structure"` cleanly returns salary confidentiality / non-disclosure (`"strictly confidential internal HR data"`). | **Outdated Script Expectation** (legitimate Phase 5 ambiguity guard preserved) |
| **8. `"what is kpi as per this application"`** | Returned `CLARIFICATION_PROMPT` (`208.18ms`) even though ChromaDB directly holds `mongo:kpidefinitions` (`"What is a KPI in this Application?"`). | `is_ambiguous_reference` checked `"this" in words` without checking if `"this"` acted as a **demonstrative determiner** modifying a following content noun (`"this application"`). | **Real Backend Defect in `app/rag/condense.py`** |

### 6.2 Implemented Fix & Regression Tests

1. **`app/rag/condense.py`:**
   - Added `_DEMONSTRATIVES = frozenset({"this", "that", "these", "those"})` and `_DEMONSTRATIVE_NON_NOUNS`.
   - Extracted typed helper functions `_has_unresolved_demonstrative` and `_has_true_reference_word` (keeping functions modular and under ~40 lines per `AGENTS.md`).
   - Resolves `"this application"`, `"this company"`, `"these guidelines"` cleanly as demonstrative determiners with explicit noun referents while **preserving strict clarification triggers** for standalone demonstratives (`"what is this?"`, `"what does this mean?"`, `"tell me about that"`), lowercase `"it"` (`"how does it work?"`), and orphan possessives (`"how much its salary"`).
2. **`tests/test_condense.py`:** Added `test_demonstrative_determiner_with_noun_not_ambiguous` and `test_standalone_demonstrative_and_possessive_pronouns_stay_ambiguous`.
3. **`scripts/verify_production.py`:**
   - Normalized `-` and `_` alongside `,` during keyword assertion comparisons (`norm_ans = ans.lower().replace(",", "").replace("-", " ").replace("_", " ")`) so human-formatted `"Super Admin"` and `"ATUL BANGRE"` satisfy JWT claim verification without weakening assertions.
   - Separated **Check 7 (`"7. Ambiguous Salary Pronoun Clarification"`)** expecting `["clarify", "referring"]` on `"how much its salary"` and **Check 8 (`"8. Salary Confidentiality / Non-Disclosure"`)** expecting `["salary"]` on `"what is employee salary structure"`, renumbering **Check 9 (`"9. KPI Definitions"`)** on `"what is kpi as per this application"`.

---

## 7. Complete Inventory of Files Changed

| File | Change Summary |
|---|---|
| [app/rag/condense.py](file:///f:/FKH_PROJECTS/chatbot-service/app/rag/condense.py) | Resolved demonstrative determiners (`this <noun>`, etc.) via `_has_unresolved_demonstrative` & `_has_true_reference_word` without weakening orphan pronoun guards. |
| [tests/test_condense.py](file:///f:/FKH_PROJECTS/chatbot-service/tests/test_condense.py) | Added regression tests for `"what is kpi as per this application"` vs `"how much its salary"`, `"what is this?"`, etc. |
| [scripts/verify_production.py](file:///f:/FKH_PROJECTS/chatbot-service/scripts/verify_production.py) | Normalized `-`/`_` delimiters for Check 1–2; preserved ambiguity check on Check 7 and separated salary non-disclosure Check 8 + KPI Check 9. |
| [chatbot-widget/src/vite-env.d.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/vite-env.d.ts) | Added optional `VITE_ENABLE_DEV_AUTH` env type definition. |
| [chatbot-widget/src/services/securityGate.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/services/securityGate.ts) | Created pure TS security & feature gating helpers (`resolveDevAuthEnabled`, `canOpenAdminWorkspaceSettings`, `shouldRenderFeedbackControls`). |
| [chatbot-widget/src/types/chat.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/types/chat.ts) | Added optional `onPersistFeedback` callback contract to `ChatWidgetProps`. |
| [chatbot-widget/src/components/MessageItem.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/MessageItem.tsx) | Made `onFeedback` optional and hid `.chat-feedback-group` when unset. |
| [chatbot-widget/src/components/MessageList.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/MessageList.tsx) | Made `onFeedback` optional in `MessageListProps`. |
| [chatbot-widget/src/components/ChatWidget.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/ChatWidget.tsx) | Gated feedback passing strictly behind `onPersistFeedback` in embedded & floating modes. |
| [chatbot-widget/src/components/workspace/AdminSettingsModal.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/AdminSettingsModal.tsx) | Added `enableDevAuth` (default `false`), hiding Authorized Roles & JWT Credentials tabs in production mode. |
| [chatbot-widget/src/components/workspace/UserProfileModal.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/UserProfileModal.tsx) | Added `enableDevAuth` (default `false`), hiding dev `"Sign In (Load Role)"` CTA and non-admin settings trigger. |
| [chatbot-widget/src/components/workspace/WorkspaceShell.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/WorkspaceShell.tsx) | Gated dev JWT triggers, admin settings access, and `onOpenDevTools` behind `enableDevAuth` & `canOpenAdminSettings`. |
| [chatbot-widget/src/components/workspace/ContextualPanel.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/ContextualPanel.tsx) | Updated token indicator wording from `"Loaded (In-Memory HS256)"` to `"Authenticated Session"`. |
| [chatbot-widget/src/demo/DemoApp.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/demo/DemoApp.tsx) | Computed `enableDevAuth = resolveDevAuthEnabled(Boolean(import.meta.env.DEV), import.meta.env.VITE_ENABLE_DEV_AUTH)` and passed it to `WorkspaceShell`. |
| [chatbot-widget/tests/widget.test.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/tests/widget.test.ts) | Added Blocker 2 and Blocker 4 unit tests covering `securityGate.ts`. |

---

## 8. Observed Test, Build & Verification Results

All checks below ran cleanly after the final code edit to their respective layers:

1. **Backend Unit & Integration Suite (`uv run pytest -q`):**
   - Result: **`400 passed, 27 skipped, 1 warning in 49.34s`** (skipped tests correspond to optional external/disabled flags).
2. **Production Verification Checklist (`$env:PYTHONPATH = "."; uv run python scripts/verify_production.py`):**
   - Result: **`ALL 9 PRODUCTION DATA CHECKS PASSED — 100% READY FOR DEMO`**
     - `[PASS] 1. User Identity (Role)`
     - `[PASS] 2. User Identity (Name)`
     - `[PASS] 3. Departments Count & List`
     - `[PASS] 4. Active Subscriptions`
     - `[PASS] 5. Client Junaid Profile`
     - `[PASS] 6. Ali Asgar Profile`
     - `[PASS] 7. Ambiguous Salary Pronoun Clarification`
     - `[PASS] 8. Salary Confidentiality / Non-Disclosure`
     - `[PASS] 9. KPI Definitions` (`sources: ["mongo:kpidefinitions", ...]`, zero grounding warnings)
3. **Frontend Unit Tests (`npm test` inside `chatbot-widget/`):**
   - Result: **`27 passed, 0 failed`** (`node --test tests/widget.test.ts`, `1088.88ms`).
4. **Frontend TypeScript Check & Production Vite Build (`npm run build` inside `chatbot-widget/`):**
   - Result: `tsc && vite build` succeeded with 0 errors (`302 modules transformed`, `dist/assets/index-bppAAxsY.css` `69.91 kB`, `dist/assets/index-CnrDPTjw.js` `396.91 kB`, `4.64s`).
5. **Compiled TSX SSR Render Gate Check (via Vite's `esbuild` + `react-dom/server`):**
   - `MSG_HAS_FEEDBACK: false`, `MSG_HAS_COPY: true`
   - `ADMIN_HAS_JWT_TAB: false`, `ADMIN_HAS_SYNC: true`
   - `PROF_HAS_SIGNIN: false`, `PROF_HAS_HOST_NOTE: true`

---

## 9. Remaining Open Blockers & Product Owner Decisions Required

| Item | Open Blocker / Question | Impact on Launch |
|---|---|---|
| **Auth Integration Mode** | Confirm Option A (Host ERP BFF minting shared-secret `HS256` JWTs) vs Option B (External IdP `OIDC`/`JWKS` asymmetric validation in `app/auth/jwt.py`). | Required before production multi-user sign-in works end-to-end. |
| **Session Persistence API** | Approve MongoDB `chat_sessions` REST spec (`GET /sessions`, `GET /sessions/{id}`, `PATCH /sessions/{id}`, `DELETE /sessions/{id}`) and retirement of `localStorage` message caching. | Required for cross-device history & enterprise security on shared browsers. |
| **Feedback Persistence API** | Decide whether to implement `POST /feedback` + `chat_feedback` schema (and wire `onPersistFeedback`) before launch or keep feedback hidden. | UI cleanly hidden today; non-blocking if deferred cleanly. |
| **Live Browser Visual QA** | Run manual or Playwright visual/layout QA checklist (Section 2.2) across desktop/tablet/mobile against isolated Chrome (`--user-data-dir`) and `vite preview`. | Mandatory pre-launch sign-off since MCP Chrome harness lacked `DevToolsActivePort`. |

---

## 10. Production Deployment Prerequisites Checklist

- Set `APP_ENV=production` and generate a cryptographically strong `JWT_SECRET` (never leave default dev secrets).
- Restrict `CORS_ORIGINS` strictly to approved production host origins (disable wildcard origins).
- Configure frontend build env `VITE_API_URL` to the TLS-terminated HTTPS backend URL and ensure `VITE_ENABLE_DEV_AUTH` is unset or `"false"`.
- Terminate TLS (`HTTPS`) at the ingress/reverse proxy supporting unbuffered HTTP/1.1 streaming for SSE (`POST /chat`, `proxy_buffering off` in Nginx).
- Ensure persistent mounted storage for ChromaDB (`CHROMA_PERSIST_DIR`) and network-restricted MongoDB Atlas credentials.

---

## 11. Updated Production Readiness Classification

**Status: `CONDITIONAL / STAGING-READY — REMEDIATION VERIFIED; NOT YET CLEARED FOR PRODUCTION LAUNCH`**

Phase 8.1 eliminated all unverified UI claims (non-persisted feedback controls), gated developer JWT/persona-switching tooling cleanly out of production frontend bundles, resolved the `this <noun>` condensation bug causing KPI failures, and verified all 9 checks in `scripts/verify_production.py` alongside `400` passing backend tests and `27` passing widget tests (`302` modules built with 0 TS errors). Full production sign-off remains gated strictly on: (1) Product Owner selection & backend implementation of host authentication (Option A vs Option B) and Session REST APIs, and (2) human or Playwright visual browser QA across responsive viewports.
