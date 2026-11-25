"""Natural language query intent classification, structured plan schema, and pre-execution validation."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal  # Dynamic validated filter and aggregation maps

from pydantic import BaseModel, Field, field_validator
from pymongo.database import Database

from app.config import settings
from app.rag.entity_lookup import _get_read_db
from app.rag.llm import complete_llm
from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.retriever import ROLE_ACCESS

_logger = logging.getLogger("chatbot.rag.query_planner")
MAX_PLAN_LIMIT: int = 500

QueryIntent = Literal[
    "ENTITY_LOOKUP",
    "ENTITY_360",
    "FILTERED_LIST",
    "FINANCIAL_ANALYTICS",
    "KPI_ANALYTICS",
    "CROSS_MODULE_ANALYTICS",
    "HISTORICAL_TREND",
    "SCHEMA_DISCOVERY",
    "EXPORT_REQUEST",
    "GENERAL_RAG",
    "CLARIFICATION_REQUIRED",
]

ALLOWED_AGG_OPS: frozenset[str] = frozenset({"count", "sum", "avg", "min", "max", "ratio"})

# Safe discoverable/queryable fields per collection; blocks secrets and operator injection
COLLECTION_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "invoices": frozenset({"invoiceNumber", "status", "amount", "subTotal", "date", "dueDate", "department", "customer", "placeOfSupply", "paymentMethod", "taxRate", "cgst", "sgst", "igst"}),
    "clientaccounts": frozenset({"marketplace", "plan", "stage", "status", "amount", "discount", "isActive", "manager", "department", "departmentId", "subscriptionStart", "subscriptionEnd", "clientId", "companyId"}),
    "tasks": frozenset({"taskId", "title", "status", "priority", "assignee", "owner", "department", "dueDate", "createdAt", "updatedAt", "planForWeek"}),
    "leads": frozenset({"leadCode", "name", "company", "status", "source", "assignedTo", "departmentId", "convertedClientId", "createdAt"}),
    "users": frozenset({"name", "email", "employeeId", "role", "department", "designation", "status", "company"}),
    "departments": frozenset({"name", "code", "slug", "status", "pillar"}),
    "teams": frozenset({"name", "code", "status", "leadId", "clientCount"}),
    "kpidefinitions": frozenset({"code", "name", "category", "unit", "direction", "weight", "status"}),
    "kpitargets": frozenset({"scopeType", "scopeId", "kpiDefinitionId", "periodStart", "periodEnd", "targetValue", "approvalStatus"}),
    "kpiperformancerecords": frozenset({"kpiDefinitionId", "scopeType", "scopeId", "kpi_code", "department", "employee", "periodStart", "periodEnd", "targetValue", "actualValue", "achievementPercent", "score", "dataStatus", "isFinal"}),
    "scoresnapshots": frozenset({"scopeType", "scopeId", "periodStart", "periodEnd", "directScore", "aggregatedScore", "finalScore", "rank", "isFinal"}),
    "subscriptionevents": frozenset({"kind", "status", "planName", "amountPaise", "listPricePaise", "departmentId", "invoiceId", "paymentDate", "createdAt"}),
    "supporttickets": frozenset({"ticketCode", "subject", "status", "priority", "category", "createdAt"}),
}

_UNIVERSAL_FILTER_KEYS: frozenset[str] = frozenset({
    "status", "priority", "department", "assignee", "owner", "customer",
    "marketplace", "plan", "scope_type", "kpi_code", "employee", "stage",
    "bi_flow", "source_type", "role",
})

_BLOCKED_KEYS: frozenset[str] = frozenset({
    "password", "passwordHash", "otp", "secret", "token", "refreshToken", "accessToken", "permissions",
})


class DateRangeSpec(BaseModel):
    """Normalized temporal window extracted from natural language."""

    period: str | None = Field(default=None, description="Relative or fiscal descriptor (e.g. last_month, Q3, FY25-26)")
    start: str | None = Field(default=None, description="ISO start date string")
    end: str | None = Field(default=None, description="ISO end date string")


class AggregationSpec(BaseModel):
    """Declarative numeric aggregation step validated before execution."""

    field: str
    operation: Literal["count", "sum", "avg", "min", "max", "ratio"] = "sum"


class SortSpec(BaseModel):
    """Sort ordering specification."""

    field: str = "createdAt"
    direction: Literal["asc", "desc"] = "desc"


class StructuredQueryPlan(BaseModel):
    """Validated query execution plan produced from natural-language ERP queries."""

    intent: QueryIntent
    entities: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    date_range: DateRangeSpec | None = None
    group_by: list[str] = Field(default_factory=list)
    aggregations: list[AggregationSpec] = Field(default_factory=list)
    sort: SortSpec | None = None
    limit: int = Field(default=100, ge=1, le=MAX_PLAN_LIMIT)
    target_entity: str | None = None
    clarification_question: str | None = None
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, v: int) -> int:
        return min(max(v, 1), MAX_PLAN_LIMIT)


class PlanValidationOutcome(BaseModel):
    """Result of validating a StructuredQueryPlan against schema, RBAC, and tenant boundaries."""

    valid: bool
    authorized: bool = True
    plan: StructuredQueryPlan
    errors: list[str] = Field(default_factory=list)
    inaccessible_collections: list[str] = Field(default_factory=list)


def _contains_unsafe_operator(obj: Any) -> bool:
    """Rejects NoSQL $-operators and blocked credential field names anywhere in filter dicts."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k).strip()
            if ks.startswith("$") or ks in _BLOCKED_KEYS or _contains_unsafe_operator(v):
                return True
    elif isinstance(obj, list):
        return any(_contains_unsafe_operator(item) for item in obj)
    return False


def _extract_period_spec(q_lower: str) -> DateRangeSpec | None:
    """Extracts relative, quarter, FY, or trend windows from lowercase query text."""
    patterns = (
        ("last month", "last_month"), ("pichle mahine", "last_month"), ("pichle month", "last_month"), ("previous month", "last_month"),
        ("this month", "this_month"), ("current month", "this_month"), ("is mahine", "this_month"),
        ("last week", "last_week"), ("pichle hafte", "last_week"), ("this week", "this_week"),
        ("yesterday", "yesterday"), ("today", "today"), ("aaj", "today"),
        ("last fy", "previous_fy"), ("previous fy", "previous_fy"), ("previous financial year", "previous_fy"),
        ("current fy", "current_fy"), ("this fy", "current_fy"), ("current financial year", "current_fy"),
    )
    for phrase, token in patterns:
        if phrase in q_lower:
            return DateRangeSpec(period=token)
    if qm := re.search(r"\b(q[1-4](?:\s*(?:fy)?\s*\d{2,4}(?:\s*-\s*\d{2,4})?)?)\b", q_lower):
        return DateRangeSpec(period=qm.group(1))
    if fym := re.search(r"\b(fy[\s_-]*\d{2,4}(?:-\d{2,4})?)\b", q_lower):
        return DateRangeSpec(period=fym.group(1))
    if ym := re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?:\s+20\d{2})?\b", q_lower):
        return DateRangeSpec(period=ym.group(0))
    return None


def _extract_department_filter(q_lower: str) -> str | None:
    """Detects known canonical department filters from natural-language text."""
    dept_aliases = (
        ("ecom management", "Ecom Management"), ("ecom", "Ecom Management"),
        ("sales department", "Sales"), ("sales dept", "Sales"),
        ("dmax", "DMAX"), ("it and development", "IT and Development"), ("it & dev", "IT and Development"),
    )
    for alias, canonical in dept_aliases:
        if alias in q_lower:
            return canonical
    if re.search(r"\bsales\b", q_lower) and any(w in q_lower for w in ("invoice", "revenue", "target", "kpi", "department", "team")):
        return "Sales"
    return None


def _extract_assignee_filter(raw_query: str) -> str | None:
    """Extracts assignee or employee name after standard assignment prepositions."""
    pat = re.search(
        r"(?:assigned\s+to|owned\s+by|managed\s+by|for\s+employee|by)\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})",
        raw_query,
    )
    if pat:
        cand = pat.group(1).strip()
        if cand.lower() not in {"last month", "this month", "sales", "amazon", "flipkart"}:
            return cand
    return None


def _classify_cross_or_trend(q_lower: str) -> StructuredQueryPlan | None:
    """Matches cross-module BI and historical time-series trend requests deterministically."""
    dr = _extract_period_spec(q_lower)
    dept = _extract_department_filter(q_lower)
    filters = {"department": dept} if dept else {}

    if any(k in q_lower for k in ("trend", "over time", "month-over-month", "monthly growth", "last 6 months", "trajectory", "historical")):
        ents = ["invoices"] if any(w in q_lower for w in ("revenue", "invoice", "mrr", "subscription")) else ["kpiperformancerecords", "scoresnapshots"]
        return StructuredQueryPlan(intent="HISTORICAL_TREND", entities=ents, filters=filters, date_range=dr, group_by=["month"])

    is_lead_funnel = any(w in q_lower for w in ("lead", "leads", "funnel")) and any(w in q_lower for w in ("convert", "paid", "revenue", "client"))
    is_rev_vs_target = any(w in q_lower for w in ("actual revenue", "revenue vs", "vs target", "against target", "revenue target"))
    is_tasks_vs_kpi = any(w in q_lower for w in ("overdue task", "tasks vs", "task vs kpi", "score")) and any(w in q_lower for w in ("kpi", "score", "performance"))

    if is_lead_funnel:
        filters["bi_flow"] = "sales_funnel"
        return StructuredQueryPlan(intent="CROSS_MODULE_ANALYTICS", entities=["leads", "clientaccounts", "invoices"], filters=filters, date_range=dr)
    if is_rev_vs_target:
        filters["bi_flow"] = "revenue_vs_target"
        return StructuredQueryPlan(intent="CROSS_MODULE_ANALYTICS", entities=["invoices", "kpitargets", "kpidefinitions"], filters=filters, date_range=dr)
    if is_tasks_vs_kpi:
        filters["bi_flow"] = "delivery_vs_kpi"
        return StructuredQueryPlan(intent="CROSS_MODULE_ANALYTICS", entities=["tasks", "kpiperformancerecords", "scoresnapshots"], filters=filters, date_range=dr)
    return None


def _classify_financial_or_kpi(q_lower: str) -> StructuredQueryPlan | None:
    """Matches financial totals, GST, MRR/ARR, and KPI analytics requests."""
    dr = _extract_period_spec(q_lower)
    dept = _extract_department_filter(q_lower)
    filters: dict[str, Any] = {"department": dept} if dept else {}

    fin_terms = ("invoice", "invoices", "gst", "cgst", "sgst", "igst", "utgst", "subtotal", "mrr", "arr", "subscription revenue", "paid amount", "pending invoice", "overdue invoice", "revenue by")
    if any(t in q_lower for t in fin_terms) or (re.search(r"\b(total|sum)\b", q_lower) and "revenue" in q_lower):
        if "mrr" in q_lower or "arr" in q_lower or "subscription revenue" in q_lower:
            return StructuredQueryPlan(
                intent="FINANCIAL_ANALYTICS",
                entities=["clientaccounts", "platformplans"],
                filters=filters,
                date_range=dr,
                aggregations=[AggregationSpec(field="amount", operation="sum")],
            )
        for st in ("paid", "pending", "overdue"):
            if re.search(rf"\b{st}\b", q_lower):
                filters["status"] = st.title()
                break
        grp = ["department"] if "department" in q_lower else (["client"] if "customer" in q_lower or "client" in q_lower else [])
        return StructuredQueryPlan(
            intent="FINANCIAL_ANALYTICS",
            entities=["invoices"],
            filters=filters,
            date_range=dr,
            group_by=grp,
            aggregations=[AggregationSpec(field="amount", operation="sum")],
        )

    if any(t in q_lower for t in ("kpi", "kpis", "scorecard", "achievement", "score snapshot", "scoresnapshot", "target")):
        if "department" in q_lower:
            filters["scope_type"] = "department"
        elif "team" in q_lower:
            filters["scope_type"] = "team"
        return StructuredQueryPlan(
            intent="KPI_ANALYTICS",
            entities=["kpiperformancerecords", "kpidefinitions"],
            filters=filters,
            date_range=dr,
            aggregations=[AggregationSpec(field="achievementPercent", operation="avg")],
        )
    return None


def _classify_list_export_or_entity(raw_q: str, q_lower: str) -> StructuredQueryPlan | None:
    """Matches schema discovery, exports, filtered lists, and Entity 360/lookup requests."""
    dr = _extract_period_spec(q_lower)
    if any(h in q_lower for h in ("database schema", "discover schema", "show schema", "collections and relationships", "db schema")):
        return StructuredQueryPlan(intent="SCHEMA_DISCOVERY", entities=list(APPROVED_COLLECTIONS.keys())[:10])
    if any(h in q_lower for h in ("export ", "download csv", "download json", "download xlsx", "download pdf", "xlsx", "excel", "pdf report")):
        return StructuredQueryPlan(intent="EXPORT_REQUEST", entities=["invoices"] if "invoice" in q_lower else ["clientaccounts"], date_range=dr)

    is_list_verb = any(h in q_lower for h in ("show all ", "list all ", "find all ", "all ", "sabhi ", "saare ", "filter ", "high-priority", "high priority", "overdue ", "pending "))
    if is_list_verb and any(w in q_lower for w in ("task", "tasks")):
        filters: dict[str, Any] = {}
        if "high-priority" in q_lower or "high priority" in q_lower:
            filters["priority"] = "High"
        elif "medium" in q_lower:
            filters["priority"] = "Medium"
        for st, val in (("completed", "Completed"), ("in progress", "In Progress"), ("cancelled", "Cancelled"), ("overdue", "Overdue")):
            if st in q_lower:
                filters["status"] = val
                break
        if asg := _extract_assignee_filter(raw_q):
            filters["assignee"] = asg
        return StructuredQueryPlan(intent="FILTERED_LIST", entities=["tasks"], filters=filters, date_range=dr, sort=SortSpec(field="createdAt", direction="desc"))

    if is_list_verb and any(w in q_lower for w in ("client", "clients", "account", "leads", "lead", "ticket", "tickets")):
        col = "leads" if "lead" in q_lower else ("supporttickets" if "ticket" in q_lower else "clientaccounts")
        filters_l: dict[str, Any] = {}
        for mkt in ("amazon", "flipkart", "meesho", "shopify"):
            if mkt in q_lower:
                filters_l["marketplace"] = mkt.title()
        for pl in ("kickstart", "elite", "growth", "enterprise", "starter"):
            if pl in q_lower:
                filters_l["plan"] = pl.title()
        return StructuredQueryPlan(intent="FILTERED_LIST", entities=[col], filters=filters_l, date_range=dr)

    if any(h in q_lower for h in ("everywhere it is mapped", "all relationships", "relationship graph", "complete information about", "complete info about", "ke bare me", "ke baare me")):
        return StructuredQueryPlan(intent="ENTITY_360", entities=["users", "clientaccounts", "tasks"])
    return None


def _maybe_llm_refine_plan(raw_query: str) -> StructuredQueryPlan | None:
    """Uses centralized llm.py JSON mode as optional fallback when heuristics do not match."""
    if getattr(settings, "llm_provider", "mock") == "mock":
        return None
    sys_msg = (
        "Classify the ERP query into JSON matching StructuredQueryPlan: "
        "{intent, entities, filters, date_range:{period,start,end}|null, group_by, aggregations, sort, limit, clarification_question}. "
        "Allowed intents: ENTITY_LOOKUP, ENTITY_360, FILTERED_LIST, FINANCIAL_ANALYTICS, KPI_ANALYTICS, "
        "CROSS_MODULE_ANALYTICS, HISTORICAL_TREND, SCHEMA_DISCOVERY, EXPORT_REQUEST, GENERAL_RAG, CLARIFICATION_REQUIRED. "
        "Return JSON object only."
    )
    try:
        raw = complete_llm([{"role": "system", "content": sys_msg}, {"role": "user", "content": raw_query[:400]}], max_tokens=280)
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end <= start:
            return None
        return StructuredQueryPlan.model_validate(json.loads(raw[start : end + 1]))
    except Exception as exc:
        _logger.debug("LLM plan refinement skipped (%s)", type(exc).__name__)
        return None


def plan_natural_language_query(raw_query: str) -> StructuredQueryPlan:
    """Converts a natural-language ERP question into a typed StructuredQueryPlan."""
    clean = (raw_query or "").strip()
    if len(clean) < 3:
        return StructuredQueryPlan(intent="CLARIFICATION_REQUIRED", clarification_question="Please specify which ERP entity, metric, or report you need.")
    if re.search(r"\b(?:FISIPL[A-Z0-9]{4,}|TSK-\d+|LEAD-\d+|TCK-\d{4}-\d+|EMP-\d+)\b", clean, re.IGNORECASE):
        return StructuredQueryPlan(intent="ENTITY_LOOKUP", entities=["users", "tasks", "invoices", "leads", "supporttickets"])
    q_lower = clean.lower()
    if q_lower in ("revenue batao", "kpi batao", "show invoices", "analytics"):
        return StructuredQueryPlan(
            intent="CLARIFICATION_REQUIRED",
            clarification_question="Please clarify the date range (e.g., This Month, Last Month, Q1, FY25-26) and scope (department, customer, or company-wide).",
        )
    if matched := _classify_cross_or_trend(q_lower):
        return matched
    if matched := _classify_financial_or_kpi(q_lower):
        return matched
    if matched := _classify_list_export_or_entity(clean, q_lower):
        return matched
    if refined := _maybe_llm_refine_plan(clean):
        return refined
    return StructuredQueryPlan(intent="GENERAL_RAG")


def _validate_collection_fields(col: str, keys: list[str], db: Database[dict[str, Any]] | None) -> list[str]:
    """Validates filter/group/sort keys against static allowlist or live sample schema keys."""
    allowed = set(COLLECTION_ALLOWED_FIELDS.get(col, frozenset())) | set(_UNIVERSAL_FILTER_KEYS)
    if db is not None and col in APPROVED_COLLECTIONS:
        try:
            if sample := db[col].find_one({}, None):
                allowed |= (set(sample.keys()) - _BLOCKED_KEYS)
        except Exception:
            pass
    return [k for k in keys if k not in allowed]


def validate_query_plan(
    plan: StructuredQueryPlan,
    *,
    role: str,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> PlanValidationOutcome:
    """Validates collections, discovered fields, RBAC permissions, and tenant boundaries prior to execution."""
    errors: list[str] = []
    inaccessible: list[str] = []
    norm_role = role.lower().strip()
    if norm_role not in ROLE_ACCESS:
        return PlanValidationOutcome(valid=False, authorized=False, plan=plan, errors=[f"Invalid role: {role!r}"])
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return PlanValidationOutcome(valid=False, authorized=False, plan=plan, errors=["Verified company_id claim is required."])
    if _contains_unsafe_operator(plan.filters):
        return PlanValidationOutcome(valid=False, authorized=False, plan=plan, errors=["Unsafe filter operators or restricted fields are not permitted."])

    allowed_levels = set(ROLE_ACCESS[norm_role])
    for col in plan.entities:
        spec = APPROVED_COLLECTIONS.get(col)
        if spec is None:
            errors.append(f"Unapproved or unknown collection: {col!r}")
        elif spec.access_level not in allowed_levels:
            inaccessible.append(col)

    if inaccessible and plan.intent not in ("CROSS_MODULE_ANALYTICS",):
        return PlanValidationOutcome(
            valid=False,
            authorized=False,
            plan=plan,
            inaccessible_collections=inaccessible,
            errors=[f"Access denied to collection(s): {', '.join(inaccessible)}"],
        )

    check_keys = [k for k in plan.filters.keys()] + [a.field for a in plan.aggregations]
    if plan.sort:
        check_keys.append(plan.sort.field)
    if plan.entities and check_keys:
        primary = plan.entities[0]
        if primary in APPROVED_COLLECTIONS:
            bad = _validate_collection_fields(primary, check_keys, db)
            if bad:
                errors.append(f"Unsupported or unknown fields for {primary}: {', '.join(bad)}")

    return PlanValidationOutcome(
        valid=len(errors) == 0,
        authorized=len(inaccessible) == 0 or plan.intent == "CROSS_MODULE_ANALYTICS",
        plan=plan,
        errors=errors,
        inaccessible_collections=inaccessible,
    )
