"""Phase 2 comprehensive tests for Decimal financial math, GST, Paise/INR, IST FY, KPI, and tenant analytics."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any  # Dynamic MongoDB test fixtures

import pytest
from bson import ObjectId

from app.config import settings
from app.rag.analytics_engine import (
    compute_invoice_analytics,
    compute_kpi_analytics,
    compute_subscription_metrics,
    format_analytics_chunks,
    normalize_currency_amount,
    parse_fiscal_date_range,
    validate_invoice_gst,
)
from app.rag.entity_pagination import _tenant_id_variants


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __iter__(self) -> Any:
        return iter(self._docs)


def _match(doc: dict[str, Any], filt: dict[str, Any]) -> bool:
    if not filt:
        return True
    for k, cond in filt.items():
        if k == "$and":
            if not all(_match(doc, sub) for sub in cond):
                return False
            continue
        if k == "$or":
            if not any(_match(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(k)
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


class _FakeCol:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def find(self, filt: dict[str, Any] | None = None) -> _FakeCursor:
        q = filt or {}
        return _FakeCursor([d for d in self._docs if _match(d, q)])


class _FakeDB:
    def __init__(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self._data = data

    def __getitem__(self, name: str) -> _FakeCol:
        return _FakeCol(self._data.get(name, []))


def test_currency_normalization_never_double_converts() -> None:
    """Verifies Paise->INR scaling occurs exactly once and Decimal accuracy holds."""
    p1 = normalize_currency_amount(1770000, "amountPaise")
    assert p1.original_unit == "paise"
    assert p1.inr_amount == Decimal("17700.00")
    assert p1.paise_amount == 1770000

    # Re-passing NormalizedAmount must be idempotent
    p2 = normalize_currency_amount(p1, "amountPaise")
    assert p2.inr_amount == Decimal("17700.00")

    r1 = normalize_currency_amount("5000.5", "subTotal")
    assert r1.original_unit == "INR"
    assert r1.inr_amount == Decimal("5000.50")


def test_indian_fy_and_ist_date_range_boundaries() -> None:
    """Verifies Asia/Kolkata to UTC conversions for India FY (Apr-Mar) and quarters."""
    # 2026-06-15T10:00 IST falls in Q1 FY26-27
    ref_utc = datetime(2026, 6, 15, 4, 30, tzinfo=timezone.utc)
    fp_q1 = parse_fiscal_date_range("Q1", now_utc=ref_utc)
    assert fp_q1 is not None
    assert fp_q1.fiscal_year == "FY26-27"
    assert fp_q1.start_utc == datetime(2026, 3, 31, 18, 30, tzinfo=timezone.utc)  # Apr 1 00:00 IST

    fp_last_m = parse_fiscal_date_range("last_month", now_utc=ref_utc)
    assert fp_last_m is not None
    assert fp_last_m.label == "May 2026"

    fp_prev_fy = parse_fiscal_date_range("previous_fy", now_utc=ref_utc)
    assert fp_prev_fy is not None
    assert fp_prev_fy.fiscal_year == "FY25-26"

    # January belongs to prior calendar year's Indian FY (Jan 2026 -> FY25-26, Q4)
    jan_utc = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
    fp_jan_q4 = parse_fiscal_date_range("Q4", now_utc=jan_utc)
    assert fp_jan_q4 is not None
    assert fp_jan_q4.fiscal_year == "FY25-26"


def test_gst_validation_detects_asymmetry_and_total_mismatch() -> None:
    """Verifies CGST/SGST vs IGST conflict and grand total mismatch detection."""
    good = validate_invoice_gst({
        "invoiceNumber": "INV-OK-1",
        "subTotal": 5000,
        "amount": 5900,
        "taxBreakdown": {"cgst": 450, "sgst": 450, "igst": 0, "utgst": 0, "taxRate": 18},
        "items": [{"hsnCode": "998311"}],
    })
    assert good.valid is True
    assert good.total_tax_inr == "900.00"

    bad = validate_invoice_gst({
        "invoiceNumber": "INV-BAD-2",
        "subTotal": 5000,
        "amount": 6500,
        "taxBreakdown": {"cgst": 450, "sgst": 450, "igst": 180, "utgst": 0, "taxRate": 18},
        "items": [{"description": "Missing HSN"}],
    })
    assert bad.valid is False
    assert len(bad.warnings) >= 3


def test_invoice_analytics_totals_overdue_dept_and_rbac(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies paid/pending/overdue/GST/discount/dept attribution and fail-closed RBAC."""
    dept_sales = ObjectId()
    dept_ecom = ObjectId()
    cli_sales = ObjectId()
    cli_multi = ObjectId()
    inv_paid_id = ObjectId()

    db = _FakeDB({
        "departments": [
            {"_id": dept_sales, "name": "Sales", "code": "DEP-SALES", "companyId": "comp-A"},
            {"_id": dept_ecom, "name": "Ecom Management", "code": "DEP-ECOM", "companyId": "comp-A"},
        ],
        "clientaccounts": [
            {"_id": ObjectId(), "clientId": cli_sales, "departmentId": dept_sales, "isActive": True, "amount": 5000, "discount": 500, "plan": "Elite (LS 1M)", "marketplace": "Amazon", "companyId": "comp-A"},
            {"_id": ObjectId(), "clientId": cli_multi, "departmentId": dept_sales, "isActive": True, "amount": 12000, "plan": "Annual 12M", "marketplace": "Flipkart", "companyId": "comp-A"},
            {"_id": ObjectId(), "clientId": cli_multi, "departmentId": dept_ecom, "isActive": True, "amount": 3000, "plan": "Monthly", "marketplace": "Amazon", "companyId": "comp-A"},
        ],
        "subscriptionevents": [
            {"_id": ObjectId(), "invoiceId": inv_paid_id, "departmentId": dept_sales, "listPricePaise": 590000, "amountPaise": 500000, "companyId": "comp-A"},
        ],
        "invoices": [
            {"_id": inv_paid_id, "invoiceNumber": "INV-1", "status": "Paid", "subTotal": 5000, "amount": 5900, "taxBreakdown": {"cgst": 450, "sgst": 450, "igst": 0, "utgst": 0, "taxRate": 18}, "client": {"id": cli_sales, "name": "Acme"}, "date": datetime(2026, 6, 10, tzinfo=timezone.utc), "companyId": "comp-A"},
            {"_id": ObjectId(), "invoiceNumber": "INV-2", "status": "Pending", "subTotal": 10000, "amount": 11800, "taxBreakdown": {"cgst": 0, "sgst": 0, "igst": 1800, "utgst": 0, "taxRate": 18}, "client": {"id": cli_multi, "name": "MultiCorp"}, "date": datetime(2026, 6, 11, tzinfo=timezone.utc), "dueDate": datetime(2026, 6, 1, tzinfo=timezone.utc), "companyId": "comp-A"},
            {"_id": ObjectId(), "invoiceNumber": "INV-3", "status": "Pending", "subTotal": 2000, "amount": 2360, "taxBreakdown": {"cgst": 180, "sgst": 180, "igst": 0, "utgst": 0, "taxRate": 18}, "client": {"id": cli_sales, "name": "Acme"}, "date": datetime(2026, 6, 12, tzinfo=timezone.utc), "dueDate": datetime(2026, 6, 30, tzinfo=timezone.utc), "companyId": "comp-A"},
            {"_id": ObjectId(), "invoiceNumber": "INV-B", "status": "Paid", "subTotal": 99999, "amount": 99999, "taxBreakdown": {}, "client": {"id": ObjectId(), "name": "CompB"}, "date": datetime(2026, 6, 12, tzinfo=timezone.utc), "companyId": "comp-B"},
        ],
        "platformplans": [{"name": "Enterprise", "pricing": {"baseFeePaise": 2499900}}],
    })

    # Employee role must fail closed on finance invoices
    denied = compute_invoice_analytics(db, role="employee", company_id="comp-A")  # type: ignore[arg-type]
    assert denied.authorized is False

    res = compute_invoice_analytics(
        db,
        role="finance",
        company_id="comp-A",
        now_utc=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )  # type: ignore[arg-type]
    assert res.authorized is True
    assert res.summary["invoice_count"] == 3
    assert res.summary["paid_total_inr"] == "5900.00"
    assert res.summary["overdue_total_inr"] == "11800.00"
    assert res.summary["pending_total_inr"] == "2360.00"
    assert res.summary["cgst_total_inr"] == "630.00"
    assert res.summary["igst_total_inr"] == "1800.00"
    assert res.summary["discount_total_inr"] == "900.00"
    assert res.data_coverage["unattributed_department_count"] == 1
    assert format_analytics_chunks(res, role="finance")

    # Subscription MRR / ARR verification: 5000 (1M) + 12000/12 (1000) + 3000 (1M) = 9000.00 MRR, 108000.00 ARR
    sub_res = compute_subscription_metrics(db, role="admin", company_id="comp-A")  # type: ignore[arg-type]
    assert sub_res.summary["mrr_inr"] == "9000.00"
    assert sub_res.summary["arr_inr"] == "108000.00"
    assert sub_res.summary["platform_plan_catalog_mrr_inr"]["enterprise"] == "24999.00"

    # Strict tenant isolation blocks missing company_id
    monkeypatch.setattr(settings, "require_tenant_isolation", True)
    assert compute_subscription_metrics(db, role="admin", company_id=None).authorized is False  # type: ignore[arg-type]


def test_kpi_analytics_weighted_score_and_no_data_warnings() -> None:
    """Verifies weighted KPI calculation, capPercent, no_data counting, and RBAC."""
    kdef_rev = ObjectId()
    kdef_tsk = ObjectId()
    dept_sales = ObjectId()
    emp_id = ObjectId()

    db = _FakeDB({
        "departments": [{"_id": dept_sales, "name": "Sales", "companyId": "comp-A"}],
        "teams": [],
        "users": [{"_id": emp_id, "name": "Nishu", "companyId": "comp-A"}],
        "kpidefinitions": [
            {"_id": kdef_rev, "code": "SALES_TOTAL_REVENUE", "name": "Total Revenue", "unit": "₹", "direction": "higher_is_better", "capPercent": 100, "weight": 60, "companyId": "comp-A"},
            {"_id": kdef_tsk, "code": "TASK_COMPLETION_RATE", "name": "Task Completion", "unit": "%", "direction": "higher_is_better", "capPercent": 100, "weight": 40, "companyId": "comp-A"},
        ],
        "kpiperformancerecords": [
            {"_id": ObjectId(), "kpiDefinitionId": kdef_rev, "scopeType": "department", "scopeId": dept_sales, "targetValue": 100000, "actualValue": 80000, "weight": 60, "dataStatus": "success", "companyId": "comp-A"},
            {"_id": ObjectId(), "kpiDefinitionId": kdef_tsk, "scopeType": "user", "scopeId": emp_id, "targetValue": 100, "actualValue": 120, "weight": 40, "dataStatus": "success", "companyId": "comp-A"},
            {"_id": ObjectId(), "kpiDefinitionId": kdef_tsk, "scopeType": "user", "scopeId": emp_id, "targetValue": 100, "actualValue": 0, "weight": 0, "dataStatus": "no_data", "companyId": "comp-A"},
        ],
    })

    # Base employee lacks operations access to kpiperformancerecords
    assert compute_kpi_analytics(db, role="employee", company_id="comp-A").authorized is False  # type: ignore[arg-type]

    res = compute_kpi_analytics(db, role="operations", company_id="comp-A")  # type: ignore[arg-type]
    assert res.authorized is True
    # (80 * 60 + 100 * 40) / 100 = 88.00%
    assert res.summary["weighted_achievement_percent"] == "88.00"
    assert res.summary["no_data_count"] == 1
    assert any("no_data" in w for w in res.warnings)
