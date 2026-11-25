"""Stable cursor/offset pagination, duplicate detection, and completeness verification for entity records."""
from __future__ import annotations

import logging
from typing import Any  # Dynamic MongoDB documents across approved collections

from bson import ObjectId
from pymongo.database import Database

from app.config import settings
from app.models.entities import (
    BatchPageResponse,
    RelationshipEdgeSpec,
    RelationshipResult,
    RetrievalCompleteness,
)
from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.mongo_serializer import is_sensitive_field, normalize_bson_value
from app.rag.retriever import ROLE_ACCESS

_logger = logging.getLogger("chatbot.rag.entity_pagination")
MAX_BATCH_SIZE: int = 100
_MAX_TOTAL_EXACT_COUNT: int = 100000
_CLIENT_SELF_COLLECTIONS: frozenset[str] = frozenset({
    "users", "clientcompanies", "clientaccounts", "subscriptionevents", "supporttickets",
})


def _tenant_id_variants(raw_id: str) -> list[Any]:  # String + optional ObjectId
    """Returns string and BSON ObjectId forms when raw_id is 24-hex valid."""
    variants: list[Any] = [raw_id]
    if len(raw_id) == 24:
        try:
            variants.append(ObjectId(raw_id))
        except Exception:
            pass
    return variants


def build_tenant_mongo_filter(
    base_filter: dict[str, Any],
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    collection_name: str = "",
) -> dict[str, Any] | None:
    """Applies strict company_id and client_id isolation predicates inside MongoDB queries."""
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return None
    clauses: list[dict[str, Any]] = [base_filter] if base_filter else []
    if company_id:
        cvals = _tenant_id_variants(company_id)
        clauses.append({
            "$or": [
                {"companyId": {"$in": cvals}},
                {"company_id": {"$in": cvals}},
                {"workspaceId": {"$in": cvals}},
                {"client.companyId": {"$in": cvals}},
            ]
        })
    if role.lower() == "client":
        if not client_id or (collection_name and collection_name not in _CLIENT_SELF_COLLECTIONS):
            return None
        clvals = _tenant_id_variants(client_id)
        if collection_name == "users":
            clauses.append({"_id": {"$in": clvals}, "role": "client"})
        else:
            clauses.append({
                "$or": [
                    {"clientId": {"$in": clvals}},
                    {"client_id": {"$in": clvals}},
                    {"client.id": {"$in": clvals}},
                ]
            })
    if not clauses:
        return {}
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _sanitize_record(doc: dict[str, Any]) -> dict[str, Any]:
    """Strips sensitive fields and converts BSON values cleanly for JSON/API serialization."""
    clean: dict[str, Any] = {}
    for k, v in doc.items():
        if k == "__v" or is_sensitive_field(k):
            continue
        norm = normalize_bson_value(v)
        if isinstance(norm, dict):
            clean[k] = _sanitize_record(norm)
        elif isinstance(norm, list):
            clean[k] = [
                _sanitize_record(item) if isinstance(item, dict) else normalize_bson_value(item)
                for item in norm[:50]
            ]
        else:
            clean[k] = norm
    return clean


def _coerce_target_value(source_doc: dict[str, Any], source_field: str) -> Any:
    """Extracts source join key value across top-level or nested dotted fields."""
    if source_field == "_id":
        return source_doc.get("_id")
    cur: Any = source_doc
    for part in source_field.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _build_relation_filter(
    edge: RelationshipEdgeSpec,
    source_docs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Builds a parameterized MongoDB query filter linking source values to target field."""
    values: list[Any] = []
    for doc in source_docs:
        raw = _coerce_target_value(doc, edge.source_field)
        if isinstance(raw, list):
            values.extend(v for v in raw if v not in (None, ""))
        elif raw not in (None, ""):
            values.append(raw)
    if not values:
        return None
    unique_vals = list(dict.fromkeys(values))
    if len(unique_vals) == 1:
        val = unique_vals[0]
        if edge.target_field == "marketplace" and isinstance(val, str):
            import re  # noqa: PLC0415

            return {edge.target_field: {"$regex": f"^{re.escape(val)}", "$options": "i"}}
        if edge.target_field == "clientName" and isinstance(val, str):
            import re  # noqa: PLC0415

            return {edge.target_field: {"$regex": f"^{re.escape(val)}$", "$options": "i"}}
        return {edge.target_field: val}
    return {edge.target_field: {"$in": unique_vals}}


def _apply_search_filter(
    base_filter: dict[str, Any],
    search_filter: str | None,
) -> dict[str, Any]:
    """Appends safe case-insensitive text search across common descriptive fields."""
    if not search_filter or not search_filter.strip():
        return base_filter
    import re  # noqa: PLC0415

    pat = {"$regex": re.escape(search_filter.strip()), "$options": "i"}
    search_or = {
        "$or": [
            {"name": pat}, {"title": pat}, {"subject": pat}, {"taskId": pat},
            {"leadCode": pat}, {"invoiceNumber": pat}, {"ticketCode": pat},
            {"plan": pat}, {"status": pat}, {"stage": pat}, {"content": pat},
        ]
    }
    return {"$and": [base_filter, search_or]}


def _safe_count(db: Database[dict[str, Any]], col: str, filt: dict[str, Any]) -> int:
    """Executes bounded exact record count with graceful mock fallback."""
    try:
        try:
            return min(int(db[col].count_documents(filt, maxTimeMS=2500)), _MAX_TOTAL_EXACT_COUNT)
        except TypeError:
            return len(list(db[col].find(filt)))
    except Exception:
        return 0


def _sorted_batch(
    db: Database[dict[str, Any]],
    col: str,
    filt: dict[str, Any],
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetches a deterministic, stably sorted batch with duplicate-free ObjectId ordering."""
    try:
        cur = db[col].find(filt)
        if hasattr(cur, "sort"):
            try:
                cur = cur.sort([("createdAt", -1), ("_id", 1)])
            except TypeError:
                cur = cur.sort("_id", 1)
        if hasattr(cur, "max_time_ms"):
            cur = cur.max_time_ms(3000)
        if hasattr(cur, "skip"):
            cur = cur.skip(offset).limit(limit)
            raw_docs = list(cur)
        else:
            raw_docs = list(cur)[offset : offset + limit]
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for d in raw_docs:
            did = str(d.get("_id", ""))
            if did and did in seen:
                continue
            if did:
                seen.add(did)
            out.append(d)
        return out
    except Exception as exc:
        _logger.debug("Batch query failed on %s (%s)", col, type(exc).__name__)
        return []


def count_related_records(
    db: Database[dict[str, Any]],
    edge: RelationshipEdgeSpec,
    source_docs: list[dict[str, Any]],
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> int:
    """Counts total related records for an edge after enforcing RBAC and tenant bounds."""
    spec = APPROVED_COLLECTIONS.get(edge.target)
    if spec is None or spec.access_level not in ROLE_ACCESS.get(role.lower(), []):
        return 0
    base_filt = _build_relation_filter(edge, source_docs)
    if base_filt is None:
        return 0
    filt = build_tenant_mongo_filter(
        base_filt, role=role, client_id=client_id, company_id=company_id, collection_name=edge.target
    )
    if filt is None:
        return 0
    return _safe_count(db, edge.target, filt)


def get_related_records(
    db: Database[dict[str, Any]],
    edge: RelationshipEdgeSpec,
    source_docs: list[dict[str, Any]],
    role: str,
    offset: int = 0,
    batch_size: int = 25,
    search_filter: str | None = None,
    cursor: str | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
) -> tuple[int, list[dict[str, Any]], bool, str | None, bool, str | None]:
    """Retrieves a paginated batch of RBAC- and tenant-isolated sanitized target records."""
    allowed = ROLE_ACCESS.get(role.lower(), [])
    target_spec = APPROVED_COLLECTIONS.get(edge.target)
    is_client_allowed = (
        role.lower() == "client" and bool(client_id) and edge.target in _CLIENT_SELF_COLLECTIONS
    )
    if (role.lower() == "client" and not is_client_allowed) or target_spec is None or (
        role.lower() != "client" and target_spec.access_level not in allowed
    ):
        return 0, [], False, None, False, f"Restricted to {edge.required_access_level} role"

    if cursor is not None and cursor.isdigit():
        offset = max(offset, int(cursor))
    offset = max(0, offset)
    clamped_size = min(max(batch_size, 1), MAX_BATCH_SIZE)

    base_filter = _build_relation_filter(edge, source_docs)
    if base_filter is None:
        return 0, [], False, None, True, None

    scoped_filter = build_tenant_mongo_filter(
        base_filter, role=role, client_id=client_id, company_id=company_id, collection_name=edge.target
    )
    if scoped_filter is None:
        return 0, [], False, None, False, "Tenant identity isolation constraint denied access"

    filt = _apply_search_filter(scoped_filter, search_filter)
    total = _safe_count(db, edge.target, filt)
    docs = _sorted_batch(db, edge.target, filt, offset, clamped_size)
    sanitized = [_sanitize_record(d) for d in docs]
    next_off = offset + len(sanitized)
    has_more = next_off < total and len(sanitized) > 0
    next_cursor = str(next_off) if has_more else None
    return total, sanitized, has_more, next_cursor, True, None


def verify_completeness(relationships: list[RelationshipResult]) -> RetrievalCompleteness:
    """Verifies completeness across every accessible relationship without false positives."""
    accessible = [r for r in relationships if r.accessible]
    inaccessible = [f"{r.relation_name} ({r.target}: {r.restriction_reason or 'restricted'})" for r in relationships if not r.accessible]
    partial = [
        f"{r.relation_name} ({r.retrieved_records}/{r.total_records})"
        for r in accessible
        if r.has_more or r.retrieved_records < r.total_records
    ]
    total_cnt = sum(r.total_records for r in accessible)
    retrieved_cnt = sum(r.retrieved_records for r in accessible)
    complete_rels = sum(1 for r in accessible if not r.has_more and r.retrieved_records >= r.total_records)
    warnings: list[str] = []
    if partial:
        warnings.append(f"Partial retrieval on: {', '.join(partial)}. Use next batch or CSV/JSON export for all records.")
    if inaccessible:
        warnings.append(f"Restricted mappings omitted due to RBAC: {', '.join(inaccessible)}.")
    return RetrievalCompleteness(
        complete=(len(partial) == 0),
        total_records=total_cnt,
        retrieved_records=retrieved_cnt,
        total_relationships=len(accessible),
        complete_relationships=complete_rels,
        partial_relationships=partial,
        inaccessible_relationships=inaccessible,
        warnings=warnings,
    )


def build_batch_page_response(
    entity_id: str,
    entity_type: str,
    edge: RelationshipEdgeSpec,
    total: int,
    records: list[dict[str, Any]],
    offset: int,
    batch_size: int,
    has_more: bool,
    next_cursor: str | None,
) -> BatchPageResponse:
    """Constructs a structured BatchPageResponse with exact progress label."""
    start_num = offset + 1 if records else 0
    end_num = offset + len(records)
    label = f"Records {start_num}–{end_num} of {total}" if records else f"Records 0 of {total}"
    return BatchPageResponse(
        entity_id=entity_id,
        entity_type=entity_type,
        relation_name=edge.relation_name,
        source=edge.source,
        target=edge.target,
        total_records=total,
        retrieved_records=len(records),
        offset=offset,
        batch_size=batch_size,
        has_more=has_more,
        next_cursor=next_cursor,
        batch_label=label,
        complete=(not has_more and end_num >= total),
        records=records,
    )
