"""Dynamic MongoDB schema discovery, index inspection, and validated relationship registry."""
from __future__ import annotations

import logging
import time
from typing import Any  # Pymongo dynamic BSON inspection helpers

from bson import ObjectId
from pymongo.database import Database

from app.config import settings
from app.models.entities import (
    DatabaseSchemaResponse,
    DiscoveredCollectionSchema,
    DiscoveredFieldSpec,
    RelationshipEdgeSpec,
)
from app.rag.mongo_catalog import (
    APPROVED_COLLECTIONS,
    SENSITIVE_EXCLUDED_COLLECTIONS,
    SYSTEM_EXCLUDED_COLLECTIONS,
)
from app.rag.mongo_serializer import is_sensitive_field
from app.rag.retriever import ROLE_ACCESS

_logger = logging.getLogger("chatbot.rag.schema_discovery")
_SCHEMA_TTL_SECONDS: float = 300.0
_SCHEMA_CACHE: dict[str, tuple[float, DatabaseSchemaResponse]] = {}

_NON_REFERENCE_FIELDS: frozenset[str] = frozenset({
    "name", "title", "status", "type", "stage", "plan", "email", "phone",
    "date", "createdAt", "updatedAt", "description", "notes", "country",
    "category", "priority", "source", "role", "__v",
})


def _edge(
    name: str,
    src: str,
    src_f: str,
    tgt: str,
    tgt_f: str,
    rel_type: str,
    desc: str,
    embedded: bool = False,
) -> RelationshipEdgeSpec:
    """Constructs a validated RelationshipEdgeSpec inheriting target collection access level."""
    target_spec = APPROVED_COLLECTIONS.get(tgt)
    access_lvl = target_spec.access_level if target_spec else "employee"
    return RelationshipEdgeSpec(
        relation_name=name,
        source=src,
        source_field=src_f,
        target=tgt,
        target_field=tgt_f,
        relationship_type=rel_type,
        description=desc,
        is_embedded=embedded,
        is_configured=True,
        required_access_level=access_lvl,
    )


CONFIGURED_RELATIONSHIPS: tuple[RelationshipEdgeSpec, ...] = (
    # AccountTypes / Marketplaces (e.g. Amazon, Flipkart, Dropshipping, DMAX)
    _edge("accounttype_clientaccounts", "accounttypes", "_id", "clientaccounts", "accountTypeId", "one_to_many", "Marketplace seller accounts registered under this account type"),
    _edge("accounttype_marketplace_accounts", "accounttypes", "name", "clientaccounts", "marketplace", "one_to_many", "Marketplace accounts matched by marketplace name"),
    _edge("accounttype_teams", "accounttypes", "name", "teams", "marketplace", "one_to_many", "Operational teams aligned to this marketplace"),
    _edge("accounttype_tasks", "accounttypes", "name", "tasks", "client.accountTypeName", "one_to_many", "Tasks tagged with this marketplace account type", embedded=True),
    _edge("accounttype_pricingplans", "accounttypes", "name", "pricingplans", "name", "one_to_many", "Pricing plans scoped to this marketplace name"),
    # Departments
    _edge("department_teams", "departments", "_id", "teams", "departmentId", "one_to_many", "Functional teams inside this department"),
    _edge("department_accounts", "departments", "_id", "clientaccounts", "departmentIds", "many_to_many", "Client accounts assigned to this department", embedded=True),
    _edge("department_tasks", "departments", "_id", "tasks", "departmentId", "one_to_many", "Operational tasks scoped to this department"),
    _edge("department_leads", "departments", "_id", "leads", "departmentId", "one_to_many", "Sales leads routed to this department"),
    # Clients (users where role='client')
    _edge("client_companies", "users", "_id", "clientcompanies", "clientId", "one_to_many", "Registered legal companies owned by client"),
    _edge("client_accounts", "users", "_id", "clientaccounts", "clientId", "one_to_many", "Active/historical marketplace seller accounts"),
    _edge("client_teams", "users", "teams", "teams", "_id", "many_to_many", "Assigned operational service teams", embedded=True),
    _edge("client_tasks", "users", "_id", "tasks", "client.id", "one_to_many", "Operational tasks executed for client", embedded=True),
    _edge("client_notes", "users", "_id", "notes", "clientId", "one_to_many", "Activity notes and manager logs"),
    _edge("client_sub_events", "users", "_id", "subscriptionevents", "clientId", "one_to_many", "Subscription renewal, extension, and plan change history"),
    _edge("client_tickets", "users", "_id", "supporttickets", "clientId", "one_to_many", "Support tickets and client conversation threads"),
    _edge("client_invoices", "users", "_id", "invoices", "client.id", "one_to_many", "Commercial GST invoices billed to client", embedded=True),
    _edge("client_leads", "users", "email", "leads", "email", "one_to_many", "Pre-conversion CRM leads sharing client email"),
    _edge("client_approvals", "users", "name", "approvalrequests", "clientName", "one_to_many", "Operational approval audits referencing client"),
    _edge("client_profile_updates", "users", "_id", "profileupdaterequests", "client", "one_to_many", "Client profile change requests"),
    # Employees / Staff (users where role!='client')
    _edge("emp_led_teams", "users", "_id", "teams", "leadId", "one_to_many", "Teams led by this employee"),
    _edge("emp_member_teams", "users", "_id", "teams", "memberIds", "many_to_many", "Teams where employee is an active member", embedded=True),
    _edge("emp_org_memberships", "users", "_id", "orgmemberships", "userId", "one_to_many", "Organizational team/department memberships"),
    _edge("emp_reports_to", "users", "_id", "reportingrelations", "subordinateUserId", "one_to_many", "Managers this employee reports to"),
    _edge("emp_direct_reports", "users", "_id", "reportingrelations", "managerUserId", "one_to_many", "Subordinates reporting to this manager"),
    _edge("emp_leave_balances", "users", "_id", "leavebalances", "userId", "one_to_one", "Annual leave quotas and utilization"),
    _edge("emp_managed_accounts", "users", "_id", "clientaccounts", "managerId", "one_to_many", "Client accounts managed by this employee"),
    _edge("emp_assigned_tasks", "users", "_id", "tasks", "assignee.id", "one_to_many", "Operational tasks assigned to this employee", embedded=True),
    _edge("emp_authored_notes", "users", "_id", "notes", "authorId", "one_to_many", "Client notes authored by this employee"),
    _edge("emp_assigned_leads", "users", "_id", "leads", "assignedTo", "one_to_many", "Sales leads owned by this employee"),
    _edge("emp_kpi_scores", "users", "_id", "scoresnapshots", "scopeId", "one_to_many", "Periodic KPI scorecards and rankings"),
    _edge("emp_approvals_decided", "users", "_id", "approvalrequests", "decidedBy", "one_to_many", "Approvals reviewed and decided by this employee"),
    _edge("emp_invoices", "users", "_id", "invoices", "client.id", "one_to_many", "Invoices associated with user account", embedded=True),
    # Client Companies & Client Accounts (intermediate / junction entities)
    _edge("company_client", "clientcompanies", "clientId", "users", "_id", "one_to_one", "Parent client user record"),
    _edge("company_accounts", "clientcompanies", "_id", "clientaccounts", "companyId", "one_to_many", "Marketplace accounts under this legal company"),
    _edge("account_client", "clientaccounts", "clientId", "users", "_id", "one_to_one", "Owning client profile"),
    _edge("account_company", "clientaccounts", "companyId", "clientcompanies", "_id", "one_to_one", "Mapped legal client company"),
    _edge("account_type", "clientaccounts", "accountTypeId", "accounttypes", "_id", "one_to_one", "Marketplace account type definition"),
    _edge("account_manager", "clientaccounts", "managerId", "users", "_id", "one_to_one", "Assigned account manager"),
    _edge("account_tasks", "clientaccounts", "_id", "tasks", "client.accountId", "one_to_many", "Tasks scoped to this marketplace account", embedded=True),
    _edge("account_notes", "clientaccounts", "_id", "notes", "accountId", "one_to_many", "Notes logged against this account"),
    _edge("account_sub_events", "clientaccounts", "_id", "subscriptionevents", "accountId", "one_to_many", "Subscription lifecycle events on this account"),
    # Tasks, Invoices, Leads, Tickets, Teams
    _edge("task_client", "tasks", "client.id", "users", "_id", "one_to_one", "Client associated with task", embedded=True),
    _edge("task_assignee", "tasks", "assignee.id", "users", "_id", "one_to_one", "Staff assignee responsible for task", embedded=True),
    _edge("task_approvals", "tasks", "_id", "approvalrequests", "ref.id", "one_to_many", "Completion verification approvals linked to task", embedded=True),
    _edge("invoice_client", "invoices", "client.id", "users", "_id", "one_to_one", "Billed client record", embedded=True),
    _edge("invoice_sub_events", "invoices", "_id", "subscriptionevents", "invoiceId", "one_to_many", "Subscription activations triggered by invoice"),
    _edge("lead_assignee", "leads", "assignedTo", "users", "_id", "one_to_one", "Assigned sales owner"),
    _edge("lead_approvals", "leads", "_id", "approvalrequests", "ref.id", "one_to_many", "Lead win/conversion approvals", embedded=True),
    _edge("ticket_client", "supporttickets", "clientId", "users", "_id", "one_to_one", "Client who raised support ticket"),
    _edge("team_lead", "teams", "leadId", "users", "_id", "one_to_one", "Designated team lead"),
    _edge("team_members", "teams", "memberIds", "users", "_id", "many_to_many", "Member employees belonging to team", embedded=True),
    _edge("team_scores", "teams", "_id", "scoresnapshots", "scopeId", "one_to_many", "Team KPI score snapshots"),
    # Pricing Plans
    _edge("plan_accounts", "pricingplans", "name", "clientaccounts", "plan", "one_to_many", "Client accounts subscribed to this pricing plan"),
    _edge("plan_events", "pricingplans", "name", "subscriptionevents", "planName", "one_to_many", "Subscription events recording this plan"),
)

_FIELD_TARGET_HINTS: dict[str, str] = {
    "clientId": "users",
    "userId": "users",
    "managerId": "users",
    "leadId": "users",
    "subordinateUserId": "users",
    "managerUserId": "users",
    "assignedTo": "users",
    "authorId": "users",
    "decidedBy": "users",
    "companyId": "clientcompanies",
    "accountId": "clientaccounts",
    "clientAccountId": "clientaccounts",
    "accountTypeId": "accounttypes",
    "departmentId": "departments",
    "teamId": "teams",
    "invoiceId": "invoices",
    "kpiDefinitionId": "kpidefinitions",
}


def invalidate_schema_cache() -> None:
    """Clears cached dynamic schema discovery graphs."""
    _SCHEMA_CACHE.clear()


def _infer_target_collection(field_path: str, sample_vals: list[Any]) -> str | None:
    """Infers target collection strictly for ID/reference fields, ignoring plain text attributes."""
    leaf = field_path.split(".")[-1]
    if leaf in _NON_REFERENCE_FIELDS or not any(isinstance(v, ObjectId) for v in sample_vals):
        return None
    if leaf in _FIELD_TARGET_HINTS:
        return _FIELD_TARGET_HINTS[leaf]
    if field_path in ("client.id", "assignee.id", "requestedBy", "subjectUserId"):
        return "users"
    if field_path == "client.accountId":
        return "clientaccounts"
    return None


def _collect_sample_fields(docs: list[dict[str, Any]]) -> list[DiscoveredFieldSpec]:
    """Samples document schemas safely, masking sensitive keys and detecting ObjectId references."""
    type_map: dict[str, set[str]] = {}
    samples: dict[str, list[Any]] = {}
    masked: set[str] = set()
    for doc in docs:
        for key, val in doc.items():
            if is_sensitive_field(key):
                masked.add(key)
                type_map.setdefault(key, set()).add("redacted")
                continue
            type_map.setdefault(key, set()).add(type(val).__name__)
            samples.setdefault(key, []).append(val)
            if isinstance(val, dict):
                for sk, sv in val.items():
                    path = f"{key}.{sk}"
                    if is_sensitive_field(sk):
                        masked.add(path)
                        type_map.setdefault(path, set()).add("redacted")
                        continue
                    type_map.setdefault(path, set()).add(type(sv).__name__)
                    samples.setdefault(path, []).append(sv)
    return [
        DiscoveredFieldSpec(
            name=fname,
            types=sorted(type_map[fname]),
            is_reference=_infer_target_collection(fname, samples.get(fname, [])) is not None,
            inferred_target=_infer_target_collection(fname, samples.get(fname, [])),
            is_sensitive_masked=(fname in masked),
        )
        for fname in sorted(type_map.keys())
    ]


def _safe_indexes(db: Database[dict[str, Any]], col_name: str) -> list[str]:
    """Safely lists index names for an approved collection, handling mock DBs cleanly."""
    try:
        info = db[col_name].index_information()
        return sorted(str(k) for k in info.keys())
    except Exception:
        return ["_id_"]


def _safe_count(db: Database[dict[str, Any]], col_name: str) -> int:
    """Counts collection documents safely with bounded execution time."""
    try:
        col = db[col_name]
        try:
            return int(col.count_documents({}, maxTimeMS=2500))
        except TypeError:
            return len(list(col.find({})))
    except Exception:
        return 0


def _safe_sample(db: Database[dict[str, Any]], col_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """Reads up to N documents safely for runtime schema inspection."""
    try:
        cur = db[col_name].find({})
        if hasattr(cur, "max_time_ms"):
            cur = cur.max_time_ms(2500)
        out: list[dict[str, Any]] = []
        for idx, doc in enumerate(cur):
            if idx >= limit:
                break
            if isinstance(doc, dict):
                out.append(doc)
        return out
    except Exception:
        return []


def discover_database_schema(
    role: str,
    db: Database[dict[str, Any]] | None = None,
    collection_filter: str | None = None,
) -> DatabaseSchemaResponse:
    """Dynamically discovers accessible collections, indexes, field types, and relationship edges."""
    allowed_levels = set(ROLE_ACCESS.get(role.lower(), []))
    cache_key = f"{role.lower()}::{collection_filter or ''}"
    now = time.monotonic()
    if db is None and cache_key in _SCHEMA_CACHE and (now - _SCHEMA_CACHE[cache_key][0]) < _SCHEMA_TTL_SECONDS:
        return _SCHEMA_CACHE[cache_key][1]

    if db is None:
        from app.rag.entity_lookup import _get_read_db  # noqa: PLC0415

        db = _get_read_db()

    accessible_edges = [
        e for e in CONFIGURED_RELATIONSHIPS
        if e.required_access_level in allowed_levels
        and APPROVED_COLLECTIONS.get(e.source) is not None
        and APPROVED_COLLECTIONS[e.source].access_level in allowed_levels
        and (collection_filter is None or e.source == collection_filter or e.target == collection_filter)
    ]

    collections_out: list[DiscoveredCollectionSchema] = []
    for name, spec in sorted(APPROVED_COLLECTIONS.items()):
        if spec.access_level not in allowed_levels:
            continue
        if name in SENSITIVE_EXCLUDED_COLLECTIONS or name in SYSTEM_EXCLUDED_COLLECTIONS:
            continue
        if collection_filter and name != collection_filter:
            continue
        sample_docs = _safe_sample(db, name) if db is not None else []
        fields = _collect_sample_fields(sample_docs)
        for f in fields:
            if f.is_reference and f.inferred_target:
                tgt_spec = APPROVED_COLLECTIONS.get(f.inferred_target)
                if tgt_spec and tgt_spec.access_level in allowed_levels and not any(
                    e.source == name and e.source_field == f.name and e.target == f.inferred_target
                    for e in accessible_edges
                ):
                    accessible_edges.append(
                        RelationshipEdgeSpec(
                            relation_name=f"{name}_{f.name.replace('.', '_')}_{f.inferred_target}",
                            source=name,
                            source_field=f.name,
                            target=f.inferred_target,
                            target_field="_id",
                            relationship_type="one_to_one",
                            description=f"Dynamically inferred ObjectId reference from {name}.{f.name}",
                            is_embedded=("." in f.name),
                            is_configured=False,
                            required_access_level=tgt_spec.access_level,
                        )
                    )
        cnt = _safe_count(db, name) if db is not None else 0
        idxs = _safe_indexes(db, name) if db is not None else ["_id_"]
        collections_out.append(
            DiscoveredCollectionSchema(
                collection=name,
                access_level=spec.access_level,
                department=spec.department,
                document_count=cnt,
                indexes=idxs,
                fields=fields,
                outgoing_relationships=[e for e in accessible_edges if e.source == name],
                incoming_relationships=[e for e in accessible_edges if e.target == name],
            )
        )

    resp = DatabaseSchemaResponse(
        database=settings.mongodb_db_name,
        engine="mongodb",
        role=role.lower(),
        total_collections=len(collections_out),
        excluded_sensitive_count=len(SENSITIVE_EXCLUDED_COLLECTIONS),
        collections=collections_out,
        relationships=accessible_edges,
    )
    if db is not None and not hasattr(db, "_cols"):
        _SCHEMA_CACHE[cache_key] = (now, resp)
    return resp
