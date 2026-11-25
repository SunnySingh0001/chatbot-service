"""Validated read-only entity exploration, relationship pagination, schema, and CSV/JSON export tools."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any  # Dynamic database record dictionaries

from pymongo.database import Database

from app.models.entities import (
    BatchPageResponse,
    DatabaseSchemaResponse,
    EntityDetailsResponse,
    EntityIdentity,
    RelationshipEdgeSpec,
    RetrievalCompleteness,
)
from app.rag.entity_lookup import _get_read_db
from app.rag.entity_pagination import (
    _sanitize_record,
    build_batch_page_response,
    count_related_records as _count_rel_impl,
    get_related_records as _get_rel_impl,
    verify_completeness,
)
from app.rag.entity_resolver import resolve_entity as _resolve_impl, search_entity as _search_impl
from app.rag.relationship_engine import (
    _load_source_doc,
    discover_relationships as _discover_rel_impl,
    explore_relationships as _explore_rel_impl,
)
from app.rag.retriever import ROLE_ACCESS, RetrievedChunk
from app.rag.schema_discovery import (
    CONFIGURED_RELATIONSHIPS,
    discover_database_schema as _discover_schema_impl,
)

_logger = logging.getLogger("chatbot.rag.entity_tools")
MAX_EXPORT_RECORDS: int = 5000

_EXPORT_HINTS: tuple[str, ...] = ("export csv", "export json", "download csv", "download json", "csv export", "json export", "export data")
_SCHEMA_HINTS: tuple[str, ...] = (
    "database schema", "discover schema", "show schema", "collections and relationships",
    "all collections schema", "schema discovery", "db schema",
)
_RELATION_MAP_HINTS: tuple[str, ...] = (
    "everywhere it is mapped", "where is it mapped", "show everywhere", "kaha kaha",
    "jaha jaha", "mapped everywhere", "show all mappings", "all mappings", "all relationships",
    "relationship graph", "relationship tree", "complete mapping", "mapping paths",
)
_EXPLORATION_PHRASES: tuple[str, ...] = (
    "complete information about", "complete info about", "ke bare me", "ke baare me",
    "details of ", "details about ", "who is ", "profile of ", "about ",
)


def search_entity(
    query: str,
    role: str,
    entity_type: str | None = None,
    limit: int = 15,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> list[EntityIdentity]:
    """Searches accessible collections for entities matching a name, business code, or ID."""
    return _search_impl(
        query=query, role=role, entity_type=entity_type, limit=limit,
        client_id=client_id, company_id=company_id, db=db,
    )


def resolve_entity(
    identifier: str,
    role: str,
    entity_type: str | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> tuple[EntityIdentity | None, bool, list[EntityIdentity]]:
    """Resolves an identifier to one canonical EntityIdentity or returns ambiguous candidates."""
    return _resolve_impl(
        identifier=identifier, entity_type=entity_type, role=role,
        client_id=client_id, company_id=company_id, db=db,
    )


def discover_database_schema(
    role: str,
    collection_filter: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> DatabaseSchemaResponse:
    """Inspects approved MongoDB collections, indexes, field types, and relationship mappings for role."""
    return _discover_schema_impl(role=role, db=db, collection_filter=collection_filter)


def discover_relationships(
    source_collection: str,
    entity_type: str | None = None,
    role: str = "employee",
    max_depth: int = 2,
) -> list[tuple[int, list[str], RelationshipEdgeSpec]]:
    """Computes reachable multi-hop relationship edges up to max_depth with cycle prevention."""
    return _discover_rel_impl(source_collection=source_collection, entity_type=entity_type, role=role, max_depth=max_depth)


def explore_relationships(
    identifier: str,
    role: str,
    entity_type: str | None = None,
    max_depth: int = 2,
    batch_size: int = 20,
    include_empty: bool = False,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> EntityDetailsResponse:
    """Resolves entity and executes cycle-safe direct/indirect relationship traversal."""
    return get_entity_details(
        identifier=identifier,
        role=role,
        entity_type=entity_type,
        max_depth=max_depth,
        batch_size=batch_size,
        include_empty=include_empty,
        client_id=client_id,
        company_id=company_id,
        db=db,
    )


def get_entity_details(
    identifier: str,
    role: str,
    entity_type: str | None = None,
    max_depth: int = 2,
    batch_size: int = 20,
    include_empty: bool = False,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> EntityDetailsResponse:
    """Returns full structured entity overview, attributes, relationships, and completeness state."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None or role.lower() not in ROLE_ACCESS or role.lower() == "client":
        return EntityDetailsResponse(retrieval=verify_completeness([]))

    resolved, is_ambiguous, candidates = resolve_entity(
        identifier, role=role, entity_type=entity_type,
        client_id=client_id, company_id=company_id, db=active_db,
    )
    if is_ambiguous:
        return EntityDetailsResponse(
            ambiguous=True,
            candidates=candidates,
            retrieval=RetrievalCompleteness(
                complete=False,
                total_records=0,
                retrieved_records=0,
                total_relationships=0,
                complete_relationships=0,
                warnings=[f"Multiple ({len(candidates)}) matching entities found; clarification required."],
            ),
        )
    if resolved is None:
        return EntityDetailsResponse(retrieval=verify_completeness([]))

    _logger.info("Audit entity exploration role=%s collection=%s id=%s", role, resolved.collection, resolved.id)
    return _explore_rel_impl(
        active_db,
        resolved,
        role=role,
        max_depth=max_depth,
        batch_size=batch_size,
        include_empty=include_empty,
        client_id=client_id,
        company_id=company_id,
    )


def _find_edge_by_name(source_collection: str, relation_name: str) -> RelationshipEdgeSpec | None:
    """Finds configured edge matching relation_name cleanly."""
    for edge in CONFIGURED_RELATIONSHIPS:
        if edge.relation_name == relation_name:
            return edge
    for edge in CONFIGURED_RELATIONSHIPS:
        if edge.source == source_collection and edge.target == relation_name:
            return edge
    return None


def count_related_records(
    identifier: str,
    relation_name: str,
    role: str,
    entity_type: str | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> int:
    """Counts total related records for an entity-relation pair under RBAC and tenant permissions."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None or role.lower() == "client":
        return 0
    resolved, is_ambiguous, _ = resolve_entity(
        identifier, role=role, entity_type=entity_type,
        client_id=client_id, company_id=company_id, db=active_db,
    )
    if resolved is None or is_ambiguous:
        return 0
    edge = _find_edge_by_name(resolved.collection, relation_name)
    root_doc = _load_source_doc(
        active_db, resolved, role=role, client_id=client_id, company_id=company_id
    ) if edge else None
    if edge is None or root_doc is None:
        return 0
    return _count_rel_impl(active_db, edge, [root_doc], role, client_id=client_id, company_id=company_id)


def get_related_records(
    identifier: str,
    relation_name: str,
    role: str,
    entity_type: str | None = None,
    offset: int = 0,
    cursor: str | None = None,
    batch_size: int = 25,
    search_filter: str | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> BatchPageResponse | None:
    """Fetches a stably sorted paginated batch of related records for a resolved entity."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None or role.lower() == "client":
        return None
    resolved, is_ambiguous, _ = resolve_entity(
        identifier, role=role, entity_type=entity_type,
        client_id=client_id, company_id=company_id, db=active_db,
    )
    if resolved is None or is_ambiguous:
        return None
    edge = _find_edge_by_name(resolved.collection, relation_name)
    root_doc = _load_source_doc(
        active_db, resolved, role=role, client_id=client_id, company_id=company_id
    ) if edge else None
    if edge is None or root_doc is None:
        return None
    parent_docs = _resolve_parent_docs_for_edge(
        active_db, resolved, root_doc, edge, role, client_id=client_id, company_id=company_id
    )
    total, recs, has_more, nxt, accessible, _ = _get_rel_impl(
        active_db, edge, parent_docs, role=role, offset=offset, batch_size=batch_size,
        search_filter=search_filter, cursor=cursor, client_id=client_id, company_id=company_id,
    )
    if not accessible:
        return None
    start_off = int(cursor) if (cursor is not None and cursor.isdigit()) else max(0, offset)
    return build_batch_page_response(resolved.id, resolved.type, edge, total, recs, start_off, batch_size, has_more, nxt)


def _resolve_parent_docs_for_edge(
    db: Database[dict[str, Any]],
    resolved: EntityIdentity,
    root_doc: dict[str, Any],
    edge: RelationshipEdgeSpec,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> list[dict[str, Any]]:
    """Supports pagination on both direct (hop-1) and indirect (hop-2 via intermediate collection) edges."""
    if edge.source == resolved.collection:
        return [root_doc]
    hop1_edges = [e for e in CONFIGURED_RELATIONSHIPS if e.source == resolved.collection and e.target == edge.source]
    collected: list[dict[str, Any]] = []
    for h1 in hop1_edges:
        _, h1_recs, _, _, acc, _ = _get_rel_impl(
            db, h1, [root_doc], role=role, offset=0, batch_size=100,
            client_id=client_id, company_id=company_id,
        )
        if acc and h1_recs:
            from app.rag.relationship_engine import _reconstruct_raw_ids  # noqa: PLC0415

            collected.extend(_reconstruct_raw_ids(h1_recs, db, h1.target))
    return collected


def get_next_batch(
    identifier: str,
    relation_name: str,
    role: str,
    cursor: str | None = None,
    offset: int = 0,
    batch_size: int = 25,
    search_filter: str | None = None,
    entity_type: str | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> BatchPageResponse | None:
    """Continues retrieval from the next batch cursor/offset for a given relationship."""
    return get_related_records(
        identifier=identifier,
        relation_name=relation_name,
        role=role,
        entity_type=entity_type,
        offset=offset,
        cursor=cursor,
        batch_size=batch_size,
        search_filter=search_filter,
        client_id=client_id,
        company_id=company_id,
        db=db,
    )


def _safe_filename(raw: str, ext: str) -> str:
    """Builds a header-injection-safe download filename."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_") or "entity"
    return f"{slug}_export.{ext}"


def _collect_all_for_export(
    db: Database[dict[str, Any]],
    details: EntityDetailsResponse,
    role: str,
    relation_name: str | None,
    max_records: int,
    client_id: str | None = None,
    company_id: str | None = None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Iterates cursor pagination across accessible relationships up to max_records."""
    cap = min(max(max_records, 1), MAX_EXPORT_RECORDS)
    rows: list[dict[str, Any]] = []
    expected_total = 0
    truncated = False
    if details.entity is None:
        return rows, 0, False
    root_doc = _load_source_doc(db, details.entity, role=role, client_id=client_id, company_id=company_id)
    if root_doc is None:
        return rows, 0, False

    for rel in details.relationships:
        if not rel.accessible:
            continue
        if relation_name and rel.relation_name != relation_name and rel.target != relation_name:
            continue
        expected_total += rel.total_records
        edge = _find_edge_by_name(rel.source, rel.relation_name)
        if edge is None:
            continue
        parent_docs = _resolve_parent_docs_for_edge(
            db, details.entity, root_doc, edge, role, client_id=client_id, company_id=company_id
        )
        offset = 0
        while len(rows) < cap:
            remaining = min(100, cap - len(rows))
            total, batch, has_more, _, acc, _ = _get_rel_impl(
                db, edge, parent_docs, role=role, offset=offset, batch_size=remaining,
                client_id=client_id, company_id=company_id,
            )
            if not acc or not batch:
                break
            for rec in batch:
                rows.append({"relation": rel.relation_name, "collection": rel.target, "hop": rel.hop, "path": " -> ".join(rel.path), **rec})
            offset += len(batch)
            if not has_more or offset >= total:
                break
            if len(rows) >= cap:
                truncated = True
                break
        if len(rows) >= cap and expected_total > len(rows):
            truncated = True
    return rows, expected_total, truncated


def export_entity_data(
    identifier: str,
    role: str,
    fmt: str = "json",
    relation_name: str | None = None,
    max_records: int = 2000,
    entity_type: str | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> tuple[bytes, str, str]:
    """Exports accessible entity attributes and mapped records as UTF-8 CSV or structured JSON."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None or role.lower() not in ROLE_ACCESS or role.lower() == "client":
        raise PermissionError("Entity export is restricted for this role or database is unavailable.")

    details = get_entity_details(
        identifier, role=role, entity_type=entity_type, max_depth=2, batch_size=20,
        client_id=client_id, company_id=company_id, db=active_db,
    )
    if details.entity is None:
        raise ValueError(f"Could not uniquely resolve entity for export: {identifier!r}")

    records, expected_total, truncated = _collect_all_for_export(
        active_db, details, role=role, relation_name=relation_name,
        max_records=max_records, client_id=client_id, company_id=company_id,
    )
    export_complete = (not truncated) and (len(records) >= expected_total) and (len(details.inaccessible_relationships) == 0 or relation_name is not None)
    fmt_norm = fmt.strip().lower()
    if fmt_norm == "csv":
        buf = io.StringIO()
        base_cols = ["relation", "collection", "hop", "path", "_id", "name", "title", "taskId", "invoiceNumber", "leadCode", "ticketCode", "email", "marketplace", "plan", "stage", "status", "amount", "manager", "createdAt", "details_json"]
        writer = csv.DictWriter(buf, fieldnames=base_cols, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            flat = {col: row.get(col, "") for col in base_cols if col != "details_json"}
            extra = {k: v for k, v in row.items() if k not in base_cols}
            flat["details_json"] = json.dumps(_sanitize_record(extra), default=str, ensure_ascii=False)
            writer.writerow(flat)
        csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
        return csv_bytes, "text/csv; charset=utf-8", _safe_filename(details.entity.name, "csv")

    payload = {
        "entity": details.entity.model_dump(),
        "overview": details.overview,
        "attributes": details.attributes,
        "relationship_tree": details.relationship_tree,
        "completeness": {
            "complete": export_complete,
            "exported_records": len(records),
            "total_accessible_records": expected_total,
            "truncated_at_limit": truncated,
            "inaccessible_relationships": details.inaccessible_relationships,
        },
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", _safe_filename(details.entity.name, "json")


def _format_disambiguation_chunk(query: str, candidates: list[EntityIdentity]) -> list[RetrievedChunk]:
    """Builds a high-priority disambiguation context chunk asking user to clarify ambiguous matches."""
    lines = [
        f"### Multiple Entities Matched Query: `{query}` — Ask User to Clarify Instead of Guessing",
        "INSTRUCTIONS TO ASSISTANT: Multiple distinct database records match this name/term. "
        "Do NOT guess one. Politely list all candidates below and ask the user which exact one they want (or ask for its ID/code):",
    ]
    for idx, c in enumerate(candidates, start=1):
        code_s = f" | Code: `{c.code}`" if c.code else ""
        sub_s = f" | Details: {c.subtitle}" if c.subtitle else ""
        lines.append(f"{idx}. **{c.name}** — Type: `{c.type}` (`{c.collection}`){code_s}{sub_s} | ID: `{c.id}`")
    return [RetrievedChunk(text="\n".join(lines), source=f"mongo:{candidates[0].collection}", department="operations", access_level="employee", score=1.0, chunk_index=0)]


def _format_schema_chunks(schema: DatabaseSchemaResponse) -> list[RetrievedChunk]:
    """Formats dynamic database schema overview and accessible relationship map as RetrievedChunks."""
    lines = [
        f"# Dynamic Database Schema Discovery (`{schema.database}`, Role: `{schema.role}`)",
        f"- **Accessible Collections**: {schema.total_collections} | **Sensitive Excluded**: {schema.excluded_sensitive_count}",
        "### Accessible Collections & Indexes:",
    ]
    for c in schema.collections:
        ref_fields = [f"{f.name} -> {f.inferred_target}" for f in c.fields if f.is_reference and f.inferred_target]
        ref_s = f" | Refs: {', '.join(ref_fields)}" if ref_fields else ""
        lines.append(f"- **`mongo:{c.collection}`** ({c.document_count} docs | access: `{c.access_level}` | indexes: {', '.join(c.indexes[:5])}){ref_s}")
    lines.append("\n### Discovered Relationship Mappings:")
    for e in schema.relationships[:40]:
        lines.append(f"- `{e.source}.{e.source_field}` -> `{e.target}.{e.target_field}` (`{e.relationship_type}`): {e.description}")
    return [RetrievedChunk(text="\n".join(lines), source="mongo:schema_discovery", department="operations", access_level="employee", score=1.0, chunk_index=0)]


def format_relationship_discovery_chunks(details: EntityDetailsResponse) -> list[RetrievedChunk]:
    """Converts EntityDetailsResponse into structured context chunks for LLM explanation."""
    if details.ambiguous and details.candidates:
        return _format_disambiguation_chunk(details.candidates[0].name, details.candidates)
    if details.entity is None:
        return []
    ent = details.entity
    chunks: list[RetrievedChunk] = []
    tree_lines = [
        f"# Entity Intelligence & Relationship Discovery: {ent.name} ({ent.type} | ID: `{ent.id}`)",
        f"- **Primary Collection**: `mongo:{ent.collection}` | **Database Locations**: {', '.join(f'`mongo:{loc}`' for loc in details.database_locations)}",
        f"- **Completeness Status**: `complete={details.retrieval.complete}` ({details.retrieval.retrieved_records} loaded of {details.retrieval.total_records} total accessible records)",
        f"- **Export Endpoints**: CSV `/entities/export?identifier={ent.id}&fmt=csv` | JSON `/entities/export?identifier={ent.id}&fmt=json`",
        "\n### Relationship Mapping Tree:\n```text",
        details.relationship_tree,
        "```",
        "\n### Mapping Summary (Source -> Field -> Target -> Counts):",
        "| Hop | Relation | Path | Mapping Fields | Type | Total | Loaded | Has More | Access |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in details.relationships:
        access_s = "Accessible" if r.accessible else f"Restricted ({r.restriction_reason})"
        tree_lines.append(
            f"| {r.hop} | {r.relation_name} | `{' -> '.join(r.path)}` | `{r.mapping_field}` | {r.relationship_type} | {r.total_records} | {r.retrieved_records} | {r.has_more} | {access_s} |"
        )
    if details.retrieval.warnings:
        tree_lines.append("\n### Completeness & Access Notices:")
        tree_lines.extend(f"- {w}" for w in details.retrieval.warnings)
    chunks.append(RetrievedChunk(text="\n".join(tree_lines), source=f"mongo:{ent.collection}", department="operations", access_level="employee", score=1.0, chunk_index=0))

    for idx, r in enumerate(details.relationships, start=1):
        if not r.accessible or not r.records:
            continue
        rec_lines = [f"### Mapped Records: `{r.relation_name}` (`mongo:{r.target}`, Hop {r.hop}, Showing {r.retrieved_records} of {r.total_records}, has_more={r.has_more})"]
        for item in r.records[:20]:
            label = item.get("name") or item.get("title") or item.get("invoiceNumber") or item.get("taskId") or item.get("leadCode") or item.get("_id")
            compact = ", ".join(f"{k}={v}" for k, v in item.items() if k not in ("description", "updates", "messages", "evidence") and v not in (None, "", []) )[:240]
            rec_lines.append(f"- **{label}**: {compact}")
        chunks.append(RetrievedChunk(text="\n".join(rec_lines), source=f"mongo:{r.target}", department="operations", access_level="employee", score=1.0, chunk_index=idx))
    return chunks


def run_entity_discovery_for_chat(
    query: str,
    role: str,
    original_query: str | None = None,
    history: list[dict[str, str]] | None = None,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> list[RetrievedChunk]:
    """Routes schema discovery, ambiguity clarification, and relationship map queries safely."""
    del history
    if role.lower() not in ROLE_ACCESS or role.lower() == "client":
        return []
    raw_q = original_query or query or ""
    q_check = raw_q.lower()
    is_schema_q = any(h in q_check for h in _SCHEMA_HINTS)
    from app.rag.entity_resolver import _clean_search_phrase  # noqa: PLC0415
    from app.rag.entity_lookup import _PLURAL_FILTER_HINTS  # noqa: PLC0415

    if not is_schema_q and any(p in q_check for p in _PLURAL_FILTER_HINTS):
        return []

    is_explicit_map = any(h in q_check for h in _RELATION_MAP_HINTS) or any(h in q_check for h in _EXPORT_HINTS)
    is_explore = is_explicit_map or any(h in q_check for h in _EXPLORATION_PHRASES)
    if not is_schema_q and not is_explore:
        return []

    active_db = db if db is not None else _get_read_db()
    if active_db is None:
        return []
    if is_schema_q:
        return _format_schema_chunks(discover_database_schema(role=role, db=active_db))

    details = get_entity_details(
        raw_q, role=role, max_depth=2, batch_size=20,
        client_id=client_id, company_id=company_id, db=active_db,
    )
    if details.ambiguous and details.candidates:
        return _format_disambiguation_chunk(_clean_search_phrase(raw_q) or raw_q, details.candidates)

    if details.entity is not None:
        # Catalog/hierarchical entities (AccountType, Department, PricingPlan) or explicit mapping queries return full graph + counts
        if is_explicit_map or details.entity.type in ("AccountType", "Department", "PricingPlan", "PlatformPlan", "Team"):
            return format_relationship_discovery_chunks(details)
        # For individual entities (Client, Employee, Task, Invoice, Lead, Ticket), prepend mapping tree + completeness onto deep 360 records
        from app.rag.entity_lookup import lookup_entity_360  # noqa: PLC0415

        deep_chunks = lookup_entity_360(
            query, role=role, original_query=original_query,
            client_id=client_id, company_id=company_id, db=active_db,
        )
        graph_chunks = format_relationship_discovery_chunks(details)
        if deep_chunks and graph_chunks:
            return [graph_chunks[0], *deep_chunks]
        return graph_chunks or deep_chunks
    return []
