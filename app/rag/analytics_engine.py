"""Deterministic Decimal financial, GST, MRR/ARR, and KPI analytics engine over MongoDB."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any  # Dynamic BSON/MongoDB analytics records

from bson import ObjectId
from pydantic import BaseModel, Field
from pymongo.database import Database

from app.config import settings
from app.rag.entity_lookup import _get_read_db
from app.rag.entity_pagination import build_tenant_mongo_filter
from app.rag.fiscal_calendar import (
    FiscalPeriod,
    NormalizedAmount,
    format_inr,
    normalize_currency_amount,
    parse_fiscal_date_range,
    quantize_inr,
    to_decimal,
)
from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.retriever import ROLE_ACCESS, RetrievedChunk

_logger = logging.getLogger("chatbot.rag.analytics_engine")
_ZERO = Decimal("0.00")
_MAX_SCAN_DOCS = 10000


class GSTValidationSummary(BaseModel):
    """Per-invoice GST arithmetic and HSN compliance validation summary."""

    invoice_number: str
    subtotal_inr: str
    cgst_inr: str
    sgst_inr: str
    igst_inr: str
    utgst_inr: str
    total_tax_inr: str
    grand_total_inr: str
    tax_rate: str
    hsn_codes: list[str] = Field(default_factory=list)
    valid: bool = True
    warnings: list[str] = Field(default_factory=list)


class AnalyticsResult(BaseModel):
    """Structured analytical output with strict provenance, units, coverage, and warnings."""

    metric_family: str
    authorized: bool = True
    currency: str = "INR"
    unit: str = "INR"
    calculation_basis: str = ""
    date_range: dict[str, str] = Field(default_factory=dict)
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    source_collections: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    breakdowns: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    data_coverage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    completeness_status: str = "complete"


def _can_read(col: str, role: str) -> bool:
    """Checks approved collection governance and RBAC access level."""
    spec = APPROVED_COLLECTIONS.get(col)
    allowed = ROLE_ACCESS.get(role.lower(), [])
    return spec is not None and spec.access_level in allowed


def _access_denied_result(family: str, source_cols: list[str], reason: str) -> AnalyticsResult:
    """Constructs fail-closed AnalyticsResult on RBAC or tenant isolation failure."""
    return AnalyticsResult(
        metric_family=family,
        authorized=False,
        source_collections=source_cols,
        warnings=[reason],
        completeness_status="denied",
    )


def _fetch_scoped(
    db: Database[dict[str, Any]],
    col: str,
    base: dict[str, Any],
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    limit: int = _MAX_SCAN_DOCS,
) -> tuple[list[dict[str, Any]], bool]:
    """Executes tenant-and-role-scoped MongoDB query rejecting unapproved collections."""
    if not _can_read(col, role):
        raise PermissionError(f"Role {role!r} is not authorized to query {col!r}")
    non_deleted: dict[str, Any] = {"$and": [base, {"isDeleted": {"$ne": True}}]} if base else {"isDeleted": {"$ne": True}}
    filt = build_tenant_mongo_filter(non_deleted, role=role, client_id=client_id, company_id=company_id, collection_name=col)
    if filt is None:
        raise PermissionError(f"Tenant isolation blocked query on {col!r}")
    docs = list(db[col].find(filt).limit(limit + 1))
    truncated = len(docs) > limit
    return (docs[:limit] if truncated else docs), truncated


def _as_utc_dt(val: Any) -> datetime | None:
    """Coerces MongoDB datetime or ISO string safely into UTC datetime."""
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val.astimezone(timezone.utc)
    if isinstance(val, str) and val.strip():
        try:
            dt = datetime.fromisoformat(val.strip().replace("Z", "+00:00"))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def validate_invoice_gst(inv_doc: dict[str, Any]) -> GSTValidationSummary:
    """Validates invoice subtotal, CGST/SGST/IGST/UTGST split, HSNs, and grand total via Decimal."""
    inv_no = str(inv_doc.get("invoiceNumber") or inv_doc.get("_id") or "UNKNOWN")
    tb = inv_doc.get("taxBreakdown") if isinstance(inv_doc.get("taxBreakdown"), dict) else {}
    sub_inr = normalize_currency_amount(inv_doc.get("subTotal", 0), "subTotal").inr_amount
    amt_inr = normalize_currency_amount(inv_doc.get("amount", 0), "amount").inr_amount
    cgst = normalize_currency_amount(tb.get("cgst", 0), "cgst").inr_amount
    sgst = normalize_currency_amount(tb.get("sgst", 0), "sgst").inr_amount
    igst = normalize_currency_amount(tb.get("igst", 0), "igst").inr_amount
    utgst = normalize_currency_amount(tb.get("utgst", 0), "utgst").inr_amount
    total_tax = quantize_inr(cgst + sgst + igst + utgst)

    warnings: list[str] = []
    if (cgst > _ZERO or sgst > _ZERO) and igst > _ZERO:
        warnings.append(f"{inv_no}: mixed intra-state (CGST/SGST) and inter-state (IGST) taxes")
    if cgst != sgst:
        warnings.append(f"{inv_no}: asymmetric CGST ({cgst}) vs SGST ({sgst})")
    expected_total = quantize_inr(sub_inr + total_tax)
    diff = abs(amt_inr - expected_total)
    if diff > Decimal("1.00") and amt_inr > _ZERO:
        warnings.append(f"{inv_no}: total mismatch (subTotal+tax={expected_total}, amount={amt_inr})")

    items = inv_doc.get("items") if isinstance(inv_doc.get("items"), list) else []
    hsns = sorted({str(it.get("hsnCode")).strip() for it in items if isinstance(it, dict) and it.get("hsnCode")})
    if items and not hsns:
        warnings.append(f"{inv_no}: missing HSN codes on line items")

    return GSTValidationSummary(
        invoice_number=inv_no,
        subtotal_inr=str(sub_inr),
        cgst_inr=str(cgst),
        sgst_inr=str(sgst),
        igst_inr=str(igst),
        utgst_inr=str(utgst),
        total_tax_inr=str(total_tax),
        grand_total_inr=str(amt_inr),
        tax_rate=str(to_decimal(tb.get("taxRate", 0))),
        hsn_codes=hsns,
        valid=len(warnings) == 0,
        warnings=warnings,
    )


def _resolve_dept_maps(
    db: Database[dict[str, Any]],
    role: str,
    client_id: str | None,
    company_id: str | None,
) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str]]:
    """Builds non-ambiguous department attribution maps from departments, clientaccounts, and subscriptionevents."""
    dept_names: dict[str, str] = {}
    client_depts: dict[str, set[str]] = {}
    sub_inv_dept: dict[str, str] = {}

    if _can_read("departments", role):
        depts, _ = _fetch_scoped(db, "departments", {}, role, client_id, company_id, limit=200)
        for d in depts:
            dept_names[str(d.get("_id"))] = str(d.get("name") or d.get("code") or "")
    if _can_read("clientaccounts", role):
        accs, _ = _fetch_scoped(db, "clientaccounts", {}, role, client_id, company_id, limit=5000)
        for a in accs:
            cid = str(a.get("clientId") or "")
            did = str(a.get("departmentId") or "")
            if cid and did in dept_names:
                client_depts.setdefault(cid, set()).add(dept_names[did])
    if _can_read("subscriptionevents", role):
        evs, _ = _fetch_scoped(db, "subscriptionevents", {"invoiceId": {"$ne": None}}, role, client_id, company_id, limit=2000)
        for ev in evs:
            iid = str(ev.get("invoiceId") or "")
            did = str(ev.get("departmentId") or "")
            if iid and did in dept_names:
                sub_inv_dept[iid] = dept_names[did]
    return dept_names, client_depts, sub_inv_dept


def _resolve_invoice_dept(
    inv: dict[str, Any],
    sub_inv_dept: dict[str, str],
    client_depts: dict[str, set[str]],
) -> tuple[str, bool]:
    """Resolves unique invoice department without splitting or double-counting ambiguous clients."""
    iid = str(inv.get("_id") or "")
    if iid in sub_inv_dept:
        return sub_inv_dept[iid], False
    explicit_dept = str(inv.get("department") or "").strip()
    if explicit_dept:
        return explicit_dept, False
    cl_obj = inv.get("client") if isinstance(inv.get("client"), dict) else {}
    cid = str(cl_obj.get("id") or "")
    mapped = client_depts.get(cid, set())
    if len(mapped) == 1:
        return next(iter(mapped)), False
    if len(mapped) > 1:
        return "Unattributed (Multi-Dept Client)", True
    return "Unattributed", True


def _invoice_discount_map(
    db: Database[dict[str, Any]],
    role: str,
    client_id: str | None,
    company_id: str | None,
) -> dict[str, Decimal]:
    """Computes normalized INR discount delta per invoiceId from subscriptionevents."""
    if not _can_read("subscriptionevents", role):
        return {}
    evs, _ = _fetch_scoped(db, "subscriptionevents", {}, role, client_id, company_id, limit=2000)
    out: dict[str, Decimal] = {}
    for ev in evs:
        iid = str(ev.get("invoiceId") or "")
        lp = ev.get("listPricePaise")
        ap = ev.get("amountPaise")
        if iid and lp is not None and ap is not None:
            list_inr = normalize_currency_amount(lp, "listPricePaise").inr_amount
            paid_inr = normalize_currency_amount(ap, "amountPaise").inr_amount
            if list_inr > paid_inr:
                out[iid] = quantize_inr(list_inr - paid_inr)
    return out


def _classify_invoice_bucket(inv: dict[str, Any], ref_now: datetime) -> str:
    """Classifies invoice financial state cleanly into paid, overdue, pending, or other."""
    status = str(inv.get("status") or "").strip().lower()
    if status == "paid":
        return "paid"
    due_dt = _as_utc_dt(inv.get("dueDate"))
    if status == "overdue" or (status in ("pending", "unpaid", "partially_paid", "issued") and due_dt and due_dt < ref_now):
        return "overdue"
    if status in ("pending", "unpaid", "partially_paid", "issued"):
        return "pending"
    return status or "other"


def _apply_invoice_filters(
    invs: list[dict[str, Any]],
    fp: FiscalPeriod | None,
    status_filter: str | None,
    customer: str | None,
) -> list[dict[str, Any]]:
    """Applies verified date window, status, and customer filters to invoice records."""
    st_norm = status_filter.strip().lower() if status_filter else None
    cust_norm = customer.strip().lower() if customer else None
    kept: list[dict[str, Any]] = []
    for inv in invs:
        if fp is not None:
            inv_dt = _as_utc_dt(inv.get("date")) or _as_utc_dt(inv.get("createdAt"))
            if inv_dt is None or not (fp.start_utc <= inv_dt <= fp.end_utc):
                continue
        if st_norm and str(inv.get("status") or "").strip().lower() != st_norm:
            continue
        if cust_norm:
            cl = inv.get("client") if isinstance(inv.get("client"), dict) else {}
            hay = " ".join(str(cl.get(k) or "") for k in ("name", "company", "email")).lower()
            if cust_norm not in hay:
                continue
        kept.append(inv)
    return kept


def _to_breakdown_rows(buckets: dict[str, Decimal], counts: dict[str, int], key_name: str) -> list[dict[str, Any]]:
    """Converts Decimal aggregation maps into sorted tabular breakdown records."""
    ranked = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
    return [
        {key_name: k, "amount_inr": str(quantize_inr(v)), "count": counts.get(k, 0)}
        for k, v in ranked
    ]


def compute_invoice_analytics(
    db: Database[dict[str, Any]] | None = None,
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status_filter: str | None = None,
    department: str | None = None,
    customer: str | None = None,
    now_utc: datetime | None = None,
) -> AnalyticsResult:
    """Computes Decimal invoice, GST, status, department, and customer revenue totals."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None:
        return AnalyticsResult(metric_family="financial_invoices", authorized=True, completeness_status="unavailable", warnings=["MongoDB read connection unavailable."])
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return _access_denied_result("financial_invoices", ["invoices"], "Missing required company_id claim.")
    try:
        raw_invs, truncated = _fetch_scoped(active_db, "invoices", {}, role, client_id, company_id)
    except PermissionError as exc:
        return _access_denied_result("financial_invoices", ["invoices"], str(exc))

    ref_now = now_utc or datetime.now(timezone.utc)
    fp = parse_fiscal_date_range(period, start_date, end_date, now_utc=ref_now)
    _, client_depts, sub_inv_dept = _resolve_dept_maps(active_db, role, client_id, company_id)
    disc_map = _invoice_discount_map(active_db, role, client_id, company_id)
    filtered = _apply_invoice_filters(raw_invs, fp, status_filter, customer)

    subtotal_sum = paid_sum = pending_sum = overdue_sum = total_sum = disc_sum = _ZERO
    cgst_sum = sgst_sum = igst_sum = utgst_sum = tax_sum = _ZERO
    by_dept: dict[str, Decimal] = {}
    by_dept_cnt: dict[str, int] = {}
    by_cust: dict[str, Decimal] = {}
    by_cust_cnt: dict[str, int] = {}
    warnings: list[str] = ["Invoice scan hit safety cap; totals reflect partial batch."] if truncated else []
    unattributed_cnt = 0

    for inv in filtered:
        dept_label, is_unattr = _resolve_invoice_dept(inv, sub_inv_dept, client_depts)
        if department and department.strip().lower() not in dept_label.lower():
            continue
        if is_unattr:
            unattributed_cnt += 1
        gst = validate_invoice_gst(inv)
        warnings.extend(gst.warnings)
        sub_v, grand_v = Decimal(gst.subtotal_inr), Decimal(gst.grand_total_inr)
        subtotal_sum += sub_v
        total_sum += grand_v
        cgst_sum += Decimal(gst.cgst_inr)
        sgst_sum += Decimal(gst.sgst_inr)
        igst_sum += Decimal(gst.igst_inr)
        utgst_sum += Decimal(gst.utgst_inr)
        tax_sum += Decimal(gst.total_tax_inr)
        disc_sum += disc_map.get(str(inv.get("_id") or ""), _ZERO) + normalize_currency_amount(inv.get("discount", 0), "discount").inr_amount

        bucket = _classify_invoice_bucket(inv, ref_now)
        if bucket == "paid":
            paid_sum += grand_v
        elif bucket == "overdue":
            overdue_sum += grand_v
        elif bucket == "pending":
            pending_sum += grand_v

        by_dept[dept_label] = by_dept.get(dept_label, _ZERO) + grand_v
        by_dept_cnt[dept_label] = by_dept_cnt.get(dept_label, 0) + 1
        cl = inv.get("client") if isinstance(inv.get("client"), dict) else {}
        cust_label = str(cl.get("company") or cl.get("name") or "Unknown Client").strip()
        by_cust[cust_label] = by_cust.get(cust_label, _ZERO) + grand_v
        by_cust_cnt[cust_label] = by_cust_cnt.get(cust_label, 0) + 1

    if unattributed_cnt:
        warnings.append(f"{unattributed_cnt} invoice(s) had no unique department mapping and were not split across departments.")

    matched_cnt = sum(by_dept_cnt.values())
    return AnalyticsResult(
        metric_family="financial_invoices",
        currency="INR",
        unit="INR",
        calculation_basis="Decimal sum over verified MongoDB invoices using INR fields and single-pass Paise->INR discounts.",
        date_range={"label": fp.label, "start_utc": fp.start_utc.isoformat(), "end_utc": fp.end_utc.isoformat(), "fiscal_year": fp.fiscal_year} if fp else {"label": "All Time"},
        applied_filters={"status": status_filter, "department": department, "customer": customer},
        source_collections=["invoices", "clientaccounts", "departments", "subscriptionevents"],
        summary={
            "invoice_count": matched_cnt,
            "subtotal_inr": str(quantize_inr(subtotal_sum)),
            "paid_total_inr": str(quantize_inr(paid_sum)),
            "pending_total_inr": str(quantize_inr(pending_sum)),
            "overdue_total_inr": str(quantize_inr(overdue_sum)),
            "cgst_total_inr": str(quantize_inr(cgst_sum)),
            "sgst_total_inr": str(quantize_inr(sgst_sum)),
            "igst_total_inr": str(quantize_inr(igst_sum)),
            "utgst_total_inr": str(quantize_inr(utgst_sum)),
            "gst_total_inr": str(quantize_inr(tax_sum)),
            "discount_total_inr": str(quantize_inr(disc_sum)),
            "grand_total_inr": str(quantize_inr(total_sum)),
        },
        breakdowns={
            "by_department": _to_breakdown_rows(by_dept, by_dept_cnt, "department"),
            "by_customer": _to_breakdown_rows(by_cust, by_cust_cnt, "customer")[:25],
        },
        data_coverage={"scanned_invoices": len(raw_invs), "matched_invoices": matched_cnt, "unattributed_department_count": unattributed_cnt},
        warnings=list(dict.fromkeys(warnings)),
        completeness_status="partial" if truncated else "complete",
    )


def _infer_monthly_divisor(acc: dict[str, Any]) -> tuple[Decimal, str]:
    """Determines billing cadence divisor (1=monthly, 3=quarterly, 12=annual) cleanly."""
    plan = str(acc.get("plan") or "").lower()
    if any(tok in plan for tok in ("12m", "annual", "yearly", "1y")):
        return Decimal("12"), "annual"
    if any(tok in plan for tok in ("3m", "quarter", "quarterly")):
        return Decimal("3"), "quarterly"
    start_dt = _as_utc_dt(acc.get("subscriptionStart"))
    end_dt = _as_utc_dt(acc.get("subscriptionEnd"))
    if start_dt and end_dt and end_dt > start_dt:
        days = (end_dt - start_dt).days
        if days >= 330:
            return Decimal("12"), "annual_window"
        if 75 <= days <= 110:
            return Decimal("3"), "quarterly_window"
    return Decimal("1"), "monthly"


def compute_subscription_metrics(
    db: Database[dict[str, Any]] | None = None,
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    plan_filter: str | None = None,
    marketplace: str | None = None,
) -> AnalyticsResult:
    """Computes MRR, ARR, active accounts, and normalized Paise/INR subscription revenue."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None:
        return AnalyticsResult(metric_family="subscription_mrr_arr", completeness_status="unavailable")
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return _access_denied_result("subscription_mrr_arr", ["clientaccounts"], "Missing required company_id claim.")
    try:
        accs, truncated = _fetch_scoped(active_db, "clientaccounts", {"isActive": True}, role, client_id, company_id)
    except PermissionError as exc:
        return _access_denied_result("subscription_mrr_arr", ["clientaccounts"], str(exc))

    plan_catalog_mrr: dict[str, Decimal] = {}
    if _can_read("platformplans", role):
        for pl in active_db["platformplans"].find({}):
            base_paise = (pl.get("pricing") or {}).get("baseFeePaise")
            if base_paise is not None:
                plan_catalog_mrr[str(pl.get("name") or "").lower()] = normalize_currency_amount(base_paise, "baseFeePaise").inr_amount

    mrr_sum = discount_sum = gross_sum = _ZERO
    by_plan: dict[str, Decimal] = {}
    by_plan_cnt: dict[str, int] = {}
    by_mkt: dict[str, Decimal] = {}
    by_mkt_cnt: dict[str, int] = {}
    zero_fee_cnt = 0

    for a in accs:
        pl_name = str(a.get("plan") or "Unspecified").strip()
        mkt_name = str(a.get("marketplace") or "Unspecified").strip()
        if plan_filter and plan_filter.strip().lower() not in pl_name.lower():
            continue
        if marketplace and marketplace.strip().lower() not in mkt_name.lower():
            continue
        fee_inr = normalize_currency_amount(a.get("amount", 0), "amount").inr_amount
        disc_inr = normalize_currency_amount(a.get("discount", 0), "discount").inr_amount
        divisor, _ = _infer_monthly_divisor(a)
        monthly_val = quantize_inr(fee_inr / divisor) if fee_inr > _ZERO else _ZERO
        if fee_inr <= _ZERO:
            zero_fee_cnt += 1
        mrr_sum += monthly_val
        gross_sum += fee_inr
        discount_sum += disc_inr
        by_plan[pl_name] = by_plan.get(pl_name, _ZERO) + monthly_val
        by_plan_cnt[pl_name] = by_plan_cnt.get(pl_name, 0) + 1
        by_mkt[mkt_name] = by_mkt.get(mkt_name, _ZERO) + monthly_val
        by_mkt_cnt[mkt_name] = by_mkt_cnt.get(mkt_name, 0) + 1

    mrr_q = quantize_inr(mrr_sum)
    arr_q = quantize_inr(mrr_q * Decimal("12"))
    warnings = [f"{zero_fee_cnt} active account(s) had ₹0.00 fee."] if zero_fee_cnt else []
    if truncated:
        warnings.append("Subscription scan hit safety cap.")

    return AnalyticsResult(
        metric_family="subscription_mrr_arr",
        currency="INR",
        unit="INR",
        calculation_basis="MRR sums active clientaccounts fee normalized by billing cadence (1/3/12); ARR = MRR * 12; platform plan baseFeePaise scaled once / 100.",
        applied_filters={"plan": plan_filter, "marketplace": marketplace},
        source_collections=["clientaccounts", "platformplans"],
        summary={
            "active_accounts": sum(by_plan_cnt.values()),
            "mrr_inr": str(mrr_q),
            "arr_inr": str(arr_q),
            "gross_billed_fee_inr": str(quantize_inr(gross_sum)),
            "discount_total_inr": str(quantize_inr(discount_sum)),
            "platform_plan_catalog_mrr_inr": {k: str(v) for k, v in plan_catalog_mrr.items()},
        },
        breakdowns={
            "by_plan_mrr": _to_breakdown_rows(by_plan, by_plan_cnt, "plan"),
            "by_marketplace_mrr": _to_breakdown_rows(by_mkt, by_mkt_cnt, "marketplace"),
        },
        data_coverage={"scanned_active_accounts": len(accs), "zero_fee_accounts": zero_fee_cnt},
        warnings=warnings,
        completeness_status="partial" if truncated else "complete",
    )


def _compute_kpi_achv(actual: Decimal, target: Decimal, direction: str, cap: Decimal) -> Decimal:
    """Computes deterministic Decimal KPI achievement % capped cleanly."""
    if target <= _ZERO:
        return _ZERO
    raw = (actual / target * Decimal("100")) if direction != "lower_is_better" else (target / actual * Decimal("100") if actual > _ZERO else _ZERO)
    bounded = max(_ZERO, min(cap, raw))
    return quantize_inr(bounded)


def compute_kpi_analytics(
    db: Database[dict[str, Any]] | None = None,
    *,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    department: str | None = None,
    employee: str | None = None,
    kpi_code: str | None = None,
    scope_type: str | None = None,
) -> AnalyticsResult:
    """Computes weighted KPI target vs actual achievement from MongoDB KPI performance records."""
    active_db = db if db is not None else _get_read_db()
    if active_db is None:
        return AnalyticsResult(metric_family="kpi_analytics", completeness_status="unavailable")
    if getattr(settings, "require_tenant_isolation", False) and not company_id:
        return _access_denied_result("kpi_analytics", ["kpiperformancerecords"], "Missing required company_id claim.")
    try:
        recs, truncated = _fetch_scoped(active_db, "kpiperformancerecords", {}, role, client_id, company_id)
        defs, _ = _fetch_scoped(active_db, "kpidefinitions", {}, role, client_id, company_id, limit=500)
    except PermissionError as exc:
        return _access_denied_result("kpi_analytics", ["kpiperformancerecords", "kpidefinitions"], str(exc))

    def_map = {str(d["_id"]): d for d in defs}
    scope_names: dict[str, str] = {}
    for col in ("departments", "teams", "users"):
        if _can_read(col, role):
            docs, _ = _fetch_scoped(active_db, col, {}, role, client_id, company_id, limit=2000)
            for doc in docs:
                scope_names[str(doc["_id"])] = str(doc.get("name") or doc.get("code") or doc.get("_id"))

    fp = parse_fiscal_date_range(period, start_date, end_date)
    weighted_num = weight_sum = target_sum = actual_sum = _ZERO
    matched_cnt = no_data_cnt = excluded_cnt = 0
    by_kpi: list[dict[str, Any]] = []

    for r in recs:
        if fp is not None:
            ps = _as_utc_dt(r.get("periodStart"))
            pe = _as_utc_dt(r.get("periodEnd")) or ps
            if ps is None or pe < fp.start_utc or ps > fp.end_utc:
                continue
        if scope_type and str(r.get("scopeType") or "").lower() != scope_type.lower():
            continue
        kdef = def_map.get(str(r.get("kpiDefinitionId") or ""), {})
        code = str(kdef.get("code") or r.get("kpiDefinitionId") or "")
        kname = str(kdef.get("name") or code)
        if kpi_code and kpi_code.lower() not in code.lower() and kpi_code.lower() not in kname.lower():
            continue
        sname = scope_names.get(str(r.get("scopeId") or ""), str(r.get("scopeId") or ""))
        if department and str(r.get("scopeType") or "") == "department" and department.lower() not in sname.lower():
            continue
        if employee and employee.lower() not in sname.lower():
            continue

        matched_cnt += 1
        if r.get("excluded"):
            excluded_cnt += 1
            continue
        if str(r.get("dataStatus") or "") == "no_data":
            no_data_cnt += 1

        t_val = to_decimal(r.get("targetValue", 0))
        a_val = to_decimal(r.get("actualValue", 0))
        wt = to_decimal(r.get("weight", kdef.get("weight", 1)), Decimal("1"))
        cap = to_decimal(r.get("capPercent", kdef.get("capPercent", 100)), Decimal("100"))
        direction = str(r.get("direction") or kdef.get("direction") or "higher_is_better")
        achv = _compute_kpi_achv(a_val, t_val, direction, cap) if r.get("achievementPercent") is None else quantize_inr(to_decimal(r.get("achievementPercent")))

        target_sum += t_val
        actual_sum += a_val
        weighted_num += achv * wt
        weight_sum += wt
        by_kpi.append({
            "kpi_code": code,
            "kpi_name": kname,
            "unit": str(kdef.get("unit") or ""),
            "scope_type": str(r.get("scopeType") or ""),
            "scope_name": sname,
            "target": str(quantize_inr(t_val)),
            "actual": str(quantize_inr(a_val)),
            "achievement_percent": str(achv),
            "weight": str(wt),
            "data_status": str(r.get("dataStatus") or "unknown"),
        })

    w_avg = quantize_inr(weighted_num / weight_sum) if weight_sum > _ZERO else _ZERO
    warnings = [f"{no_data_cnt} KPI record(s) reported no_data."] if no_data_cnt else []
    if truncated:
        warnings.append("KPI scan hit safety cap.")

    return AnalyticsResult(
        metric_family="kpi_analytics",
        currency="INR" if any(row["unit"] == "₹" for row in by_kpi) else "Mixed",
        unit="Percent/Mixed",
        calculation_basis="Deterministic weighted achievement from kpiperformancerecords + kpidefinitions with capPercent enforcement.",
        date_range={"label": fp.label, "start_utc": fp.start_utc.isoformat(), "end_utc": fp.end_utc.isoformat(), "fiscal_year": fp.fiscal_year} if fp else {"label": "All Periods"},
        applied_filters={"department": department, "employee": employee, "kpi_code": kpi_code, "scope_type": scope_type},
        source_collections=["kpiperformancerecords", "kpidefinitions", "departments", "teams", "users"],
        summary={
            "records_matched": matched_cnt,
            "excluded_count": excluded_cnt,
            "no_data_count": no_data_cnt,
            "weighted_achievement_percent": str(w_avg),
            "aggregate_target_sum": str(quantize_inr(target_sum)),
            "aggregate_actual_sum": str(quantize_inr(actual_sum)),
        },
        breakdowns={"kpi_records": by_kpi[:40]},
        data_coverage={"scanned_records": len(recs), "matched_records": matched_cnt, "no_data_records": no_data_cnt},
        warnings=warnings,
        completeness_status="partial" if truncated else "complete",
    )


def format_analytics_chunks(result: AnalyticsResult, role: str) -> list[RetrievedChunk]:
    """Serializes an AnalyticsResult into grounded RBAC-tagged RetrievedChunks for RAG answers."""
    if not result.authorized:
        return []
    col_primary = result.source_collections[0] if result.source_collections else "analytics"
    spec = APPROVED_COLLECTIONS.get(col_primary)
    dept = spec.department if spec else "operations"
    lvl = spec.access_level if spec else ("finance" if "financial" in result.metric_family else "operations")
    if lvl not in ROLE_ACCESS.get(role.lower(), []):
        return []

    lines = [
        f"# Deterministic ERP Analytics: {result.metric_family}",
        f"- **Currency / Units**: {result.currency} ({result.unit})",
        f"- **Date Range**: {result.date_range.get('label', 'All Time')}",
        f"- **Applied Filters**: {result.applied_filters}",
        f"- **Calculation Methodology**: {result.calculation_basis}",
        f"- **Source Collections**: {', '.join(result.source_collections)}",
        f"- **Completeness Status**: {result.completeness_status} | Coverage: {result.data_coverage}",
        "### Key Summary Metrics",
    ]
    for k, v in result.summary.items():
        disp = format_inr(to_decimal(v)) if isinstance(v, str) and k.endswith("_inr") else str(v)
        lines.append(f"- **{k}**: {disp}")
    for b_name, rows in result.breakdowns.items():
        if rows:
            lines.append(f"### Breakdown: {b_name} ({len(rows)} rows)")
            lines.extend(f"- {row}" for row in rows[:20])
    if result.warnings:
        lines.append("### Data Validation & Coverage Warnings")
        lines.extend(f"- {w}" for w in result.warnings[:15])
    return [RetrievedChunk(text="\n".join(lines), source=f"mongo:{col_primary}", department=dept, access_level=lvl, score=1.0, chunk_index=0)]


__all__ = [
    "AnalyticsResult",
    "FiscalPeriod",
    "GSTValidationSummary",
    "NormalizedAmount",
    "compute_invoice_analytics",
    "compute_kpi_analytics",
    "compute_subscription_metrics",
    "format_analytics_chunks",
    "normalize_currency_amount",
    "parse_fiscal_date_range",
    "validate_invoice_gst",
]
