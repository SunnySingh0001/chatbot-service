# Project Brief: Internal Employee Chatbot (v1)

## Goal
A **standalone internal employee chatbot** that answers questions from company documents,
returns source citations, and enforces role-based access — using dummy data only for v1.

---

## v1 Scope

| Feature | Detail |
|---|---|
| RAG pipeline | Retrieve → augment → generate over Markdown company docs |
| Citations | Every answer lists the source file names used |
| Fallback | "I don't know" when context is insufficient or similarity < threshold |
| Auth | JWT with roles: `employee`, `hr` |
| Role filtering | Enforced at retrieval query time — not post-LLM |
| Rate limiting | Per authenticated user |
| Logging | Structured request/response logging |

## Out of Scope (v1)
- UI beyond a minimal test widget
- Actions, tools, or any database writes (read-only bot)
- Admin panel
- Production deployment / CI/CD

---

## Bot Behaviour
- Answer **only** from retrieved document context.
- If context is insufficient **or** best similarity score < `RAG_SCORE_THRESHOLD`:
  → reply "I don't have enough information. Please contact HR or IT."
- Reply in the **user's language**: English, Hindi, or Hinglish.
- Keep answers under **6 lines**.
- **Always** list source file names at the end of every answer.

---

## API (v1)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/chat` | JWT Bearer | `{message, history[]}` → SSE streamed answer + sources |
| `GET` | `/health` | None | Service health check |

---

## Document Chunk Metadata

Each chunk stored in the vector DB carries these metadata fields:

```json
{
  "source":       "employee_handbook.md",
  "department":   "general",
  "access_level": "all"
}
```

`access_level` values: `"all"` | `"hr"` | `"it"`

Role → allowed access levels:
- `employee` → `["all"]`
- `hr`       → `["all", "hr"]`
- `it`       → `["all", "it"]`

---

## Directory Layout

```
chatbot-service/
├── app/
│   ├── config.py          # All env-based settings
│   ├── auth/              # JWT decode, CurrentUser model (Phase 4)
│   ├── rag/
│   │   ├── embedder.py    # Embedding function (sentence-transformers)
│   │   ├── ingest.py      # Doc → chunk → embed → ChromaDB
│   │   ├── retriever.py   # Top-k search + permission filter
│   │   ├── prompt.py      # System prompt builder
│   │   ├── llm.py         # LLM gateway (Groq default, swappable)
│   │   └── cli.py         # Dev CLI: python -m app.rag.cli "q" --role hr
│   ├── api/               # FastAPI routers (Phase 5)
│   └── middleware/        # Rate limit, logging (Phase 6)
├── data/docs/             # Dummy Markdown docs with YAML frontmatter
├── tests/
├── .env.example
└── requirements.txt
```

---

## Phases

| Phase | Scope |
|---|---|
| **1** | RAG core: ingest, retriever, prompt, llm, CLI |
| **2** | FastAPI scaffold + `/health` |
| **3** | JWT auth + role injection |
| **4** | `POST /chat` with SSE streaming |
| **5** | Rate limiting + structured logging |
| **6** | Tests |

