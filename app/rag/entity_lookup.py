"""Read-only 360-degree cross-collection MongoDB entity lookup for complete profile and history hydration."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any  # Pymongo documents are dynamic BSON mappings

from bson import ObjectId
from pymongo import MongoClient, ReadPreference
from pymongo.database import Database

from app.config import settings
from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.mongo_serializer import is_sensitive_field, normalize_bson_value
from app.rag.retriever import ROLE_ACCESS, RetrievedChunk

_logger = logging.getLogger("chatbot.rag.entity_lookup")

_CLIENT_CACHE: MongoClient[dict[str, Any]] | None = None
_CATALOG_TTL_SECONDS: float = 300.0
_MONGO_CIRCUIT_OPEN_UNTIL: float = 0.0
_MAX_DETAIL_ROWS: int = 25

_CODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("task", re.compile(r"\b(TSK-\d+)\b", re.IGNORECASE)),
    ("invoice", re.compile(r"\b(FISIPL[A-Z0-9]{4,})\b", re.IGNORECASE)),
    ("lead", re.compile(r"\b(LEAD-\d+)\b", re.IGNORECASE)),
    ("ticket", re.compile(r"\b(TCK-\d{4}-\d+)\b", re.IGNORECASE)),
    ("employee_id", re.compile(r"\b(EMP-\d+)\b", re.IGNORECASE)),
)
_EMAIL_RE: re.Pattern[str] = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE: re.Pattern[str] = re.compile(r"(?:\+?91[\s-]?)?\b([6-9]\d{9})\b")
_OBJECT_ID_RE: re.Pattern[str] = re.compile(r"\b([0-9a-f]{24})\b", re.IGNORECASE)
_HTML_TAG_RE: re.Pattern[str] = re.compile(r"<[^>]+>")

_PRONOUN_REF_TOKENS: frozenset[str] = frozenset({
    "iska", "iske", "iski", "isko", "isme", "inhe", "inka", "inke", "inki",
    "uska", "uske", "uski", "usko", "usme", "unhe", "unka", "unke", "unki",
    "his", "her", "their", "them", "its", "this", "that",
})

_PLURAL_FILTER_HINTS: tuple[str, ...] = (
    "plan ke clients", "plan clients", "wale clients", "sabhi clients", "saare clients",
    "all clients", "complete client list", "client directory", "sabhi employees",
    "all employees", "40 employees", "pending tasks list", "all tasks", "all invoices",
    "all leads", "leave policy", "vpn software", "kitne clients", "kitne employees",
    "how many clients", "how many employees", "total clients", "total employees",
)

_IGNORED_ENTITY_NAMES: frozenset[str] = frozenset({
    "admin", "super admin", "super-admin", "client", "clients", "user", "users",
    "employee", "employees", "staff", "manager", "supervisor", "owner", "lead", "leads",
    "task", "tasks", "invoice", "invoices", "ticket", "tickets", "team", "teams",
    "sales", "dmax", "feta", "general", "operations", "finance", "support", "hr", "it",
    "amazon", "flipkart", "meesho", "shopify", "nykaa", "myntra", "jiomart",
    "free", "basic", "elite", "premium", "kickstart", "growth", "amazon kickstart",
    "plan", "company", "fakhri", "fakhri it", "fakhri it services", "fakhri management",
    "management", "na", "n/a", "none", "null", "unknown", "test", "demo", "testing",
    "my collection", "india", "bhopal", "support ongoing", "new lead",
})


@dataclass
class _EntityCatalog:
    """In-memory index of entity identifiers and display names for fast query detection."""

    built_at: float = 0.0
    user_names: dict[str, list[ObjectId]] = field(default_factory=dict)
    company_names: dict[str, list[ObjectId]] = field(default_factory=dict)
    lead_names: dict[str, list[ObjectId]] = field(default_factory=dict)
    team_names: dict[str, ObjectId] = field(default_factory=dict)
    user_lookup: dict[ObjectId, dict[str, str]] = field(default_factory=dict)


_CATALOG_CACHE: dict[str, _EntityCatalog] = {}


def invalidate_entity_cache() -> None:
    """Clears cached MongoDB entity lookup index and resets circuit breaker."""
    global _MONGO_CIRCUIT_OPEN_UNTIL  # noqa: PLW0603
    _CATALOG_CACHE.clear()
    _MONGO_CIRCUIT_OPEN_UNTIL = 0.0


def _tquery(
    base: dict[str, Any],
    col: str,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any] | None:
    """Wraps a MongoDB filter with verified company_id and client_id isolation predicates."""
    from app.rag.entity_pagination import build_tenant_mongo_filter  # noqa: PLC0415

    return build_tenant_mongo_filter(
        base, role=role, client_id=client_id, company_id=company_id, collection_name=col
    )


def _tfind(
    db: Database[dict[str, Any]],
    col: str,
    base: dict[str, Any],
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    projection: dict[str, int] | None = None,
    sort_spec: tuple[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Executes tenant-and-client-scoped MongoDB find returning empty list when blocked."""
    filt = _tquery(base, col, role, client_id=client_id, company_id=company_id)
    if filt is None:
        return []
    cur = db[col].find(filt, projection) if projection is not None else db[col].find(filt)
    if sort_spec is not None:
        cur = cur.sort(sort_spec[0], sort_spec[1])
    return list(cur)


def _tfind_one(
    db: Database[dict[str, Any]],
    col: str,
    base: dict[str, Any],
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any] | None:
    """Executes tenant-and-client-scoped MongoDB find_one returning None when blocked."""
    filt = _tquery(base, col, role, client_id=client_id, company_id=company_id)
    return db[col].find_one(filt) if filt is not None else None


def _trip_mongo_circuit(seconds: float = 60.0) -> None:
    """Temporarily skips MongoDB lookups after network/selection timeout to protect latency."""
    global _MONGO_CIRCUIT_OPEN_UNTIL  # noqa: PLW0603
    _MONGO_CIRCUIT_OPEN_UNTIL = time.monotonic() + seconds


def _get_read_db() -> Database[dict[str, Any]] | None:
    """Returns read-only SecondaryPreferred MongoDB handle, or None when unavailable."""
    global _CLIENT_CACHE  # noqa: PLW0603
    if not settings.mongodb_uri or time.monotonic() < _MONGO_CIRCUIT_OPEN_UNTIL:
        return None
    try:
        if _CLIENT_CACHE is None:
            _CLIENT_CACHE = MongoClient(
                settings.mongodb_uri,
                serverSelectionTimeoutMS=2500,
                connectTimeoutMS=2500,
                read_preference=ReadPreference.SECONDARY_PREFERRED,
            )
        return _CLIENT_CACHE[settings.mongodb_db_name]
    except Exception as exc:
        _logger.debug("MongoDB connection skipped (%s)", type(exc).__name__)
        _trip_mongo_circuit()
        return None


def _can_read_collection(col_name: str, role: str) -> bool:
    """Checks approved collection governance and role-level RBAC access."""
    spec = APPROVED_COLLECTIONS.get(col_name)
    allowed = ROLE_ACCESS.get(role.lower(), [])
    return spec is not None and spec.access_level in allowed


def _strip_html(val: object) -> str:
    """Converts HTML/whitespace values into compact readable plain text."""
    raw = str(normalize_bson_value(val) or "")
    text = _HTML_TAG_RE.sub(" ", raw).replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def _fmt_dt(val: object) -> str:
    """Formats BSON dates/timestamps compactly for LLM consumption."""
    norm = normalize_bson_value(val)
    if norm is None or norm == "":
        return "N/A"
    s = str(norm)
    return s[:19].replace("T", " ") if len(s) >= 19 and s[10:11] == "T" else s


def _normalize_label(text: str) -> str:
    """Normalizes entity display labels for boundary-aware exact matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _is_indexable_label(label: str) -> bool:
    """Filters out short, numeric, or generic stopwords from entity catalog keys."""
    if not label or len(label) < 4 or label in _IGNORED_ENTITY_NAMES or label.isdigit():
        return False
    return len(label.split()) >= 2 or len(label) >= 6


def _add_name_key(mapping: dict[str, list[ObjectId]], label: str, oid: ObjectId) -> None:
    """Indexes a normalized entity name cleanly into a multi-value mapping."""
    key = _normalize_label(label)
    if _is_indexable_label(key):
        bucket = mapping.setdefault(key, [])
        if oid not in bucket:
            bucket.append(oid)


def _index_users_into_catalog(
    db: Database[dict[str, Any]],
    cat: _EntityCatalog,
    company_id: str | None = None,
) -> None:
    """Populates user lookup metadata, user names, and company keys from tenant-scoped users."""
    proj = {"name": 1, "company": 1, "role": 1, "employeeId": 1, "employeeCode": 1, "email": 1}
    uq = _tquery({}, "users", "admin", company_id=company_id)
    for u in db["users"].find(uq, proj):
        uid = u["_id"]
        role_str = str(u.get("role", "employee"))
        emp_code = u.get("employeeCode", "")
        emp_id = str(u.get("employeeId") or ("Super Admin" if role_str == "super-admin" else (f"EMP-{emp_code}" if emp_code else "")))
        name = str(u.get("name", "")).strip()
        company = str(u.get("company", "")).strip()
        cat.user_lookup[uid] = {
            "name": name or "Unknown",
            "role": role_str,
            "empId": emp_id,
            "email": str(u.get("email", "")).strip(),
            "company": company,
        }
        _add_name_key(cat.user_names, name, uid)
        if company:
            _add_name_key(cat.company_names, company, uid)


def _build_entity_catalog(
    db: Database[dict[str, Any]],
    company_id: str | None = None,
) -> _EntityCatalog:
    """Builds lightweight in-memory entity name and user lookup maps from MongoDB."""
    cat = _EntityCatalog(built_at=time.monotonic())
    _index_users_into_catalog(db, cat, company_id=company_id)
    for cc in db["clientcompanies"].find(_tquery({}, "clientcompanies", "admin", company_id=company_id), {"clientId": 1, "name": 1}):
        if cc.get("clientId") and cc.get("name"):
            _add_name_key(cat.company_names, str(cc["name"]), cc["clientId"])
    for ld in db["leads"].find(_tquery({}, "leads", "admin", company_id=company_id), {"name": 1, "company": 1}):
        lid = ld["_id"]
        _add_name_key(cat.lead_names, str(ld.get("name", "")), lid)
        if ld.get("company"):
            _add_name_key(cat.lead_names, str(ld["company"]), lid)
    for tm in db["teams"].find(_tquery({}, "teams", "admin", company_id=company_id), {"name": 1, "code": 1, "slug": 1}):
        for field_name in ("name", "code", "slug"):
            val = _normalize_label(str(tm.get(field_name, "")))
            if val and len(val) >= 4 and val not in _IGNORED_ENTITY_NAMES:
                cat.team_names[val] = tm["_id"]
    return cat


def _get_entity_catalog(
    db: Database[dict[str, Any]],
    company_id: str | None = None,
) -> _EntityCatalog | None:
    """Returns tenant-scoped cached entity index, refreshing cleanly when TTL expires."""
    cache_key = company_id or "__all__"
    now = time.monotonic()
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None and (now - cached.built_at) < _CATALOG_TTL_SECONDS:
        return cached
    try:
        built = _build_entity_catalog(db, company_id=company_id)
        _CATALOG_CACHE[cache_key] = built
        return built
    except Exception as exc:
        _logger.debug("Failed to build entity catalog (%s)", type(exc).__name__)
        _trip_mongo_circuit()
        return cached


def _make_chunk(col_name: str, lines: list[str], chunk_index: int = 0) -> RetrievedChunk | None:
    """Builds an RBAC-tagged RetrievedChunk for an approved MongoDB collection."""
    spec = APPROVED_COLLECTIONS.get(col_name)
    body = "\n".join(lines).strip()
    if spec is None or not body:
        return None
    return RetrievedChunk(
        text=body,
        source=f"mongo:{col_name}",
        department=spec.department,
        access_level=spec.access_level,
        score=1.0,
        chunk_index=chunk_index,
    )


def _detect_explicit_target(query: str) -> tuple[str, str] | None:
    """Extracts explicit business IDs, emails, phones, or ObjectIds from query."""
    for kind, pat in _CODE_PATTERNS:
        m = pat.search(query)
        if m:
            return (kind, m.group(1).upper())
    em = _EMAIL_RE.search(query)
    if em:
        return ("email", em.group(1).lower())
    ph = _PHONE_RE.search(query)
    if ph:
        return ("phone", ph.group(1))
    oid = _OBJECT_ID_RE.search(query)
    if oid:
        return ("object_id", oid.group(1).lower())
    return None


def _find_best_label_match(q_lower: str, labels: list[str]) -> str | None:
    """Finds the longest word-bounded label matching cleanly inside the normalized query."""
    padded = f" {re.sub(r'[^a-z0-9@._-]+', ' ', q_lower)} "
    for label in sorted(labels, key=len, reverse=True):
        clean_lbl = re.sub(r"[^a-z0-9@._-]+", " ", label).strip()
        if clean_lbl and f" {clean_lbl} " in padded:
            return label
    return None


def _detect_named_target(query: str, catalog: _EntityCatalog) -> tuple[str, list[ObjectId]] | None:
    """Matches an unambiguous client, company, employee, team, or lead name in query."""
    q_lower = _normalize_label(query)
    if any(hint in q_lower for hint in _PLURAL_FILTER_HINTS):
        return None
    user_hit = _find_best_label_match(q_lower, list(catalog.user_names.keys()))
    comp_hit = _find_best_label_match(q_lower, list(catalog.company_names.keys()))
    if comp_hit and (not user_hit or len(comp_hit) > len(user_hit)):
        return ("user_ids", catalog.company_names[comp_hit][:3])
    if user_hit:
        return ("user_ids", catalog.user_names[user_hit][:3])
    team_hit = _find_best_label_match(q_lower, list(catalog.team_names.keys()))
    if team_hit and ("team" in q_lower or "-" in team_hit):
        return ("team_ids", [catalog.team_names[team_hit]])
    lead_hit = _find_best_label_match(q_lower, list(catalog.lead_names.keys()))
    if lead_hit:
        return ("lead_ids", catalog.lead_names[lead_hit][:3])
    return None


def _extract_history_entity_target(
    query: str,
    history: list[dict[str, str]] | None,
    catalog: _EntityCatalog,
) -> tuple[str, Any] | None:
    """Resolves entity target from recent history when current query uses a pronoun."""
    tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
    if not (tokens & _PRONOUN_REF_TOKENS) or not history:
        return None
    for turn in reversed(history[-4:]):
        text = turn.get("content", "")
        explicit = _detect_explicit_target(text)
        if explicit:
            return explicit
        named = _detect_named_target(text, catalog)
        if named:
            return named
    return None


def _resolve_target(
    query: str,
    history: list[dict[str, str]] | None,
    original_query: str | None,
    db: Database[dict[str, Any]],
    company_id: str | None = None,
) -> tuple[str, Any] | None:
    """Resolves the primary target entity reference from query or recent follow-up context."""
    from app.rag.condense import is_pagination_query, resolve_client_list_page  # noqa: PLC0415
    from app.rag.prompt import is_greeting  # noqa: PLC0415

    if resolve_client_list_page(query, history=history, original_query=original_query) is not None:
        return None
    for cand in (original_query, query):
        if cand and (is_greeting(cand) or is_pagination_query(cand)):
            return None
    catalog = _get_entity_catalog(db, company_id=company_id)
    for cand in (query, original_query):
        if not cand or not cand.strip():
            continue
        explicit = _detect_explicit_target(cand)
        if explicit:
            return explicit
        if catalog is not None:
            named = _detect_named_target(cand, catalog)
            if named:
                return named
    return _extract_history_entity_target(original_query or query, history, catalog) if catalog else None


def _safe_fields_lines(doc: dict[str, Any], exclude: frozenset[str] = frozenset()) -> list[str]:
    """Formats top-level non-sensitive BSON fields as readable bullet lines."""
    lines: list[str] = []
    for key, val in doc.items():
        if key in exclude or key == "__v" or is_sensitive_field(key):
            continue
        norm = normalize_bson_value(val)
        if norm in (None, "", []):
            continue
        if isinstance(norm, dict):
            sub = ", ".join(f"{sk}={normalize_bson_value(sv)}" for sk, sv in norm.items() if not is_sensitive_field(sk) and sv not in (None, ""))
            if sub:
                lines.append(f"- **{key}**: {sub}")
        elif isinstance(norm, list):
            if len(norm) <= 10 and all(not isinstance(x, (dict, list)) for x in norm):
                lines.append(f"- **{key}**: {', '.join(str(x) for x in norm)}")
            else:
                lines.append(f"- **{key}**: {len(norm)} items")
        else:
            lines.append(f"- **{key}**: {_strip_html(norm) if '<' in str(norm) else _fmt_dt(norm)}")
    return lines


def _format_tasks_chunk(tasks: list[dict[str, Any]], heading: str, idx: int) -> RetrievedChunk | None:
    """Formats mapped tasks with status counts and update timelines into a RetrievedChunk."""
    if not tasks:
        return None
    status_cnt: dict[str, int] = {}
    for t in tasks:
        st = str(t.get("status") or "Unknown")
        status_cnt[st] = status_cnt.get(st, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(status_cnt.items()))
    lines = [f"### {heading} ({len(tasks)} total | {summary})"]
    for t in tasks[:_MAX_DETAIL_ROWS]:
        tid = t.get("taskId") or t.get("_id")
        cl_name = ((t.get("client") or {}).get("name")) or "N/A"
        assignee = ((t.get("assignee") or {}).get("name")) or t.get("owner") or "Unassigned"
        desc = _strip_html(t.get("description", ""))[:180]
        updates = t.get("updates") or []
        upd_str = f" | Updates ({len(updates)}): {_strip_html(updates[-1])[:120]}" if updates else ""
        desc_str = f" | Details: {desc}" if desc else ""
        lines.append(
            f"- **{tid}** — {_strip_html(t.get('title', ''))} | Status: {t.get('status')} | Priority: {t.get('priority')} "
            f"| Client: {cl_name} | Assignee: {assignee} | Due: {t.get('dueDate', 'N/A')} "
            f"| Created: {_fmt_dt(t.get('createdAt'))}{desc_str}{upd_str}"
        )
    return _make_chunk("tasks", lines, chunk_index=idx)


def _format_notes_chunk(notes: list[dict[str, Any]], heading: str, idx: int) -> RetrievedChunk | None:
    """Formats chronological operational/client notes into a RetrievedChunk."""
    if not notes:
        return None
    lines = [f"### {heading} ({len(notes)} total)"]
    for n in notes[:_MAX_DETAIL_ROWS]:
        dt = _fmt_dt(n.get("date") or n.get("createdAt"))
        lines.append(f"- [{dt}] **Author: {n.get('author', 'Staff')}** — {_strip_html(n.get('content', ''))}")
    return _make_chunk("notes", lines, chunk_index=idx)


def _format_events_chunk(events: list[dict[str, Any]], idx: int) -> RetrievedChunk | None:
    """Formats subscription extension/plan change history into a RetrievedChunk."""
    if not events:
        return None
    lines = [f"### Subscription History & Events ({len(events)} total)"]
    for ev in events[:_MAX_DETAIL_ROWS]:
        amt = f"₹{ev['amountPaise'] / 100:,.2f}" if isinstance(ev.get("amountPaise"), (int, float)) else "N/A"
        lines.append(
            f"- [{_fmt_dt(ev.get('createdAt') or ev.get('paymentDate'))}] **{ev.get('kind')}** ({ev.get('status')}) "
            f"| Plan: {ev.get('planName', 'N/A')} | Window: {_fmt_dt(ev.get('previousEnd'))} -> {_fmt_dt(ev.get('newEnd'))} "
            f"| Amount: {amt} | Ref: {ev.get('reference') or 'N/A'} | Note: {ev.get('note') or '-'}"
        )
    return _make_chunk("subscriptionevents", lines, chunk_index=idx)


def _format_tickets_chunk(tickets: list[dict[str, Any]], idx: int) -> RetrievedChunk | None:
    """Formats linked support tickets and conversation threads into a RetrievedChunk."""
    if not tickets:
        return None
    lines = [f"### Support Tickets History ({len(tickets)} total)"]
    for tk in tickets[:_MAX_DETAIL_ROWS]:
        msgs = tk.get("messages") or []
        preview = " | ".join(
            f"{m.get('senderName', m.get('senderRole', 'User'))}: {_strip_html(m.get('message') or m.get('content') or m.get('text', ''))[:100]}"
            for m in msgs[-3:] if isinstance(m, dict)
        )
        lines.append(
            f"- **{tk.get('ticketCode')}** — {tk.get('subject')} | Category: {tk.get('category')} "
            f"| Priority: {tk.get('priority')} | Status: {tk.get('status')} | Rating: {tk.get('rating', 'N/A')} "
            f"| Created: {_fmt_dt(tk.get('createdAt'))} | Resolved: {_fmt_dt(tk.get('resolvedAt'))}"
            + (f" | Recent Messages: {preview}" if preview else "")
        )
    return _make_chunk("supporttickets", lines, chunk_index=idx)


def _format_invoices_chunk(invoices: list[dict[str, Any]], heading: str, idx: int) -> RetrievedChunk | None:
    """Formats financial invoice line items and tax details into a RetrievedChunk."""
    if not invoices:
        return None
    total_amt = sum(float(inv.get("amount", 0) or 0) for inv in invoices)
    lines = [f"### {heading} ({len(invoices)} total | Total Amount: ₹{total_amt:,.2f})"]
    for inv in invoices[:_MAX_DETAIL_ROWS]:
        items_desc = ", ".join(
            f"{it.get('description', '')} (Qty {it.get('quantity', 1)}, HSN {it.get('hsnCode', 'N/A')})"
            for it in (inv.get("items") or []) if isinstance(it, dict)
        )
        cl = inv.get("client") or {}
        lines.append(
            f"- **{inv.get('invoiceNumber')}** | Client: {cl.get('name', 'N/A')} ({cl.get('company', '')}) "
            f"| Amount: ₹{inv.get('amount', 0)} (SubTotal: ₹{inv.get('subTotal', 0)}) | Status: {inv.get('status')} "
            f"| Date: {_fmt_dt(inv.get('date'))} | Due: {_fmt_dt(inv.get('dueDate'))} "
            f"| Method: {inv.get('paymentMethod', 'N/A')} | Items: {items_desc or 'N/A'} | Notes: {inv.get('notes', '')}"
        )
    return _make_chunk("invoices", lines, chunk_index=idx)


def _format_approvals_chunk(approvals: list[dict[str, Any]], heading: str, idx: int) -> RetrievedChunk | None:
    """Formats approval requests, verification checks, and decision logs into a RetrievedChunk."""
    if not approvals:
        return None
    lines = [f"### {heading} ({len(approvals)} total)"]
    for ap in approvals[:_MAX_DETAIL_ROWS]:
        decs = ap.get("decisions") or []
        dec_str = f" | Decided by: {decs[-1].get('byName', 'Manager')}" if decs and isinstance(decs[-1], dict) else ""
        ref = ap.get("ref") or {}
        lines.append(
            f"- [{_fmt_dt(ap.get('createdAt'))}] **{ap.get('type', ap.get('action'))}** ({ref.get('model', '')}) "
            f"| Status: {ap.get('status')} | Client: {ap.get('clientName', 'N/A')}{dec_str} "
            f"| Decided At: {_fmt_dt(ap.get('decidedAt'))}"
        )
    return _make_chunk("approvalrequests", lines, chunk_index=idx)


def _format_leads_chunk(leads: list[dict[str, Any]], heading: str, idx: int) -> RetrievedChunk | None:
    """Formats CRM lead profiles and timestamped lead notes into a RetrievedChunk."""
    if not leads:
        return None
    lines = [f"### {heading} ({len(leads)} total)"]
    for ld in leads[:_MAX_DETAIL_ROWS]:
        note_msgs = " | ".join(_strip_html(n.get("body") or n.get("text") or n) for n in (ld.get("notes") or [])[-3:])
        lines.append(
            f"- **{ld.get('leadCode')}** — {ld.get('name')} ({ld.get('company', 'N/A')}) "
            f"| Status: {ld.get('status')} | Score: {ld.get('score')} | Service: {ld.get('serviceInterested')} "
            f"| Source: {ld.get('source')} | Team: {ld.get('teamName', 'N/A')} "
            f"| Assigned To: {ld.get('assignedToName') or 'Unassigned'}"
            + (f" | Notes: {note_msgs}" if note_msgs else "")
        )
    return _make_chunk("leads", lines, chunk_index=idx)


def _client_core_chunks(
    db: Database[dict[str, Any]],
    client_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> tuple[list[RetrievedChunk], list[ObjectId]]:
    """Builds primary profile, mapped companies, and marketplace account chunks for a client."""
    uid = client_doc["_id"]
    name = str(client_doc.get("name", "")).strip()
    chunks: list[RetrievedChunk] = []
    team_map = {
        tm["_id"]: tm.get("name", "")
        for tm in _tfind(db, "teams", {}, "admin", company_id=company_id, projection={"name": 1})
    }
    team_names = [team_map.get(tid, str(tid)) for tid in (client_doc.get("teams") or [])]
    profile_lines = [f"# Complete 360° Client Profile: {name} (ID: {uid})"]
    profile_lines.extend(_safe_fields_lines(client_doc, exclude=frozenset({"permissions", "teams"})))
    if team_names:
        profile_lines.append(f"- **Assigned Teams**: {', '.join(team_names)}")
    if prof := _make_chunk("users", profile_lines, chunk_index=1):
        chunks.append(prof)

    comp_ids: list[ObjectId] = []
    if _can_read_collection("clientcompanies", role) or (role.lower() == "client" and client_id):
        comps = _tfind(db, "clientcompanies", {"clientId": uid}, role, client_id=client_id, company_id=company_id)
        comp_ids = [c["_id"] for c in comps]
        if comps and (c_comp := _make_chunk("clientcompanies", [f"### Mapped Client Companies ({len(comps)})"] + [
            f"- **{c.get('name')}** (GST: {c.get('gstNo', 'NA')}, Country: {c.get('country', 'India')}, Primary: {c.get('isPrimary')}, Active: {c.get('isActive')})"
            for c in comps
        ], chunk_index=2)):
            chunks.append(c_comp)

    acc_ids: list[ObjectId] = []
    if _can_read_collection("clientaccounts", role) or (role.lower() == "client" and client_id):
        acc_q = {"$or": [{"clientId": uid}, {"companyId": {"$in": comp_ids}}]} if comp_ids else {"clientId": uid}
        accs = _tfind(db, "clientaccounts", acc_q, role, client_id=client_id, company_id=company_id)
        acc_ids = [a["_id"] for a in accs]
        if accs:
            alines = [f"### Marketplace Accounts & Subscriptions ({len(accs)})"]
            for a in accs:
                mgr_info = catalog.user_lookup.get(a.get("managerId"), {}) if catalog and a.get("managerId") else {}
                mgr_str = str(a.get("manager") or mgr_info.get("name") or "Unassigned")
                if mgr_info.get("email"):
                    mgr_str += f" (`{mgr_info['email']}`)"
                alines.append(
                    f"- **Account {a['_id']}** | Marketplace: {a.get('marketplace')} | Plan: {a.get('plan')} ({a.get('supportType')}) "
                    f"| Stage: {a.get('stage')} | Manager: {mgr_str} | Supervisor: {a.get('supervisor') or 'N/A'} "
                    f"| Sales Mgr: {a.get('salesManager') or 'N/A'} | Listing Mgr: {a.get('listingManager') or 'N/A'} "
                    f"| Ads Mgr: {a.get('adsManager') or 'N/A'} | Fee: ₹{a.get('amount', 0)} (Discount: {a.get('discount', 0)}) "
                    f"| Period: {_fmt_dt(a.get('subscriptionStart'))} -> {_fmt_dt(a.get('subscriptionEnd'))} | Launch Week: {a.get('launchWeek') or 'N/A'}"
                )
            if c_acc := _make_chunk("clientaccounts", alines, chunk_index=3):
                chunks.append(c_acc)
    return chunks, acc_ids


def _client_activity_chunks(
    db: Database[dict[str, Any]],
    uid: ObjectId,
    acc_ids: list[ObjectId],
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> tuple[list[RetrievedChunk], list[ObjectId], list[ObjectId]]:
    """Collects tasks, notes, subscription events, and support tickets mapped to a client."""
    chunks: list[RetrievedChunk] = []
    task_ids: list[ObjectId] = []
    sub_ids: list[ObjectId] = []
    if _can_read_collection("tasks", role):
        tq = {"$or": [{"client.id": uid}, {"client.accountId": {"$in": acc_ids}}]} if acc_ids else {"client.id": uid}
        tasks = _tfind(db, "tasks", tq, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1))
        task_ids = [t["_id"] for t in tasks]
        if c_tsk := _format_tasks_chunk(tasks, "Client Operational Tasks & History", idx=4):
            chunks.append(c_tsk)
    if _can_read_collection("notes", role):
        nq = {"$or": [{"clientId": uid}, {"accountId": {"$in": acc_ids}}]} if acc_ids else {"clientId": uid}
        if c_note := _format_notes_chunk(
            _tfind(db, "notes", nq, role, client_id=client_id, company_id=company_id, sort_spec=("date", -1)),
            "Account Notes & Activity Log",
            idx=5,
        ):
            chunks.append(c_note)
    if _can_read_collection("subscriptionevents", role) or (role.lower() == "client" and client_id):
        eq = {"$or": [{"clientId": uid}, {"accountId": {"$in": acc_ids}}]} if acc_ids else {"clientId": uid}
        evs = _tfind(db, "subscriptionevents", eq, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1))
        sub_ids = [e["_id"] for e in evs]
        if c_ev := _format_events_chunk(evs, idx=6):
            chunks.append(c_ev)
    if _can_read_collection("supporttickets", role) or (role.lower() == "client" and client_id):
        tkq = {"$or": [{"clientId": uid}, {"clientAccountId": {"$in": acc_ids}}]} if acc_ids else {"clientId": uid}
        if c_tk := _format_tickets_chunk(
            _tfind(db, "supporttickets", tkq, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1)),
            idx=7,
        ):
            chunks.append(c_tk)
    return chunks, task_ids, sub_ids


def _query_client_invoices(
    db: Database[dict[str, Any]],
    uid: ObjectId,
    email: str,
    name: str,
    role: str = "finance",
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[dict[str, Any]]:
    """Queries invoices matching client ObjectId, exact email, or exact client name."""
    clauses: list[dict[str, Any]] = [{"client.id": uid}]
    if email:
        clauses.append({"client.email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}})
    if len(name) >= 5:
        clauses.append({"client.name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    return _tfind(db, "invoices", {"$or": clauses}, role, client_id=client_id, company_id=company_id, sort_spec=("date", -1))


def _hydrate_client_360(
    db: Database[dict[str, Any]],
    client_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete RBAC-filtered 360-degree dossier chunks for a single client."""
    uid = client_doc["_id"]
    email = str(client_doc.get("email", "")).strip()
    name = str(client_doc.get("name", "")).strip()
    chunks, acc_ids = _client_core_chunks(db, client_doc, role, catalog, client_id=client_id, company_id=company_id)
    act_chunks, task_ids, sub_ids = _client_activity_chunks(db, uid, acc_ids, role, client_id=client_id, company_id=company_id)
    chunks.extend(act_chunks)

    if _can_read_collection("invoices", role):
        invs = _query_client_invoices(db, uid, email, name, role=role, client_id=client_id, company_id=company_id)
        if c_inv := _format_invoices_chunk(invs, "Client Invoices & Billing History", idx=8):
            chunks.append(c_inv)

    lead_ids: list[ObjectId] = []
    if _can_read_collection("leads", role) and email:
        leads = _tfind(db, "leads", {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}}, role, client_id=client_id, company_id=company_id)
        lead_ids = [ld["_id"] for ld in leads]
        if c_ld := _format_leads_chunk(leads, "Linked CRM Lead History", idx=9):
            chunks.append(c_ld)

    if _can_read_collection("approvalrequests", role):
        apq: dict[str, Any] = {"$or": [{"ref.id": {"$in": [uid, *acc_ids, *task_ids, *sub_ids, *lead_ids]}}]}
        if name:
            apq["$or"].append({"clientName": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
        if c_ap := _format_approvals_chunk(
            _tfind(db, "approvalrequests", apq, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1)),
            "Approval Requests & Audits",
            idx=10,
        ):
            chunks.append(c_ap)

    if _can_read_collection("profileupdaterequests", role):
        prs = _tfind(
            db, "profileupdaterequests", {"$or": [{"user": uid}, {"client": uid}]},
            role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1),
        )
        if prs and (c_pr := _make_chunk("profileupdaterequests", [f"### Profile Update Requests ({len(prs)})"] + [
            f"- [{_fmt_dt(p.get('createdAt'))}] Status: {p.get('status')} | Admin Note: {p.get('adminNotes', 'N/A')} | Old: {p.get('oldData')} -> New: {p.get('newData')}"
            for p in prs[:10]
        ], chunk_index=11)):
            chunks.append(c_pr)
    return chunks


def _emp_core_chunks(
    db: Database[dict[str, Any]],
    emp_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Builds employee profile, teams, reporting hierarchy, and leave balance chunks."""
    uid = emp_doc["_id"]
    name = str(emp_doc.get("name", "")).strip()
    chunks: list[RetrievedChunk] = []
    profile_lines = [f"# Complete 360° Employee Profile: {name} ({emp_doc.get('employeeId') or emp_doc.get('role')})"]
    profile_lines.extend(_safe_fields_lines(emp_doc, exclude=frozenset({"permissions", "assignedClientIds", "teams"})))
    if c_prof := _make_chunk("users", profile_lines, chunk_index=1):
        chunks.append(c_prof)

    if _can_read_collection("teams", role):
        teams = _tfind(db, "teams", {"$or": [{"leadId": uid}, {"memberIds": uid}]}, role, client_id=client_id, company_id=company_id)
        if teams and (c_tm := _make_chunk("teams", [f"### Team Memberships & Leadership ({len(teams)})"] + [
            f"- **{t.get('name')}** ({t.get('code', 'Team')}) — Role: {'Team Lead' if t.get('leadId') == uid else 'Member'} | Status: {t.get('status')} | Clients: {t.get('clientCount', 0)}"
            for t in teams
        ], chunk_index=2)):
            chunks.append(c_tm)

    if _can_read_collection("reportingrelations", role):
        rels = _tfind(db, "reportingrelations", {"$or": [{"subordinateUserId": uid}, {"managerUserId": uid}]}, role, client_id=client_id, company_id=company_id)
        if rels:
            rlines = [f"### Reporting Hierarchy ({len(rels)} links)"]
            for r in rels:
                st = "Active" if r.get("effectiveTo") is None else f"Ended {_fmt_dt(r.get('effectiveTo'))}"
                if r.get("subordinateUserId") == uid:
                    mgr = catalog.user_lookup.get(r.get("managerUserId"), {}) if catalog else {}
                    rlines.append(f"- **Reports To Manager**: {mgr.get('name', r.get('managerUserId'))} ({mgr.get('empId', 'Admin')}) [{st}]")
                else:
                    sub = catalog.user_lookup.get(r.get("subordinateUserId"), {}) if catalog else {}
                    rlines.append(f"- **Direct Report**: {sub.get('name', r.get('subordinateUserId'))} ({sub.get('empId', '')}) [{st}]")
            if c_rel := _make_chunk("reportingrelations", rlines, chunk_index=3):
                chunks.append(c_rel)

    if _can_read_collection("leavebalances", role):
        lbs = _tfind(db, "leavebalances", {"userId": uid}, role, client_id=client_id, company_id=company_id)
        if lbs and (c_lb := _make_chunk("leavebalances", ["### Employee Leave Balances"] + [
            f"- **FY {lb.get('fiscalYear')}**: Casual {lb.get('casualBalance', 0)} (Used {lb.get('casualUsed', 0)}) | Sick {lb.get('sickBalance', 0)} (Used {lb.get('sickUsed', 0)}) | Earned {lb.get('earnedBalance', 0)} (Used {lb.get('earnedUsed', 0)})"
            for lb in lbs
        ], chunk_index=4)):
            chunks.append(c_lb)
    return chunks


def _emp_portfolio_chunk(
    db: Database[dict[str, Any]],
    uid: ObjectId,
    name: str,
    catalog: _EntityCatalog | None,
    role: str = "employee",
    client_id: str | None = None,
    company_id: str | None = None,
) -> RetrievedChunk | None:
    """Formats managed client accounts summary and sample roster for an employee."""
    accs = _tfind(db, "clientaccounts", {"$or": [{"managerId": uid}, {"manager": name}]}, role, client_id=client_id, company_id=company_id)
    if not accs:
        return None
    plan_cnt: dict[str, int] = {}
    for a in accs:
        pl = str(a.get("plan") or "Other")
        plan_cnt[pl] = plan_cnt.get(pl, 0) + 1
    plan_sum = ", ".join(f"{k}: {v}" for k, v in sorted(plan_cnt.items(), key=lambda x: -x[1]))
    alines = [f"### Managed Client Accounts Portfolio ({len(accs)} total | Plans: {plan_sum})"]
    for a in accs[:30]:
        c_info = catalog.user_lookup.get(a.get("clientId"), {}) if catalog else {}
        alines.append(
            f"- **{c_info.get('name', a.get('clientId'))}** ({c_info.get('company', 'N/A')}) "
            f"| {a.get('marketplace')} | Plan: {a.get('plan')} | Stage: {a.get('stage')}"
        )
    return _make_chunk("clientaccounts", alines, chunk_index=5)


def _hydrate_employee_360(
    db: Database[dict[str, Any]],
    emp_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete RBAC-filtered 360-degree dossier chunks for a staff member."""
    uid = emp_doc["_id"]
    name = str(emp_doc.get("name", "")).strip()
    email = str(emp_doc.get("email", "")).strip()
    chunks = _emp_core_chunks(db, emp_doc, role, catalog, client_id=client_id, company_id=company_id)

    if _can_read_collection("clientaccounts", role):
        if c_acc := _emp_portfolio_chunk(db, uid, name, catalog, role=role, client_id=client_id, company_id=company_id):
            chunks.append(c_acc)
    if _can_read_collection("tasks", role):
        tasks = _tfind(db, "tasks", {"$or": [{"assignee.id": uid}, {"owner": name}]}, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1))
        if c_tsk := _format_tasks_chunk(tasks, "Assigned & Owned Tasks", idx=6):
            chunks.append(c_tsk)
    if _can_read_collection("notes", role):
        if c_note := _format_notes_chunk(
            _tfind(db, "notes", {"authorId": uid}, role, client_id=client_id, company_id=company_id, sort_spec=("date", -1)),
            "Notes Authored by Employee",
            idx=7,
        ):
            chunks.append(c_note)
    if _can_read_collection("leads", role):
        if c_ld := _format_leads_chunk(
            _tfind(db, "leads", {"assignedTo": uid}, role, client_id=client_id, company_id=company_id),
            "Assigned Sales Leads",
            idx=8,
        ):
            chunks.append(c_ld)
    if _can_read_collection("scoresnapshots", role):
        scores = _tfind(db, "scoresnapshots", {"scopeId": uid}, role, client_id=client_id, company_id=company_id, sort_spec=("periodStart", -1))
        if scores and (c_sc := _make_chunk("scoresnapshots", [f"### KPI Performance & Score Snapshots ({len(scores)})"] + [
            f"- Period {_fmt_dt(s.get('periodStart'))[:10]}..{_fmt_dt(s.get('periodEnd'))[:10]} | Direct Score: {s.get('directScore')} | Final Score: {s.get('finalScore')} | Rank: {s.get('rank')} | Total Weight: {s.get('totalWeight')}"
            for s in scores[:10]
        ], chunk_index=9)):
            chunks.append(c_sc)
    if _can_read_collection("approvalrequests", role):
        aps = _tfind(
            db, "approvalrequests", {"$or": [{"decidedBy": uid}, {"decisions.by": uid}]},
            role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1),
        )
        if c_ap := _format_approvals_chunk(aps, "Approval Reviews & Decisions", idx=10):
            chunks.append(c_ap)
    if _can_read_collection("invoices", role):
        if c_inv := _format_invoices_chunk(
            _query_client_invoices(db, uid, email, name, role=role, client_id=client_id, company_id=company_id),
            "Linked Invoices",
            idx=11,
        ):
            chunks.append(c_inv)
    return chunks


def _hydrate_task_360(
    db: Database[dict[str, Any]],
    task_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete 360-degree details, updates, linked client, and approvals for a task."""
    if not _can_read_collection("tasks", role):
        return []
    chunks: list[RetrievedChunk] = []
    lines = [f"# Complete Task Record: {task_doc.get('taskId')} — {_strip_html(task_doc.get('title', ''))}"]
    lines.extend(_safe_fields_lines(task_doc, exclude=frozenset({"updates"})))
    updates = task_doc.get("updates") or []
    if updates:
        lines.append(f"### Chronological Task Updates ({len(updates)})")
        lines.extend(f"- {_strip_html(u)}" for u in updates[:_MAX_DETAIL_ROWS])
    if c_tsk := _make_chunk("tasks", lines, chunk_index=1):
        chunks.append(c_tsk)
    if _can_read_collection("approvalrequests", role):
        if c_ap := _format_approvals_chunk(
            _tfind(db, "approvalrequests", {"ref.id": task_doc["_id"]}, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1)),
            "Task Approval History",
            idx=2,
        ):
            chunks.append(c_ap)
    cl_id = (task_doc.get("client") or {}).get("id")
    if isinstance(cl_id, ObjectId) and (cl_doc := _tfind_one(db, "users", {"_id": cl_id}, role, client_id=client_id, company_id=company_id)):
        chunks.extend(_hydrate_client_360(db, cl_doc, role, catalog, client_id=client_id, company_id=company_id)[:4])
    return chunks


def _hydrate_invoice_360(
    db: Database[dict[str, Any]],
    inv_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete invoice breakdown, linked client, and subscription event (finance-gated)."""
    if not _can_read_collection("invoices", role):
        return []
    lines = [f"# Complete Invoice Record: {inv_doc.get('invoiceNumber')}"]
    lines.extend(_safe_fields_lines(inv_doc, exclude=frozenset({"items"})))
    for it in (inv_doc.get("items") or []):
        if isinstance(it, dict):
            lines.append(f"- **Line Item**: {it.get('description')} | HSN: {it.get('hsnCode')} | Qty: {it.get('quantity')} | Price: ₹{it.get('unitPrice', it.get('amount', ''))}")
    chunks: list[RetrievedChunk] = []
    if c_inv := _make_chunk("invoices", lines, chunk_index=1):
        chunks.append(c_inv)
    if _can_read_collection("subscriptionevents", role):
        if c_ev := _format_events_chunk(
            _tfind(db, "subscriptionevents", {"invoiceId": inv_doc["_id"]}, role, client_id=client_id, company_id=company_id),
            idx=2,
        ):
            chunks.append(c_ev)
    cl_id = (inv_doc.get("client") or {}).get("id")
    if isinstance(cl_id, ObjectId) and (cl_doc := _tfind_one(db, "users", {"_id": cl_id}, role, client_id=client_id, company_id=company_id)):
        chunks.extend(_hydrate_client_360(db, cl_doc, role, catalog, client_id=client_id, company_id=company_id)[:3])
    return chunks


def _hydrate_lead_360(
    db: Database[dict[str, Any]],
    lead_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete lead details, notes history, approvals, and converted client records."""
    if not _can_read_collection("leads", role):
        return []
    chunks: list[RetrievedChunk] = []
    lines = [f"# Complete Lead Record: {lead_doc.get('leadCode')} — {lead_doc.get('name')}"]
    lines.extend(_safe_fields_lines(lead_doc, exclude=frozenset({"notes"})))
    notes = lead_doc.get("notes") or []
    if notes:
        lines.append(f"### Lead Follow-Up Notes ({len(notes)})")
        lines.extend(f"- {_strip_html(n)}" for n in notes[:_MAX_DETAIL_ROWS])
    if c_ld := _make_chunk("leads", lines, chunk_index=1):
        chunks.append(c_ld)
    if _can_read_collection("approvalrequests", role):
        if c_ap := _format_approvals_chunk(
            _tfind(db, "approvalrequests", {"ref.id": lead_doc["_id"]}, role, client_id=client_id, company_id=company_id, sort_spec=("createdAt", -1)),
            "Lead Approval Requests",
            idx=2,
        ):
            chunks.append(c_ap)
    email = str(lead_doc.get("email", "")).strip()
    if email and (u_doc := _tfind_one(db, "users", {"email": {"$regex": f"^{re.escape(email)}$", "$options": "i"}, "role": "client"}, role, client_id=client_id, company_id=company_id)):
        chunks.extend(_hydrate_client_360(db, u_doc, role, catalog, client_id=client_id, company_id=company_id)[:4])
    return chunks


def _hydrate_ticket_360(
    db: Database[dict[str, Any]],
    tk_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete support ticket conversation history and linked client account."""
    if not _can_read_collection("supporttickets", role) and not (role.lower() == "client" and client_id):
        return []
    chunks: list[RetrievedChunk] = []
    lines = [f"# Complete Support Ticket: {tk_doc.get('ticketCode')} — {tk_doc.get('subject')}"]
    lines.extend(_safe_fields_lines(tk_doc, exclude=frozenset({"messages"})))
    msgs = tk_doc.get("messages") or []
    if msgs:
        lines.append(f"### Ticket Message Thread ({len(msgs)})")
        for m in msgs:
            if isinstance(m, dict):
                lines.append(f"- [{_fmt_dt(m.get('at') or m.get('createdAt'))}] **{m.get('senderName')} ({m.get('senderRole')})**: {_strip_html(m.get('message') or m.get('content') or m.get('text', ''))}")
    if c_tk := _make_chunk("supporttickets", lines, chunk_index=1):
        chunks.append(c_tk)
    cl_id = tk_doc.get("clientId")
    if isinstance(cl_id, ObjectId) and (cl_doc := _tfind_one(db, "users", {"_id": cl_id}, role, client_id=client_id, company_id=company_id)):
        chunks.extend(_hydrate_client_360(db, cl_doc, role, catalog, client_id=client_id, company_id=company_id)[:3])
    return chunks


def _hydrate_team_360(
    db: Database[dict[str, Any]],
    tm_doc: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Assembles complete team profile, lead, member roster, and team KPIs."""
    if not _can_read_collection("teams", role):
        return []
    lines = [f"# Complete Team Profile: {tm_doc.get('name')} ({tm_doc.get('code', 'Team')})"]
    lines.extend(_safe_fields_lines(tm_doc, exclude=frozenset({"memberIds"})))
    lead = catalog.user_lookup.get(tm_doc.get("leadId"), {}) if catalog and tm_doc.get("leadId") else {}
    if lead:
        lines.append(f"- **Team Lead**: {lead.get('name')} ({lead.get('empId')}, `{lead.get('email')}`)")
    if catalog:
        m_names = [f"{(catalog.user_lookup.get(mid) or {}).get('name', mid)} ({(catalog.user_lookup.get(mid) or {}).get('empId', '')})" for mid in (tm_doc.get("memberIds") or [])]
        if m_names:
            lines.append(f"- **Team Members ({len(m_names)})**: {', '.join(m_names)}")
    chunks: list[RetrievedChunk] = []
    if c_tm := _make_chunk("teams", lines, chunk_index=1):
        chunks.append(c_tm)
    if _can_read_collection("scoresnapshots", role):
        scores = _tfind(db, "scoresnapshots", {"scopeId": tm_doc["_id"]}, role, client_id=client_id, company_id=company_id, sort_spec=("periodStart", -1))
        if scores and (c_sc := _make_chunk("scoresnapshots", ["### Team KPI Score Snapshots"] + [
            f"- Period {_fmt_dt(s.get('periodStart'))[:10]}..{_fmt_dt(s.get('periodEnd'))[:10]} | Score: {s.get('finalScore')} | Rank: {s.get('rank')}"
            for s in scores[:6]
        ], chunk_index=2)):
            chunks.append(c_sc)
    return chunks


def _hydrate_user_docs(
    db: Database[dict[str, Any]],
    docs: list[dict[str, Any]],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Routes matched user documents to client or employee 360-degree hydrators."""
    chunks: list[RetrievedChunk] = []
    for u in docs[:2]:
        if str(u.get("role", "")).lower() == "client":
            chunks.extend(_hydrate_client_360(db, u, role, catalog, client_id=client_id, company_id=company_id))
        elif role.lower() != "client":
            chunks.extend(_hydrate_employee_360(db, u, role, catalog, client_id=client_id, company_id=company_id))
    return chunks


def _lookup_contact_or_lead(
    db: Database[dict[str, Any]],
    field: str,
    pattern: dict[str, Any],
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Resolves email or phone match across users first and leads second."""
    if _can_read_collection("users", role) and (docs := _tfind(db, "users", {field: pattern}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_user_docs(db, docs, role, catalog, client_id=client_id, company_id=company_id)
    if _can_read_collection("leads", role) and (ld := _tfind_one(db, "leads", {field: pattern}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_lead_360(db, ld, role, catalog, client_id=client_id, company_id=company_id)
    return []


def _dispatch_target(
    db: Database[dict[str, Any]],
    kind: str,
    val: Any,  # str or list[ObjectId]
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Executes targeted read-only lookup and 360-degree hydration for resolved target."""
    if role.lower() == "client":
        return []
    if kind == "user_ids" and _can_read_collection("users", role):
        return _hydrate_user_docs(db, _tfind(db, "users", {"_id": {"$in": val}}, role, client_id=client_id, company_id=company_id), role, catalog, client_id=client_id, company_id=company_id)
    if kind == "employee_id" and _can_read_collection("users", role):
        return _hydrate_user_docs(db, _tfind(db, "users", {"employeeId": {"$regex": f"^{re.escape(str(val))}$", "$options": "i"}}, role, client_id=client_id, company_id=company_id), role, catalog, client_id=client_id, company_id=company_id)
    if kind in ("email", "phone"):
        pat = {"$regex": f"^{re.escape(str(val))}$", "$options": "i"} if kind == "email" else {"$regex": re.escape(str(val))}
        return _lookup_contact_or_lead(db, kind, pat, role, catalog, client_id=client_id, company_id=company_id)
    if kind == "task" and _can_read_collection("tasks", role) and (t := _tfind_one(db, "tasks", {"taskId": {"$regex": f"^{re.escape(str(val))}$", "$options": "i"}}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_task_360(db, t, role, catalog, client_id=client_id, company_id=company_id)
    if kind == "invoice" and _can_read_collection("invoices", role):
        inv = _tfind_one(db, "invoices", {"invoiceNumber": {"$regex": f"^{re.escape(str(val))}$", "$options": "i"}}, role, client_id=client_id, company_id=company_id)
        return _hydrate_invoice_360(db, inv, role, catalog, client_id=client_id, company_id=company_id) if inv else []
    if kind == "lead" and _can_read_collection("leads", role) and (ld := _tfind_one(db, "leads", {"leadCode": {"$regex": f"^{re.escape(str(val))}$", "$options": "i"}}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_lead_360(db, ld, role, catalog, client_id=client_id, company_id=company_id)
    if kind == "lead_ids" and _can_read_collection("leads", role) and (ld := _tfind_one(db, "leads", {"_id": {"$in": val}}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_lead_360(db, ld, role, catalog, client_id=client_id, company_id=company_id)
    if kind == "ticket" and _can_read_collection("supporttickets", role) and (tk := _tfind_one(db, "supporttickets", {"ticketCode": {"$regex": f"^{re.escape(str(val))}$", "$options": "i"}}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_ticket_360(db, tk, role, catalog, client_id=client_id, company_id=company_id)
    if kind == "team_ids" and _can_read_collection("teams", role) and (tm := _tfind_one(db, "teams", {"_id": {"$in": val}}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_team_360(db, tm, role, catalog, client_id=client_id, company_id=company_id)
    if kind == "object_id":
        return _dispatch_object_id(db, ObjectId(str(val)), role, catalog, client_id=client_id, company_id=company_id)
    return []


def _dispatch_object_id(
    db: Database[dict[str, Any]],
    oid: ObjectId,
    role: str,
    catalog: _EntityCatalog | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Resolves a raw BSON ObjectId across primary entity collections."""
    if _can_read_collection("users", role) and (u := _tfind_one(db, "users", {"_id": oid}, role, client_id=client_id, company_id=company_id)):
        return _hydrate_user_docs(db, [u], role, catalog, client_id=client_id, company_id=company_id)
    handlers = (("tasks", _hydrate_task_360), ("invoices", _hydrate_invoice_360), ("leads", _hydrate_lead_360), ("supporttickets", _hydrate_ticket_360))
    for col, fn in handlers:
        if _can_read_collection(col, role) and (doc := _tfind_one(db, col, {"_id": oid}, role, client_id=client_id, company_id=company_id)):
            return fn(db, doc, role, catalog, client_id=client_id, company_id=company_id)
    return []


def _lookup_self_client_360(
    db: Database[dict[str, Any]],
    query: str,
    client_id: str,
    company_id: str | None = None,
) -> list[RetrievedChunk]:
    """Returns strictly self-scoped 360 chunks when an authenticated client asks about their own account."""
    lower = query.lower()
    self_hints = ("my ", "mine", "mera ", "meri ", "mere ", "apna", "profile", "account", "subscription", "ticket", "plan")
    if not any(h in lower for h in self_hints):
        return []
    key: Any = ObjectId(client_id) if len(client_id) == 24 and all(c in "0123456789abcdefABCDEF" for c in client_id) else client_id
    doc = _tfind_one(db, "users", {"_id": key, "role": "client"}, "client", client_id=client_id, company_id=company_id)
    if doc is None:
        return []
    return _hydrate_client_360(db, doc, "client", None, client_id=client_id, company_id=company_id)


def lookup_entity_360(
    query: str,
    role: str,
    original_query: str | None = None,
    history: list[dict[str, str]] | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> list[RetrievedChunk]:
    """Resolves complete RBAC-filtered and tenant-isolated 360-degree cross-collection records."""
    if role.lower() not in ROLE_ACCESS:
        return []
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return []
    from app.rag.condense import is_pagination_query, resolve_client_list_page  # noqa: PLC0415
    from app.rag.prompt import is_greeting  # noqa: PLC0415

    active_db = db if db is not None else _get_read_db()
    if active_db is None:
        return []
    if role.lower() == "client":
        return _lookup_self_client_360(active_db, original_query or query, client_id, company_id) if client_id else []

    if resolve_client_list_page(query, history=history, original_query=original_query) is not None:
        return []
    for cand in (original_query, query):
        if cand and (is_greeting(cand) or is_pagination_query(cand) or any(h in cand.lower() for h in _PLURAL_FILTER_HINTS)):
            return []

    try:
        target = _resolve_target(query, history, original_query, active_db, company_id=company_id)
        if target is None:
            return []
        catalog = _get_entity_catalog(active_db, company_id=company_id)
        return _dispatch_target(active_db, target[0], target[1], role, catalog, client_id=client_id, company_id=company_id)
    except Exception as exc:
        _logger.debug("Entity 360 lookup fallback (%s)", type(exc).__name__)
        if db is None:
            _trip_mongo_circuit()
        return []
