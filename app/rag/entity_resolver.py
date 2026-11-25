"""Dynamic entity search, canonical resolution, and ambiguity detection across approved MongoDB collections."""
from __future__ import annotations

import logging
import re
from typing import Any  # Pymongo BSON documents contain heterogeneous types

from bson import ObjectId
from pymongo.database import Database

from app.models.entities import EntityIdentity
from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.retriever import ROLE_ACCESS

_logger = logging.getLogger("chatbot.rag.entity_resolver")

_CODE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("tasks", "taskId", re.compile(r"\b(TSK-\d+)\b", re.IGNORECASE)),
    ("invoices", "invoiceNumber", re.compile(r"\b(FISIPL[A-Z0-9]{4,})\b", re.IGNORECASE)),
    ("leads", "leadCode", re.compile(r"\b(LEAD-\d+)\b", re.IGNORECASE)),
    ("supporttickets", "ticketCode", re.compile(r"\b(TCK-\d{4}-\d+)\b", re.IGNORECASE)),
    ("users", "employeeId", re.compile(r"\b(EMP-\d+)\b", re.IGNORECASE)),
    ("teams", "code", re.compile(r"\b(TEAM-[A-Z0-9-]+)\b", re.IGNORECASE)),
)
_EMAIL_RE: re.Pattern[str] = re.compile(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_RE: re.Pattern[str] = re.compile(r"(?:\+?91[\s-]?)?\b([6-9]\d{9})\b")
_OBJECT_ID_RE: re.Pattern[str] = re.compile(r"\b([0-9a-f]{24})\b", re.IGNORECASE)

_SEARCH_STOPWORDS: frozenset[str] = frozenset({
    "give", "me", "complete", "full", "all", "info", "information", "details", "detail",
    "about", "and", "show", "everywhere", "it", "is", "mapped", "mapping", "mappings",
    "where", "tell", "what", "who", "of", "the", "a", "an", "for", "in", "on", "to",
    "ke", "bare", "mein", "me", "batao", "bataiye", "dikhao", "puri", "poori", "sabhi",
    "sab", "kuch", "entity", "record", "records", "relationship", "relationships", "history",
    "client", "employee", "task", "invoice", "lead", "team", "account", "plan", "department",
})


def _can_read(col_name: str, role: str) -> bool:
    """Checks approved collection governance and caller role access."""
    spec = APPROVED_COLLECTIONS.get(col_name)
    return spec is not None and spec.access_level in ROLE_ACCESS.get(role.lower(), [])


def _clean_search_phrase(query: str) -> str:
    """Extracts core entity phrase by stripping common question/instruction words."""
    tokens = [t for t in re.findall(r"[A-Za-z0-9@._&+-]+", query.strip()) if t.lower() not in _SEARCH_STOPWORDS]
    return " ".join(tokens).strip()


def _to_user_identity(doc: dict[str, Any], matched_on: str) -> EntityIdentity:
    """Converts a MongoDB users doc into canonical Client or Employee EntityIdentity."""
    role_val = str(doc.get("role", "employee"))
    is_client = role_val.lower() == "client"
    subtitle = str(doc.get("company") or doc.get("email") or "") if is_client else f"{doc.get('designation') or role_val} | {doc.get('department', '')}".strip(" |")
    return EntityIdentity(
        id=str(doc["_id"]),
        name=str(doc.get("name", "Unknown")).strip(),
        type="Client" if is_client else "Employee",
        collection="users",
        code=str(doc.get("employeeId")) if doc.get("employeeId") else None,
        subtitle=subtitle or None,
        matched_on=matched_on,
    )


def _find_docs(
    db: Database[dict[str, Any]],
    col: str,
    filt: dict[str, Any],
    limit: int = 10,
    role: str = "employee",
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[dict[str, Any]]:
    """Executes bounded read-only find enforcing tenant isolation and timeouts."""
    from app.rag.entity_pagination import build_tenant_mongo_filter  # noqa: PLC0415

    scoped = build_tenant_mongo_filter(
        filt, role=role, client_id=client_id, company_id=company_id, collection_name=col
    )
    if scoped is None:
        return []
    try:
        cur = db[col].find(scoped)
        if hasattr(cur, "max_time_ms"):
            cur = cur.max_time_ms(2500)
        out: list[dict[str, Any]] = []
        for idx, doc in enumerate(cur):
            if idx >= limit:
                break
            out.append(doc)
        return out
    except Exception:
        return []


def _search_codes_and_contacts(
    db: Database[dict[str, Any]],
    phrase: str,
    role: str,
    entity_type: str | None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[EntityIdentity]:
    """Matches explicit business codes, emails, phone numbers, or 24-hex ObjectIds under tenant scope."""
    results: list[EntityIdentity] = []
    for col, field, pat in _CODE_PATTERNS:
        m = pat.search(phrase)
        if m and _can_read(col, role):
            code = m.group(1)
            for doc in _find_docs(db, col, {field: {"$regex": f"^{re.escape(code)}$", "$options": "i"}}, 5, role, client_id, company_id):
                if col == "users":
                    results.append(_to_user_identity(doc, field))
                else:
                    label_map = {"tasks": "Task", "invoices": "Invoice", "leads": "Lead", "supporttickets": "SupportTicket", "teams": "Team"}
                    results.append(EntityIdentity(id=str(doc["_id"]), name=str(doc.get("title") or doc.get("subject") or doc.get("name") or doc.get(field)), type=label_map[col], collection=col, code=str(doc.get(field)), matched_on=field))
    em = _EMAIL_RE.search(phrase)
    if em and _can_read("users", role):
        for doc in _find_docs(db, "users", {"email": {"$regex": f"^{re.escape(em.group(1))}$", "$options": "i"}}, 5, role, client_id, company_id):
            results.append(_to_user_identity(doc, "email"))
    ph = _PHONE_RE.search(phrase)
    if ph and _can_read("users", role):
        for doc in _find_docs(db, "users", {"phone": {"$regex": re.escape(ph.group(1))}}, 5, role, client_id, company_id):
            results.append(_to_user_identity(doc, "phone"))
    oid_m = _OBJECT_ID_RE.search(phrase)
    if oid_m:
        oid = ObjectId(oid_m.group(1))
        for col, typ in (("users", "User"), ("accounttypes", "AccountType"), ("departments", "Department"), ("pricingplans", "PricingPlan"), ("teams", "Team"), ("clientcompanies", "ClientCompany"), ("clientaccounts", "ClientAccount"), ("tasks", "Task"), ("invoices", "Invoice"), ("leads", "Lead"), ("supporttickets", "SupportTicket")):
            if _can_read(col, role):
                found = _find_docs(db, col, {"_id": oid}, 1, role, client_id, company_id)
                if found:
                    d = found[0]
                    if col == "users":
                        results.append(_to_user_identity(d, "_id"))
                    else:
                        results.append(EntityIdentity(id=str(d["_id"]), name=str(d.get("name") or d.get("title") or d.get("invoiceNumber") or d.get("ticketCode") or oid), type=typ, collection=col, code=str(d.get("code") or d.get("taskId") or d.get("leadCode") or "") or None, matched_on="_id"))
    if entity_type:
        return [r for r in results if r.type.lower() == entity_type.lower()]
    return results


def _search_named_collections(
    db: Database[dict[str, Any]],
    term: str,
    role: str,
    entity_type: str | None,
    limit: int,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[EntityIdentity]:
    """Searches account types, departments, pricing plans, users, companies, teams, and leads under tenant bounds."""
    pat = {"$regex": re.escape(term), "$options": "i"}
    out: list[EntityIdentity] = []
    catalog_sources = (
        ("accounttypes", "AccountType", "name", "slug"),
        ("departments", "Department", "name", "code"),
        ("pricingplans", "PricingPlan", "name", "subtitle"),
        ("platformplans", "PlatformPlan", "name", "slug"),
        ("teams", "Team", "name", "code"),
    )
    for col, typ, f1, f2 in catalog_sources:
        if (entity_type and typ.lower() != entity_type.lower()) or not _can_read(col, role):
            continue
        for doc in _find_docs(db, col, {"$or": [{f1: pat}, {f2: pat}]}, 5, role, client_id, company_id):
            out.append(EntityIdentity(id=str(doc["_id"]), name=str(doc.get(f1, "")), type=typ, collection=col, code=str(doc.get(f2) or "") or None, subtitle=f"{col} catalog", matched_on=f1))

    if _can_read("users", role) and (not entity_type or entity_type.lower() in ("client", "employee", "user")):
        u_docs = _find_docs(db, "users", {"$or": [{"name": pat}, {"company": pat}, {"email": pat}]}, limit, role, client_id, company_id)
        for doc in u_docs:
            ident = _to_user_identity(doc, "name_or_company")
            if not entity_type or ident.type.lower() == entity_type.lower():
                out.append(ident)

    if _can_read("clientcompanies", role) and (not entity_type or entity_type.lower() in ("client", "clientcompany")):
        seen_uids = {item.id for item in out if item.collection == "users"}
        for doc in _find_docs(db, "clientcompanies", {"name": pat}, limit, role, client_id, company_id):
            cid = str(doc.get("clientId", ""))
            if cid and cid not in seen_uids:
                out.append(EntityIdentity(id=str(doc["_id"]), name=str(doc.get("name", "")), type="ClientCompany", collection="clientcompanies", subtitle=f"Client ID: {cid}", matched_on="name"))

    if _can_read("leads", role) and (not entity_type or entity_type.lower() == "lead"):
        for doc in _find_docs(db, "leads", {"$or": [{"name": pat}, {"company": pat}]}, 5, role, client_id, company_id):
            out.append(EntityIdentity(id=str(doc["_id"]), name=str(doc.get("name", "")), type="Lead", collection="leads", code=doc.get("leadCode"), subtitle=str(doc.get("company") or doc.get("status") or ""), matched_on="name"))
    return out


def _resolve_self_client(
    db: Database[dict[str, Any]],
    identifier: str,
    client_id: str | None,
    company_id: str | None,
) -> tuple[EntityIdentity | None, bool, list[EntityIdentity]]:
    """Allows a client user to resolve strictly their own client_id record and nothing else."""
    if not client_id:
        return None, False, []
    cl_clean = client_id.strip()
    id_clean = identifier.strip()
    oid_val: Any = ObjectId(cl_clean) if len(cl_clean) == 24 else cl_clean
    docs = _find_docs(db, "users", {"_id": oid_val, "role": "client"}, 1, "client", cl_clean, company_id)
    if not docs:
        return None, False, []
    ident = _to_user_identity(docs[0], "_id")
    if id_clean.lower() not in (ident.id.lower(), ident.name.lower(), str(docs[0].get("email", "")).lower()):
        return None, False, []
    return ident, False, [ident]


def search_entity(
    query: str,
    role: str,
    entity_type: str | None = None,
    limit: int = 15,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> list[EntityIdentity]:
    """Searches authorized MongoDB collections enforcing role, client_id, and company_id boundaries."""
    from app.config import settings  # noqa: PLC0415

    if role.lower() not in ROLE_ACCESS or role.lower() == "client":
        return []
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return []
    if db is None:
        from app.rag.entity_lookup import _get_read_db  # noqa: PLC0415

        db = _get_read_db()
    if db is None:
        return []

    raw = query.strip()
    if not raw:
        return []
    hits = _search_codes_and_contacts(db, raw, role, entity_type, client_id=client_id, company_id=company_id)
    term = _clean_search_phrase(raw) or raw
    if not hits and len(term) >= 2:
        hits = _search_named_collections(db, term, role, entity_type, limit, client_id=client_id, company_id=company_id)

    deduped: list[EntityIdentity] = []
    seen: set[tuple[str, str]] = set()
    for item in hits:
        key = (item.collection, item.id)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped[:limit]


def resolve_entity(
    identifier: str,
    entity_type: str | None = None,
    role: str = "employee",
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> tuple[EntityIdentity | None, bool, list[EntityIdentity]]:
    """Resolves a name or ID within tenant bounds or flags genuine multi-entity ambiguity."""
    from app.config import settings  # noqa: PLC0415

    if role.lower() not in ROLE_ACCESS:
        return None, False, []
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return None, False, []
    if db is None:
        from app.rag.entity_lookup import _get_read_db  # noqa: PLC0415

        db = _get_read_db()
    if db is None:
        return None, False, []

    if role.lower() == "client":
        return _resolve_self_client(db, identifier, client_id, company_id)

    candidates = search_entity(
        identifier, role=role, entity_type=entity_type, limit=15, client_id=client_id, company_id=company_id, db=db
    )
    if not candidates:
        return None, False, []
    if len(candidates) == 1:
        return candidates[0], False, candidates

    clean = (_clean_search_phrase(identifier) or identifier).strip().lower()
    exact_matches = [
        c for c in candidates
        if c.name.strip().lower() == clean or (c.code and c.code.strip().lower() == clean) or c.id.lower() == clean
    ]
    if len(exact_matches) == 1:
        return exact_matches[0], False, exact_matches
    if len(exact_matches) > 1:
        return None, True, exact_matches
    return None, True, candidates
