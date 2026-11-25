"""Reusable multi-hop relationship discovery, cycle-safe traversal, and path explanation engine."""
from __future__ import annotations

import logging
from typing import Any  # Dynamic MongoDB documents during graph traversal

from bson import ObjectId
from pymongo.database import Database

from app.models.entities import (
    EntityDetailsResponse,
    EntityIdentity,
    RelationshipEdgeSpec,
    RelationshipResult,
)
from app.rag.entity_pagination import (
    _sanitize_record,
    get_related_records,
    verify_completeness,
)
from app.rag.retriever import ROLE_ACCESS
from app.rag.schema_discovery import CONFIGURED_RELATIONSHIPS

_logger = logging.getLogger("chatbot.rag.relationship_engine")
MAX_TRAVERSAL_DEPTH: int = 3


def discover_relationships(
    source_collection: str,
    entity_type: str | None = None,
    role: str = "super-admin",
    max_depth: int = 2,
) -> list[tuple[int, list[str], RelationshipEdgeSpec]]:
    """Discovers reachable direct and indirect relationship edges up to max_depth with cycle protection."""
    clamped_depth = min(max(max_depth, 1), MAX_TRAVERSAL_DEPTH)
    prefix_filter = _entity_edge_prefix(source_collection, entity_type)
    discovered: list[tuple[int, list[str], RelationshipEdgeSpec]] = []
    frontier: list[tuple[str, list[str]]] = [(source_collection, [source_collection])]
    visited_edges: set[tuple[str, str, str]] = set()
    visited_cols: set[str] = {source_collection}

    for hop in range(1, clamped_depth + 1):
        next_frontier: list[tuple[str, list[str]]] = []
        for col_name, path in frontier:
            for edge in CONFIGURED_RELATIONSHIPS:
                if edge.source != col_name:
                    continue
                if hop == 1 and prefix_filter and not edge.relation_name.startswith(prefix_filter):
                    continue
                sig = (edge.source, edge.relation_name, edge.target)
                if sig in visited_edges:
                    continue
                visited_edges.add(sig)
                new_path = [*path, edge.target]
                discovered.append((hop, new_path, edge))
                if edge.target not in visited_cols and hop < clamped_depth:
                    visited_cols.add(edge.target)
                    next_frontier.append((edge.target, new_path))
        frontier = next_frontier
        if not frontier:
            break
    del role
    return discovered


def _entity_edge_prefix(collection: str, entity_type: str | None) -> tuple[str, ...] | None:
    """Selects relevant edge name prefixes when users collection splits Client vs Employee semantics."""
    if collection != "users" or not entity_type:
        return None
    if entity_type.lower() == "client":
        return ("client_",)
    if entity_type.lower() == "employee":
        return ("emp_",)
    return None


def _reconstruct_raw_ids(docs: list[dict[str, Any]], db: Database[dict[str, Any]], col: str) -> list[dict[str, Any]]:
    """Rehydrates lightweight BSON records by string _id so ObjectId joins match cleanly in MongoDB."""
    raw_ids: list[Any] = []
    for d in docs:
        did = d.get("_id")
        if isinstance(did, str) and len(did) == 24:
            try:
                raw_ids.append(ObjectId(did))
                continue
            except Exception:
                pass
        if did is not None:
            raw_ids.append(did)
    if not raw_ids:
        return []
    try:
        return list(db[col].find({"_id": {"$in": raw_ids[:200]}}))
    except Exception:
        return []


def build_relationship_tree_ascii(
    entity: EntityIdentity,
    relationships: list[RelationshipResult],
) -> str:
    """Formats a clear ASCII hierarchy showing direct and indirect database mapping paths."""
    lines = [f"{entity.name} ({entity.type} | Collection: {entity.collection} | ID: {entity.id})"]
    direct = [r for r in relationships if r.hop == 1 and (not r.accessible or r.total_records > 0)]
    indirect = [r for r in relationships if r.hop > 1 and (not r.accessible or r.total_records > 0)]

    if not direct and not indirect:
        lines.append("|")
        lines.append("+--- (No linked records found in accessible collections)")
        return "\n".join(lines)

    for rel in direct:
        status = f"Total: {rel.total_records} | Loaded: {rel.retrieved_records}" if rel.accessible else f"Restricted ({rel.restriction_reason})"
        lines.append("|")
        lines.append(f"+--- {rel.target} [{rel.relationship_type} | {rel.mapping_field}] ({status})")
        children = [ind for ind in indirect if len(ind.path) >= 3 and ind.path[1] == rel.target]
        for child in children:
            c_status = f"Total: {child.total_records} | Loaded: {child.retrieved_records}" if child.accessible else f"Restricted ({child.restriction_reason})"
            path_str = " -> ".join(child.path)
            lines.append(f"|       |")
            lines.append(f"|       +--- {child.target} [indirect hop-{child.hop}: {path_str} | {child.mapping_field}] ({c_status})")
    return "\n".join(lines)


def _load_source_doc(
    db: Database[dict[str, Any]],
    identity: EntityIdentity,
    role: str = "employee",
    client_id: str | None = None,
    company_id: str | None = None,
) -> dict[str, Any] | None:
    """Loads primary MongoDB document enforcing tenant company_id and client_id boundaries."""
    from app.rag.entity_pagination import build_tenant_mongo_filter  # noqa: PLC0415

    try:
        key: Any = ObjectId(identity.id) if len(identity.id) == 24 else identity.id
    except Exception:
        key = identity.id
    filt = build_tenant_mongo_filter(
        {"_id": key}, role=role, client_id=client_id, company_id=company_id, collection_name=identity.collection
    )
    if filt is None:
        return None
    doc = db[identity.collection].find_one(filt)
    if doc is None and key != identity.id:
        fallback_filt = build_tenant_mongo_filter(
            {"_id": identity.id}, role=role, client_id=client_id, company_id=company_id, collection_name=identity.collection
        )
        if fallback_filt is not None:
            doc = db[identity.collection].find_one(fallback_filt)
    return doc


def _build_overview(
    entity: EntityIdentity,
    clean_attrs: dict[str, Any],
    relationships: list[RelationshipResult],
) -> dict[str, Any]:
    """Synthesizes concise overview metrics and connected collection summary."""
    active_rels = [r for r in relationships if r.accessible and r.total_records > 0]
    return {
        "name": entity.name,
        "type": entity.type,
        "collection": entity.collection,
        "id": entity.id,
        "code": entity.code,
        "status": clean_attrs.get("status"),
        "connected_collections": sorted({r.target for r in active_rels}),
        "direct_mappings_with_data": sum(1 for r in active_rels if r.hop == 1),
        "indirect_mappings_with_data": sum(1 for r in active_rels if r.hop > 1),
    }


def _traverse_single_edge(
    db: Database[dict[str, Any]],
    hop: int,
    path: list[str],
    edge: RelationshipEdgeSpec,
    parent_docs: list[dict[str, Any]],
    role: str,
    batch_size: int,
    max_depth: int,
    visited_nodes: set[tuple[str, str]],
    docs_by_collection: dict[str, list[dict[str, Any]]],
    client_id: str | None = None,
    company_id: str | None = None,
) -> RelationshipResult:
    """Evaluates a single graph edge with RBAC, tenant scoping, and cycle-safe child collection."""
    total, recs, has_more, nxt, accessible, reason = get_related_records(
        db, edge, parent_docs, role=role, offset=0, batch_size=batch_size, client_id=client_id, company_id=company_id
    )
    mapping_label = f"{edge.source}.{edge.source_field} -> {edge.target}.{edge.target_field}"
    if accessible and recs and hop < min(max_depth, MAX_TRAVERSAL_DEPTH):
        raw_children = _reconstruct_raw_ids(recs, db, edge.target)
        new_children = [
            d for d in raw_children
            if (edge.target, str(d.get("_id", ""))) not in visited_nodes
        ]
        for d in new_children:
            visited_nodes.add((edge.target, str(d.get("_id", ""))))
        if new_children:
            docs_by_collection.setdefault(edge.target, []).extend(new_children)
    return RelationshipResult(
        relation_name=edge.relation_name,
        source=edge.source,
        source_field=edge.source_field,
        target=edge.target,
        target_field=edge.target_field,
        relationship_type=edge.relationship_type,
        hop=hop,
        path=path,
        mapping_field=mapping_label,
        total_records=total,
        retrieved_records=len(recs),
        has_more=has_more,
        next_cursor=nxt,
        accessible=accessible,
        restriction_reason=reason,
        records=recs,
    )


def explore_relationships(
    db: Database[dict[str, Any]],
    entity: EntityIdentity,
    role: str,
    max_depth: int = 2,
    batch_size: int = 20,
    include_empty: bool = False,
    client_id: str | None = None,
    company_id: str | None = None,
) -> EntityDetailsResponse:
    """Executes cycle-safe multi-hop relationship traversal respecting role and tenant isolation."""
    allowed = ROLE_ACCESS.get(role.lower(), [])
    if not allowed or (role.lower() == "client" and not client_id):
        return EntityDetailsResponse(entity=entity, ambiguous=False, retrieval=verify_completeness([]))

    root_doc = _load_source_doc(db, entity, role=role, client_id=client_id, company_id=company_id)
    if root_doc is None:
        return EntityDetailsResponse(entity=None, retrieval=verify_completeness([]))

    clean_attrs = _sanitize_record(root_doc)
    plan = discover_relationships(entity.collection, entity.type, role=role, max_depth=max_depth)
    docs_by_collection: dict[str, list[dict[str, Any]]] = {entity.collection: [root_doc]}
    visited_nodes: set[tuple[str, str]] = {(entity.collection, str(root_doc.get("_id", "")))}
    results: list[RelationshipResult] = []
    locations: list[str] = [entity.collection]

    for hop, path, edge in plan:
        parent_docs = docs_by_collection.get(edge.source, [])
        if not parent_docs:
            continue
        rel_res = _traverse_single_edge(
            db, hop, path, edge, parent_docs, role, batch_size, max_depth,
            visited_nodes, docs_by_collection, client_id=client_id, company_id=company_id,
        )
        if not rel_res.accessible or rel_res.total_records > 0 or include_empty:
            results.append(rel_res)
        if rel_res.accessible and rel_res.total_records > 0 and edge.target not in locations:
            locations.append(edge.target)

    completeness = verify_completeness(results)
    return EntityDetailsResponse(
        entity=entity,
        ambiguous=False,
        overview=_build_overview(entity, clean_attrs, results),
        attributes=clean_attrs,
        database_locations=locations,
        relationship_tree=build_relationship_tree_ascii(entity, results),
        relationships=results,
        inaccessible_relationships=completeness.inaccessible_relationships,
        retrieval=completeness,
    )
