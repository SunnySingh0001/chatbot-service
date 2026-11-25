"""Phase 3 tests for NL query planner, pre-execution validation, filtered lists, and cross-module BI."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any  # Dynamic MongoDB test fixtures

from bson import ObjectId

from app.rag.cross_module_analytics import (
    compute_delivery_vs_kpi_bi,
    compute_revenue_vs_target_bi,
    compute_sales_funnel_bi,
    run_planned_query_for_chat,
)
from app.rag.query_planner import (
    StructuredQueryPlan,
    plan_natural_language_query,
    validate_query_plan,
)


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
            if not all(_match(doc, s) for s in cond):
                return False
            continue
        if k == "$or":
            if not any(_match(doc, s) for s in cond):
                return False
            continue
        val = doc.get(k.split(".")[0]) if "." not in k else (doc.get(k.split(".")[0]) or {}).get(k.split(".")[1])
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
            if "$regex" in cond:
                import re

                flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
                if not re.search(cond["$regex"], str(val or ""), flags):
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

    def find_one(self, filt: dict[str, Any] | None = None, proj: Any = None) -> dict[str, Any] | None:
        del proj
        q = filt or {}
        for d in self._docs:
            if _match(d, q):
                return d
        return None


class _FakeDB:
    def __init__(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self._data = data

    def __getitem__(self, name: str) -> _FakeCol:
        return _FakeCol(self._data.get(name, []))


def test_nl_query_planner_intents_and_canonical_queries() -> None:
    """Verifies intent classification across task filter, finance, cross-module BI, schema, and clarification."""
    p1 = plan_natural_language_query("Show all high-priority tasks assigned to Nishu that were completed last month.")
    assert p1.intent == "FILTERED_LIST"
    assert p1.entities == ["tasks"]
    assert p1.filters.get("priority") == "High"
    assert p1.filters.get("status") == "Completed"
    assert p1.filters.get("assignee") == "Nishu"
    assert p1.date_range is not None and p1.date_range.period == "last_month"

    p2 = plan_natural_language_query("Last month Sales department ki paid invoices dikhao aur total amount batao.")
    assert p2.intent == "FINANCIAL_ANALYTICS"
    assert p2.filters.get("department") == "Sales"
    assert p2.filters.get("status") == "Paid"
    assert p2.date_range is not None and p2.date_range.period == "last_month"

    p3 = plan_natural_language_query("Show total actual revenue vs revenue KPI target for Q1")
    assert p3.intent == "CROSS_MODULE_ANALYTICS"
    assert p3.filters.get("bi_flow") == "revenue_vs_target"

    p4 = plan_natural_language_query("Which team has the highest overdue tasks vs KPI score?")
    assert p4.intent == "CROSS_MODULE_ANALYTICS"
    assert p4.filters.get("bi_flow") == "delivery_vs_kpi"

    p5 = plan_natural_language_query("revenue batao")
    assert p5.intent == "CLARIFICATION_REQUIRED"


def test_pre_execution_plan_validation_rejects_unapproved_unsafe_and_rbac() -> None:
    """Verifies query planner validator blocks unapproved collections, $-operators, secrets, and low-privilege roles."""
    # 1. Sensitive collection blocked
    bad_col = StructuredQueryPlan(intent="FILTERED_LIST", entities=["salaryslips"])
    assert validate_query_plan(bad_col, role="super-admin").valid is False

    # 2. NoSQL injection operator or passwordHash field blocked
    bad_op = StructuredQueryPlan(intent="FILTERED_LIST", entities=["tasks"], filters={"$where": "sleep(100)"})
    out_op = validate_query_plan(bad_op, role="admin")
    assert out_op.valid is False
    assert out_op.authorized is False

    bad_secret = StructuredQueryPlan(intent="FILTERED_LIST", entities=["users"], filters={"passwordHash": "abc"})
    assert validate_query_plan(bad_secret, role="admin").authorized is False

    # 3. Employee blocked from finance invoices
    fin_plan = StructuredQueryPlan(intent="FINANCIAL_ANALYTICS", entities=["invoices"], filters={"status": "Paid"})
    assert validate_query_plan(fin_plan, role="employee").authorized is False
    assert validate_query_plan(fin_plan, role="finance").authorized is True


def test_cross_module_bi_prevents_m2n_double_counting_and_enforces_rbac() -> None:
    """Verifies sales funnel, revenue-vs-target, and delivery-vs-KPI deduplication and RBAC."""
    cli_1 = ObjectId()
    emp_1 = ObjectId()
    dept_sales = ObjectId()
    kdef_rev = ObjectId()

    db = _FakeDB({
        "users": [{"_id": emp_1, "name": "Nishu", "companyId": "comp-A"}],
        "departments": [{"_id": dept_sales, "name": "Sales", "companyId": "comp-A"}],
        "leads": [
            {"_id": ObjectId(), "leadCode": "LEAD-1", "status": "won", "convertedClientId": cli_1, "companyId": "comp-A"},
            {"_id": ObjectId(), "leadCode": "LEAD-2", "status": "won", "convertedClientId": cli_1, "companyId": "comp-A"},
            {"_id": ObjectId(), "leadCode": "LEAD-3", "status": "won", "convertedClientId": None, "companyId": "comp-A"},
        ],
        "clientaccounts": [
            {"_id": ObjectId(), "clientId": cli_1, "departmentId": dept_sales, "amount": 5000, "isActive": True, "companyId": "comp-A"},
            {"_id": ObjectId(), "clientId": cli_1, "departmentId": dept_sales, "amount": 3000, "isActive": True, "companyId": "comp-A"},
        ],
        "subscriptionevents": [],
        "invoices": [
            {"_id": ObjectId(), "invoiceNumber": "INV-1", "status": "Paid", "subTotal": 5000, "amount": 5900, "taxBreakdown": {"cgst": 450, "sgst": 450}, "client": {"id": cli_1}, "date": datetime(2026, 6, 5, tzinfo=timezone.utc), "companyId": "comp-A"},
        ],
        "kpidefinitions": [
            {"_id": kdef_rev, "code": "SALES_TOTAL_REVENUE", "name": "Total Revenue", "unit": "₹", "companyId": "comp-A"},
        ],
        "kpitargets": [
            {"_id": ObjectId(), "kpiDefinitionId": kdef_rev, "targetValue": 10000, "periodStart": datetime(2026, 4, 1, tzinfo=timezone.utc), "periodEnd": datetime(2026, 6, 30, tzinfo=timezone.utc), "companyId": "comp-A"},
        ],
        "tasks": [
            {"_id": ObjectId(), "taskId": "TSK-1", "title": "Fix Catalogue", "priority": "High", "status": "Completed", "assignee": {"id": emp_1}, "createdAt": datetime(2026, 5, 10, tzinfo=timezone.utc), "updatedAt": datetime(2026, 5, 12, tzinfo=timezone.utc), "companyId": "comp-A"},
            {"_id": ObjectId(), "taskId": "TSK-2", "title": "Overdue SLA", "priority": "High", "status": "In Progress", "assignee": {"id": emp_1}, "dueDate": datetime(2025, 1, 1, tzinfo=timezone.utc), "companyId": "comp-A"},
        ],
        "scoresnapshots": [
            {"_id": ObjectId(), "scopeId": emp_1, "finalScore": 84.5, "companyId": "comp-A"},
        ],
    })

    # Funnel: 2 leads share cli_1; invoice sum must be 5900.00 ONCE (NOT 2x = 11800!) and missing convertedClientId warned
    funnel_adm = compute_sales_funnel_bi(db, role="admin", company_id="comp-A")  # type: ignore[arg-type]
    assert funnel_adm.summary["converted_unique_clients"] == 1
    assert funnel_adm.summary["subscription_billed_inr"] == "8000.00"
    assert funnel_adm.summary["paid_invoiced_revenue_inr"] == "5900.00"
    assert funnel_adm.data_coverage["missing_converted_client_id_on_won"] == 1

    # Employee sees CRM counts/subscription but finance invoiced amount is omitted cleanly
    funnel_emp = compute_sales_funnel_bi(db, role="employee", company_id="comp-A")  # type: ignore[arg-type]
    assert "paid_invoiced_revenue_inr" not in funnel_emp.summary
    assert any("finance" in w.lower() for w in funnel_emp.warnings)

    # Actual Revenue vs KPI Target
    rev_target = compute_revenue_vs_target_bi(db, role="admin", company_id="comp-A")  # type: ignore[arg-type]
    assert rev_target.summary["actual_paid_revenue_inr"] == "5900.00"
    assert rev_target.summary["target_revenue_inr"] == "10000.00"
    assert rev_target.summary["target_achievement_percent"] == "59.00"

    # Delivery Overdue vs KPI Score
    deliv = compute_delivery_vs_kpi_bi(db, role="operations", company_id="comp-A")  # type: ignore[arg-type]
    assert deliv.summary["total_overdue_tasks"] == 1
    assert deliv.breakdowns["assignee_delivery_vs_kpi"][0]["kpi_final_score"] == "84.50"

    # End-to-end chat router: Filtered high-priority tasks assigned to Nishu
    task_chunks = run_planned_query_for_chat("Show all high-priority tasks assigned to Nishu that were completed", role="employee", company_id="comp-A", db=db)  # type: ignore[arg-type]
    assert task_chunks and "TSK-1" in task_chunks[0].text
    assert "TSK-2" not in task_chunks[0].text
