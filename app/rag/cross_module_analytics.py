"""Pre-approved cross-module BI join engine, filtered list executor, and analytics chat router."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any  # Dynamic BSON documents

from pymongo.database import Database

from app.config import settings
from app.rag.analytics_engine import (
    AnalyticsResult,
    _as_utc_dt,
    _can_read,
    _fetch_scoped,
    compute_invoice_analytics,
    compute_kpi_analytics,
    compute_subscription_metrics,
    format_analytics_chunks,
)
from app.rag.entity_lookup import _get_read_db
from app.rag.entity_pagination import _sanitize_record, build_tenant_mongo_filter
from app.rag.fiscal_calendar import format_inr, normalize_currency_amount, parse_fiscal_date_range, quantize_inr, to_decimal
from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.query_planner import (
    StructuredQueryPlan,
    plan_natural_language_query,
    validate_query_plan,
)
from app.rag.retriever import ROLE_ACCESS, RetrievedChunk

_logger = logging.getLogger("chatbot.rag.cross_module_bi")
_ZERO = Decimal("0.00")


def _period_kwargs(plan: StructuredQueryPlan) -> dict[str, str | None]:
    """Extracts fiscal period arguments safely from StructuredQueryPlan."""
    if not plan.date_range:
        return {"period": None, "start_date": None, "end_date": None}
    return {"period": plan.date_range.period, "start_date": plan.date_range.start, "end_date": plan.date_range.end}


def compute_sales_funnel_bi(
    db: Database[dict[str, Any]],
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> AnalyticsResult:
    """Safely joins leads -> converted clients -> active subscriptions -> invoices without M:N duplication."""
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return AnalyticsResult(metric_family="cross_module_sales_funnel", authorized=False, warnings=["Missing required company_id."])
    if not _can_read("leads", role) or not _can_read("clientaccounts", role):
        return AnalyticsResult(metric_family="cross_module_sales_funnel", authorized=False, warnings=["Role lacks CRM leads/clientaccounts permission."])

    leads, l_trunc = _fetch_scoped(db, "leads", {}, role, client_id, company_id)
    accs, a_trunc = _fetch_scoped(db, "clientaccounts", {}, role, client_id, company_id)
    fp = parse_fiscal_date_range(period, start_date, end_date)

    scoped_leads = [
        ld for ld in leads
        if fp is None or ((dt := _as_utc_dt(ld.get("createdAt"))) and fp.start_utc <= dt <= fp.end_utc)
    ]
    won_leads = [ld for ld in scoped_leads if str(ld.get("status") or "").lower() == "won"]
    converted_client_ids = {str(ld["convertedClientId"]) for ld in scoped_leads if ld.get("convertedClientId")}

    # Aggregate subscription value once per distinct account (never multiplied by leads)
    matched_accs = [a for a in accs if str(a.get("clientId") or "") in converted_client_ids]
    unique_sub_inr = quantize_inr(sum((normalize_currency_amount(a.get("amount", 0), "amount").inr_amount for a in matched_accs), _ZERO))

    # Aggregate paid invoices once per converted clientId if role permits finance access
    invoiced_inr: Decimal | None = None
    paid_inv_cnt = 0
    warnings: list[str] = []
    if _can_read("invoices", role):
        invs, _ = _fetch_scoped(db, "invoices", {}, role, client_id, company_id)
        seen_inv_ids: set[str] = set()
        inv_sum = _ZERO
        for inv in invs:
            iid = str(inv.get("_id") or "")
            cid = str((inv.get("client") or {}).get("id") or "")
            if cid in converted_client_ids and iid not in seen_inv_ids and str(inv.get("status") or "").lower() == "paid":
                seen_inv_ids.add(iid)
                inv_sum += normalize_currency_amount(inv.get("amount", 0), "amount").inr_amount
                paid_inv_cnt += 1
        invoiced_inr = quantize_inr(inv_sum)
    else:
        warnings.append("Invoiced revenue omitted: caller role lacks finance/invoice permission.")

    conv_rate = quantize_inr(Decimal(len(converted_client_ids)) / Decimal(len(scoped_leads)) * Decimal("100")) if scoped_leads else _ZERO
    missing_cid = sum(1 for ld in won_leads if not ld.get("convertedClientId"))
    if missing_cid:
        warnings.append(f"{missing_cid} won lead(s) lack convertedClientId join key and were excluded from client join.")
    if l_trunc or a_trunc:
        warnings.append("Dataset hit safety scan cap.")

    summary: dict[str, Any] = {
        "total_leads": len(scoped_leads),
        "won_leads": len(won_leads),
        "converted_unique_clients": len(converted_client_ids),
        "lead_to_client_conversion_percent": str(conv_rate),
        "converted_client_accounts": len(matched_accs),
        "subscription_billed_inr": str(unique_sub_inr),
    }
    if invoiced_inr is not None:
        summary["paid_invoiced_revenue_inr"] = str(invoiced_inr)
        summary["paid_invoices_matched"] = paid_inv_cnt

    return AnalyticsResult(
        metric_family="cross_module_sales_funnel",
        currency="INR",
        unit="INR / %",
        calculation_basis="Pre-deduplicated join on convertedClientId -> unique clientaccounts & unique invoice _ids to prevent M:N multiplication.",
        date_range={"label": fp.label} if fp else {"label": "All Time"},
        source_collections=["leads", "clientaccounts"] + (["invoices"] if _can_read("invoices", role) else []),
        summary=summary,
        data_coverage={"leads_scanned": len(leads), "missing_converted_client_id_on_won": missing_cid},
        warnings=warnings,
        completeness_status="partial" if (l_trunc or a_trunc) else "complete",
    )


def compute_revenue_vs_target_bi(
    db: Database[dict[str, Any]],
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    department: str | None = None,
) -> AnalyticsResult:
    """Compares actual paid invoice revenue against revenue KPI targets without Cartesian joins."""
    if not _can_read("invoices", role) or not _can_read("kpitargets", role) or not _can_read("kpidefinitions", role):
        return AnalyticsResult(
            metric_family="cross_module_revenue_vs_target",
            authorized=False,
            source_collections=["invoices", "kpitargets", "kpidefinitions"],
            warnings=["Requires finance + operations access to compare invoiced revenue with KPI targets."],
            completeness_status="denied",
        )
    inv_res = compute_invoice_analytics(db, role=role, client_id=client_id, company_id=company_id, period=period, start_date=start_date, end_date=end_date, status_filter="Paid", department=department)
    defs, _ = _fetch_scoped(db, "kpidefinitions", {}, role, client_id, company_id, limit=200)
    rev_def_ids = {d["_id"] for d in defs if d.get("unit") == "₹" or "REVENUE" in str(d.get("code") or "").upper()}
    targets, _ = _fetch_scoped(db, "kpitargets", {"kpiDefinitionId": {"$in": list(rev_def_ids)}}, role, client_id, company_id, limit=1000)

    fp = parse_fiscal_date_range(period, start_date, end_date)
    target_sum = _ZERO
    matched_targets = 0
    for tg in targets:
        if fp is not None:
            ps = _as_utc_dt(tg.get("periodStart"))
            pe = _as_utc_dt(tg.get("periodEnd")) or ps
            if ps is None or pe < fp.start_utc or ps > fp.end_utc:
                continue
        target_sum += to_decimal(tg.get("targetValue", 0))
        matched_targets += 1

    actual_inr = Decimal(str(inv_res.summary.get("paid_total_inr", "0.00")))
    target_inr = quantize_inr(target_sum)
    variance = quantize_inr(actual_inr - target_inr)
    achv = quantize_inr((actual_inr / target_inr) * Decimal("100")) if target_inr > _ZERO else _ZERO
    warnings = list(inv_res.warnings)
    if matched_targets == 0:
        warnings.append("No matching revenue KPI targets found for the requested window.")

    return AnalyticsResult(
        metric_family="cross_module_revenue_vs_target",
        currency="INR",
        unit="INR",
        calculation_basis="Independent Decimal aggregation of Paid invoices vs pre-filtered ₹ kpitargets on approved date window.",
        date_range=inv_res.date_range,
        applied_filters={"department": department},
        source_collections=["invoices", "kpitargets", "kpidefinitions"],
        summary={
            "actual_paid_revenue_inr": str(actual_inr),
            "target_revenue_inr": str(target_inr),
            "variance_inr": str(variance),
            "target_achievement_percent": str(achv),
            "matched_targets_count": matched_targets,
        },
        warnings=warnings,
        completeness_status=inv_res.completeness_status,
    )


def compute_delivery_vs_kpi_bi(
    db: Database[dict[str, Any]],
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    now_utc: datetime | None = None,
) -> AnalyticsResult:
    """Joins per-assignee overdue task metrics cleanly 1:1 with score snapshots without M:N fanout."""
    if not _can_read("tasks", role) or not _can_read("scoresnapshots", role):
        return AnalyticsResult(metric_family="cross_module_delivery_vs_kpi", authorized=False, warnings=["Requires tasks and KPI snapshot permissions."], completeness_status="denied")
    tasks, _ = _fetch_scoped(db, "tasks", {}, role, client_id, company_id)
    snaps, _ = _fetch_scoped(db, "scoresnapshots", {}, role, client_id, company_id, limit=1000)
    users, _ = _fetch_scoped(db, "users", {}, role, client_id, company_id, limit=2000)
    name_map = {str(u["_id"]): str(u.get("name") or u.get("email") or u["_id"]) for u in users}

    ref_now = now_utc or datetime.now(timezone.utc)
    fp = parse_fiscal_date_range(period, start_date, end_date, now_utc=ref_now)
    task_stats: dict[str, dict[str, int]] = {}
    for t in tasks:
        if fp is not None and ((dt := _as_utc_dt(t.get("createdAt"))) is None or not (fp.start_utc <= dt <= fp.end_utc)):
            continue
        uid = str((t.get("assignee") or {}).get("id") or t.get("owner") or "Unassigned")
        b = task_stats.setdefault(uid, {"total": 0, "completed": 0, "overdue": 0})
        b["total"] += 1
        st = str(t.get("status") or "").lower()
        due = _as_utc_dt(t.get("dueDate")) or _as_utc_dt(t.get("dueDateAt"))
        if st == "completed":
            b["completed"] += 1
        elif st != "cancelled" and due and due < ref_now:
            b["overdue"] += 1

    latest_score: dict[str, Decimal] = {}
    for s in snaps:
        sid = str(s.get("scopeId") or "")
        if sid and sid not in latest_score and s.get("finalScore") is not None:
            latest_score[sid] = quantize_inr(to_decimal(s["finalScore"]))

    rows = [
        {"entity": name_map.get(uid, uid), **st, "kpi_final_score": str(latest_score[uid]) if uid in latest_score else "N/A"}
        for uid, st in sorted(task_stats.items(), key=lambda kv: -kv[1]["overdue"])
    ]
    return AnalyticsResult(
        metric_family="cross_module_delivery_vs_kpi",
        currency="N/A",
        unit="Tasks & Score",
        calculation_basis="Per-assignee task counts computed before 1:1 join against latest scoresnapshots.",
        date_range={"label": fp.label} if fp else {"label": "All Periods"},
        source_collections=["tasks", "scoresnapshots", "users"],
        summary={"tracked_assignees": len(rows), "total_overdue_tasks": sum(r["overdue"] for r in rows)},
        breakdowns={"assignee_delivery_vs_kpi": rows[:25]},
    )


def _execute_filtered_list(
    db: Database[dict[str, Any]],
    plan: StructuredQueryPlan,
    role: str,
    client_id: str | None,
    company_id: str | None,
) -> list[RetrievedChunk]:
    """Executes validated parameterized list filters across tasks, accounts, leads, or invoices."""
    col = plan.entities[0]
    spec = APPROVED_COLLECTIONS[col]
    base_q: dict[str, Any] = {}
    p_kwargs = _period_kwargs(plan)
    fp = parse_fiscal_date_range(p_kwargs["period"], p_kwargs["start_date"], p_kwargs["end_date"])

    if assignee := plan.filters.get("assignee"):
        u_docs, _ = _fetch_scoped(db, "users", {"name": {"$regex": re.escape(str(assignee)), "$options": "i"}}, role, client_id, company_id, limit=10)
        uids = [u["_id"] for u in u_docs]
        base_q["$or"] = [{"assignee.id": {"$in": uids}}, {"assignee.name": {"$regex": re.escape(str(assignee)), "$options": "i"}}, {"owner": {"$regex": re.escape(str(assignee)), "$options": "i"}}]
    if prio := plan.filters.get("priority"):
        base_q["priority"] = {"$regex": f"^{re.escape(str(prio))}$", "$options": "i"}
    if st := plan.filters.get("status"):
        if str(st).lower() != "overdue":
            base_q["status"] = {"$regex": f"^{re.escape(str(st))}$", "$options": "i"}
    if mkt := plan.filters.get("marketplace"):
        base_q["marketplace"] = {"$regex": re.escape(str(mkt)), "$options": "i"}
    if pl := plan.filters.get("plan"):
        base_q["plan"] = {"$regex": re.escape(str(pl)), "$options": "i"}

    docs, _ = _fetch_scoped(db, col, base_q, role, client_id, company_id, limit=2000)
    if fp is not None:
        dt_field = "date" if col == "invoices" else ("updatedAt" if str(plan.filters.get("status") or "").lower() == "completed" else "createdAt")
        docs = [d for d in docs if (dt := _as_utc_dt(d.get(dt_field)) or _as_utc_dt(d.get("createdAt"))) and fp.start_utc <= dt <= fp.end_utc]

    total = len(docs)
    shown = docs[: min(plan.limit, 50)]
    lines = [
        f"# Filtered Query Results ({col})",
        f"- **Filters Applied**: {plan.filters} | Date Window: {fp.label if fp else 'All Time'}",
        f"- **Total Matching Records**: {total} | Showing: 1–{len(shown)}",
    ]
    for d in shown:
        clean = _sanitize_record(d)
        label = clean.get("taskId") or clean.get("invoiceNumber") or clean.get("leadCode") or clean.get("name") or clean.get("_id")
        meta_bits = [f"{k}: {clean[k]}" for k in ("title", "status", "priority", "marketplace", "plan", "amount", "owner") if k in clean]
        lines.append(f"- **{label}** — {' | '.join(meta_bits)}")
    return [RetrievedChunk(text="\n".join(lines), source=f"mongo:{col}", department=spec.department, access_level=spec.access_level, score=1.0, chunk_index=0)]


def run_planned_query_for_chat(
    query: str,
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    db: Database[dict[str, Any]] | None = None,
) -> list[RetrievedChunk]:
    """Plans, pre-validates, and deterministically executes analytical or filtered queries for chat."""
    plan = plan_natural_language_query(query)
    if plan.intent in ("GENERAL_RAG", "ENTITY_LOOKUP", "ENTITY_360", "SCHEMA_DISCOVERY", "EXPORT_REQUEST"):
        return []
    if plan.intent == "CLARIFICATION_REQUIRED":
        return [RetrievedChunk(text=f"Clarification Needed: {plan.clarification_question}", source="erp:query_planner", department="general", access_level="all", score=1.0, chunk_index=0)]

    active_db = db if db is not None else _get_read_db()
    if active_db is None:
        return []
    outcome = validate_query_plan(plan, role=role, company_id=company_id, db=active_db)
    if not outcome.valid or not outcome.authorized:
        _logger.warning("Query plan blocked: errors=%s denied=%s", outcome.errors, outcome.inaccessible_collections)
        return [RetrievedChunk(text="Insufficient data or access denied: You do not have permission or valid fields for this query.", source="erp:query_planner", department="general", access_level="all", score=1.0, chunk_index=0)]

    pk = _period_kwargs(plan)
    try:
        if plan.intent == "FILTERED_LIST":
            return _execute_filtered_list(active_db, plan, role, client_id, company_id)
        if plan.intent == "FINANCIAL_ANALYTICS":
            if "clientaccounts" in plan.entities:
                res = compute_subscription_metrics(active_db, role=role, client_id=client_id, company_id=company_id, plan_filter=plan.filters.get("plan"), marketplace=plan.filters.get("marketplace"))
            else:
                res = compute_invoice_analytics(active_db, role=role, client_id=client_id, company_id=company_id, status_filter=plan.filters.get("status"), department=plan.filters.get("department"), customer=plan.filters.get("customer"), **pk)
            return format_analytics_chunks(res, role)
        if plan.intent == "KPI_ANALYTICS":
            res = compute_kpi_analytics(active_db, role=role, client_id=client_id, company_id=company_id, department=plan.filters.get("department"), employee=plan.filters.get("assignee"), scope_type=plan.filters.get("scope_type"), **pk)
            return format_analytics_chunks(res, role)
        if plan.intent == "CROSS_MODULE_ANALYTICS":
            flow = str(plan.filters.get("bi_flow") or "")
            if flow == "sales_funnel":
                res = compute_sales_funnel_bi(active_db, role=role, client_id=client_id, company_id=company_id, **pk)
            elif flow == "revenue_vs_target":
                res = compute_revenue_vs_target_bi(active_db, role=role, client_id=client_id, company_id=company_id, department=plan.filters.get("department"), **pk)
            else:
                res = compute_delivery_vs_kpi_bi(active_db, role=role, client_id=client_id, company_id=company_id, **pk)
            return format_analytics_chunks(res, role)
    except PermissionError:
        return []
    return []
