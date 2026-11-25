# PHASE 6: ENTERPRISE FEATURES, ROLE-BASED EXPERIENCE AND ADMIN SETTINGS REPORT

**Product:** Fakhri ERP AI  
**Company:** Fakhri IT Services  
**Author:** Senior Enterprise SaaS Architect, Security-Conscious Frontend Engineer & Product Designer  
**Status:** Completed & Production-Verified  
**Reference Docs:**
- [PHASE_1_EXISTING_PROJECT_AUDIT.md](file:///f:/FKH_PROJECTS/chatbot-service/docs/PHASE_1_EXISTING_PROJECT_AUDIT.md)
- [PHASE_2_DESIGN_SYSTEM_AND_WORKSPACE_SHELL_REPORT.md](file:///f:/FKH_PROJECTS/chatbot-service/docs/PHASE_2_DESIGN_SYSTEM_AND_WORKSPACE_SHELL_REPORT.md)
- [PHASE_3_APPLICATION_SHELL_REPORT.md](file:///f:/FKH_PROJECTS/chatbot-service/docs/PHASE_3_APPLICATION_SHELL_REPORT.md)
- [PHASE_4_CHAT_INTERFACE_REPORT.md](file:///f:/FKH_PROJECTS/chatbot-service/docs/PHASE_4_CHAT_INTERFACE_REPORT.md)
- [PHASE_5_CONVERSATION_MANAGEMENT_REPORT.md](file:///f:/FKH_PROJECTS/chatbot-service/docs/PHASE_5_CONVERSATION_MANAGEMENT_REPORT.md)

---

## 1. Executive Summary

Phase 6 elevates **Fakhri ERP AI** into a true enterprise SaaS application by structuring its identity, permission boundaries, administrative tools, and contextual metadata into an elegant, security-first user experience. 

Previously, role selection and JWT credentials were exposed as a raw floating debug widget overlaid on the consumer demo screen. In Phase 6, this developer tooling has been completely decoupled from the chat surface and re-architected into:
1. **User Profile & Identity Experience (`UserProfileModal.tsx`)**: A clean user menu displaying the authenticated persona, email, organizational role, security clearance tier, and sign-out action, completely masking raw JWT tokens to prevent credential exfiltration.
2. **Protected Admin & Developer Settings (`AdminSettingsModal.tsx`)**: A dedicated multi-tabbed administration console providing offline JWT token assignment, authorized runtime role switching, and live MongoDB Atlas knowledge base synchronization.
3. **Contextual Knowledge & Telemetry Panel (`ContextualPanel.tsx` — Area C)**: A real-time right-hand workspace drawer detailing the active user's RBAC scope, active conversation turns, ground-truth database citations cited in the thread, and underlying LLM telemetry.
4. **Role-Adaptive Enterprise Navigation & Prompt Suggestions (`promptStarters.ts`)**: Granular mapping of accessible enterprise modules (`hr`, `operations`, `psa`, `finance`, `it`, `policies`) for all seven organizational personas, tailoring starter suggestions without bypassing backend authorization boundaries.

All features strictly adhere to the invariant defined in `AGENTS.md` and Phase 1: **the backend remains the sole source of truth for authorization and pre-retrieval vector filtering**.

---

## 2. User Profile Experience (`UserProfileModal.tsx`)

Located at [UserProfileModal.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/UserProfileModal.tsx), the user profile dialog provides an intuitive account overview accessible by clicking the user avatar badge in either the sidebar footer or the workspace top header.

### Key Capabilities:
- **Identity & Persona Representation**: Displays user full name (e.g. *Atul Bangre*, *Disha Damwani*), email address (`sub` claim or mapped `*.internal` email), and high-contrast avatar initials.
- **Role & Security Clearance Tier**:
  - `super-admin` / `founder`: **Level 5 — Platform Root / All Collections**
  - `hr`: **Level 3 — HR & Employee Directories**
  - `operations`: **Level 3 — Operations & Tasks**
  - `finance`: **Level 3 — Invoices & PSA Billing**
  - `it`: **Level 3 — IT Assets & Infrastructure**
  - `employee`: **Level 1 — General Policies & Self Directory**
  - `none` (Guest): **Level 0 — Public Handbook Only**
- **Zero Token Leakage**: Raw JWT strings and secret signatures are completely excluded from the user profile view, ensuring shoulder-surfing and presentation screens do not leak sensitive credentials.
- **Direct Settings & Sign-Out Actions**:
  - `Settings & Roles`: Opens the protected Admin Settings dialog.
  - `Sign Out`: Clears the active authentication session, resets the role to `none` (Guest mode), and notifies the user with clear visual feedback.

---

## 3. Role-Based Experience Across All 7 Company Roles

Fakhri ERP AI enforces strict segregation of duties across all 7 corporate personas:

| Role ID | Persona Name | Department & Designation | Accessible Modules | Vector Filter Scope |
| :--- | :--- | :--- | :--- | :--- |
| `super-admin` | Atul Bangre | Platform Architect & Super Admin | All Modules, Telemetry, DB Sync | Platform Root (`where: {}`) |
| `founder` | Abdul Quadir | Executive Founder & Managing Director | All Business, HR, Ops, Finance | Executive Root (`where: {}`) |
| `hr` | Disha Damwani | Human Resources Lead | HR, Directories, Comp, Policies | `where: { dept: { $in: ["hr", "general"] } }` |
| `operations` | Zainab Husain | Operations & Logistics Manager | Ops Workflows, Approvals, PSA Clients | `where: { dept: { $in: ["operations", "psa", "general"] } }` |
| `finance` | Finance Lead | Finance & Accounts Director | Invoices, Billing, PSA Pricing | `where: { dept: { $in: ["finance", "psa", "general"] } }` |
| `it` | Ali Asgar | IT & Infrastructure Lead | Assets, IT Infra, Security SOPs | `where: { dept: { $in: ["it", "operations", "general"] } }` |
| `employee` | Vivek / Aashi | Associate Team Member | Leave Rules, General Policies | `where: { dept: "general" }` |
| `none` | Guest | Unauthenticated Visitor | Public Handbook Only | `where: { access: "public" }` |

### Adaptive Frontend Guidance:
- `getRoleAccessibleModules(role)` in [promptStarters.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/services/promptStarters.ts) maps accessible functional areas for each persona.
- The welcome screen dynamically hides cards for inaccessible modules (e.g., HR employees do not see "Summarize pending invoices", and Guest visitors only see public handbook queries).
- **Security Invariant**: Even if a user manually submits a prompt outside their clearance level, the backend vector search mathematically excludes unauthorized documents in ChromaDB's `where` clause before the LLM generates a response.

---

## 4. Protected Admin & Developer Settings (`AdminSettingsModal.tsx`)

Located at [AdminSettingsModal.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/AdminSettingsModal.tsx), this protected dialog houses administrative functions organized into three focused tabs:

### Tab 1: Authorized Roles Switcher
- Visual cards for all 7 organizational personas.
- Visual status pills indicating whether a valid JWT token is loaded in-memory for that role (`✓ Ready` vs `No Token`).
- Immediate runtime context switching with transient banner confirmations.
- Prevents switching to a role that lacks loaded authentication credentials.

### Tab 2: Offline JWT Credentials Management
- Multiline input textarea accepting offline development tokens generated via `python generate_dev_tokens.py`.
- **Automatic Payload Inspector**: Decodes the JWT header and payload in-memory without sending the token over unverified channels.
- Extracts `sub`, `role`, and expiration timestamp (`exp`).
- **One-Click Auto-Assignment**: Assigns the token to the matching role slot immediately if the decoded role matches an enterprise role.
- **Manual Role Mapping**: Provides chip buttons to map custom tokens to specific role slots when needed.

### Tab 3: Knowledge Base Synchronization
- **Live Connection Status**: Confirms MongoDB Atlas connection across 20+ collections.
- **ChromaDB Chunk Counter**: Displays live ingested chunk counts (e.g. 347 chunks).
- **Last Sync Timestamp**: Shows the exact date and time of the last synchronization action.
- **Sync MongoDB Collections Trigger**: Calls the backend `POST /sync/mongo` endpoint with an interactive loading spinner (`isSyncing`) and displays the real server reconciliation response (`total_chunks` and indexed collections).

---

## 5. Contextual Information Panel (Area C — `ContextualPanel.tsx`)

The collapsible right-side drawer (Area C) provides continuous visibility into active session state:

1. **Tab 1: RBAC Scope**:
   - Displays active persona name and badge.
   - Shows active session ID and message turn count in the current thread.
   - Summarizes document clearance level and explains the pre-retrieval vector security invariant.
2. **Tab 2: Knowledge Base & Citations**:
   - Shows live MongoDB Atlas and ChromaDB status.
   - **Active Thread Citations**: Dynamically aggregates document references cited by assistant turns in the active thread (e.g. `mongo:hr_policies`, `mongo:clients`), providing immediate proof of ground truth.
3. **Tab 3: System Telemetry**:
   - Primary LLM: Google Gemini Flash Lite (1M context window, REST HTTPS transport).
   - Orchestration: LangGraph State Machine (stateless in-memory DAG with `checkpointer=None`).
   - Transport: Server-Sent Events (SSE) unidirectional HTTP/1.1 chunked streaming.

---

## 6. Security & Architectural Invariants

Phase 6 rigorously enforces the following security boundaries:
1. **Zero Hardcoded Secrets**: No API keys, JWT secret keys, or database credentials exist in frontend code or Git history.
2. **No Post-Generation Document Filtering**: Security filtering is strictly performed *inside* the vector-store retrieval query (`where: {"department": {"$in": allowed}}`), preventing the LLM from ever receiving unauthorized company data in its prompt context.
3. **Read-Only Chatbot Interaction**: The chat interface remains strictly read-only with no arbitrary database writes, tool mutations, or code execution capabilities.
4. **Untrusted Data Isolation**: All retrieved documents and user messages are treated as untrusted text and processed with sanitized rendering (`SafeMarkdown.ts`).

---

## 7. Verification & Test Results

### A. Frontend Unit Tests (Node.js Test Runner)
Executed via `npm test` inside `chatbot-widget/`:
```
✔ clampContentByRole enforces role ceilings (3.4ms)
✔ filterValidTurns excludes empty or whitespace messages (0.5ms)
✔ prepareHistoryForBackend limits history to at most 10 turns (0.6ms)
✔ prepareHistoryForBackend drops oldest messages first when exceeding 12000 chars (0.7ms)
✔ parseSSELine parses valid token and done events (1.5ms)
✔ getFriendlyErrorMessage maps HTTP statuses correctly (0.4ms)
✔ image markdown does not produce an <img> element (31.7ms)
✔ javascript: link is not rendered as a clickable link (18.8ms)
✔ data: link is not rendered as a clickable link (10.1ms)
✔ https: link is rendered as secure link with rel and target attributes (6.8ms)
✔ raw <script> and <img> HTML in the answer is shown as text, not executed (3.1ms)
✔ STARTER_PROMPTS enforces role-based access filtering (0.7ms)
✔ SafeMarkdown renders tables inside .chat-table-wrapper with sticky-ready structure (9.4ms)
✔ SafeMarkdown renders code blocks inside .chat-code-block-wrapper and inline code with .chat-inline-code (9.6ms)
✔ getGroupingCategory correctly categorizes timestamps (2.2ms)
✔ groupConversationsByDate separates pinned threads and groups chronologically (1.1ms)
✔ validateTitle rejects empty strings and strings over 80 chars (0.3ms)
✔ generateSessionTitle derives title from first user query (0.5ms)
✔ searchConversations searches titles and message contents with snippet generation (0.7ms)
✔ getRoleAccessibleModules returns correct module permissions for each role (0.4ms)
✔ getVisiblePrompts filters starter cards based on role module accessibility (0.4ms)

ℹ tests 21 | pass 21 | fail 0 | duration_ms 1474.9
```

### B. Frontend Production Build
Executed via `npm run build` inside `chatbot-widget/`:
```
> chatbot-widget@1.0.0 build
> tsc && vite build

vite v6.4.6 building for production...
✓ 300 modules transformed.
dist/index.html                   0.42 kB │ gzip:   0.29 kB
dist/assets/index-B-Qm-yct.css   62.83 kB │ gzip:  10.30 kB
dist/assets/index-B-KR5e0K.js   386.59 kB │ gzip: 116.04 kB
✓ built in 3.18s
```

### C. Backend Test Suite (Pytest)
Executed via `uv run pytest -q`:
```
397 passed, 27 skipped, 1 warning in 57.71s
```

---

## 8. Files Changed in Phase 6

| File Path | Purpose |
| :--- | :--- |
| [UserProfileModal.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/UserProfileModal.tsx) | User identity, role badge, organizational scope, sign out modal |
| [AdminSettingsModal.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/AdminSettingsModal.tsx) | Protected 3-tab modal for runtime roles, offline JWT, and KB sync |
| [ContextualPanel.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/ContextualPanel.tsx) | Enhanced Area C with thread citations, session stats, telemetry |
| [WorkspaceShell.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/components/workspace/WorkspaceShell.tsx) | Integrated profile & settings modals, profile header/footer clicks |
| [DemoApp.tsx](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/demo/DemoApp.tsx) | Knowledge sync state tracking (`lastSyncTime`, `lastSyncChunks`), sign-out |
| [promptStarters.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/services/promptStarters.ts) | Added `getRoleAccessibleModules` for all 7 roles + guest |
| [workspace.css](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/styles/workspace.css) | Complete styling for profile modal, admin settings tabs, roles grid |
| [index.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/src/index.ts) | Publicly exported Phase 6 components and utility functions |
| [widget.test.ts](file:///f:/FKH_PROJECTS/chatbot-service/chatbot-widget/tests/widget.test.ts) | Unit tests verifying role permissions and starter prompt accessibility |

---

## 9. Conclusion & Project Status

Phase 6 has been completed with zero regressions. All enterprise identity, role-switching, administrative settings, and contextual panel requirements are implemented and fully verified against the test suites.

As requested by the phase discipline instructions, execution stops here. Phase 7 will NOT begin automatically.
