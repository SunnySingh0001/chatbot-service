"""Tests for 360-degree cross-collection entity lookup and strict RBAC enforcement."""
from __future__ import annotations

from datetime import datetime
from typing import Any  # Mock Mongo documents hold arbitrary BSON-like structures

from bson import ObjectId

from app.rag.entity_lookup import invalidate_entity_cache, lookup_entity_360


class _FakeCursor:
    """In-memory pymongo cursor supporting sort() and list iteration."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, field: str, direction: int = 1) -> _FakeCursor:
        """Sorts buffered docs cleanly without failing on missing keys."""
        self._docs.sort(key=lambda d: str(d.get(field) or ""), reverse=(direction < 0))
        return self

    def __iter__(self) -> Any:  # Iterator yielding dict documents
        return iter(self._docs)


class _FakeCollection:
    """In-memory MongoDB collection supporting subset of find/find_one query predicates."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    @staticmethod
    def _get_path(doc: dict[str, Any], key: str) -> Any:  # Traverses nested dotted fields
        cur: Any = doc
        for part in key.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    @classmethod
    def _matches(cls, doc: dict[str, Any], filt: dict[str, Any]) -> bool:
        """Evaluates basic equality, $in, $regex, and $or MongoDB query filters."""
        if not filt:
            return True
        if "$or" in filt:
            return any(cls._matches(doc, sub) for sub in filt["$or"])
        for key, cond in filt.items():
            val = cls._get_path(doc, key)
            if isinstance(cond, dict):
                if "$in" in cond:
                    if isinstance(val, list):
                        if not any(v in cond["$in"] for v in val):
                            return False
                    elif val not in cond["$in"]:
                        return False
                if "$regex" in cond:
                    import re  # noqa: PLC0415

                    flags = re.IGNORECASE if "i" in str(cond.get("$options", "")) else 0
                    if not re.search(str(cond["$regex"]), str(val or ""), flags):
                        return False
            elif isinstance(val, list):
                if cond not in val and val != cond:
                    return False
            elif val != cond:
                return False
        return True

    def find(self, filt: dict[str, Any] | None = None, projection: dict[str, int] | None = None) -> _FakeCursor:
        """Filters stored docs and returns an iterable sortable cursor."""
        del projection
        f = filt or {}
        return _FakeCursor([d for d in self._docs if self._matches(d, f)])

    def find_one(self, filt: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Returns first matching doc or None."""
        for d in self.find(filt or {}):
            return d
        return None


class _FakeDatabase:
    """In-memory MongoDB database mapping collection names to _FakeCollection."""

    def __init__(self, collections: dict[str, list[dict[str, Any]]]) -> None:
        self._cols = {k: _FakeCollection(v) for k, v in collections.items()}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._cols.get(name, _FakeCollection([]))


def _build_fixture_db() -> tuple[_FakeDatabase, ObjectId, ObjectId]:
    """Builds a realistic cross-collection fixture covering client, staff, task, invoice, and ticket links."""
    cid = ObjectId()
    eid = ObjectId()
    comp_id = ObjectId()
    acc_id = ObjectId()
    tid = ObjectId()
    ld_id = ObjectId()
    team_id = ObjectId()
    inv_id = ObjectId()

    data: dict[str, list[dict[str, Any]]] = {
        "users": [
            {
                "_id": cid,
                "name": "RAJESH KUMAR CHELARAMANI",
                "email": "rajesh@example-dummy.com",
                "phone": "9826012345",
                "password": "secret-hashed-password-never-expose",
                "merchantToken": "SECRET_TOKEN_XYZ",
                "role": "client",
                "company": "RKC Traders LLP",
                "plan": "Elite",
                "status": "active",
                "teams": [team_id],
            },
            {
                "_id": eid,
                "name": "Nishu Sarathe",
                "employeeId": "EMP-1026",
                "email": "nishu.dummy@example.com",
                "password": "emp-hashed-password",
                "role": "admin",
                "designation": "Account Manager",
                "department": "Ecom Management",
            },
        ],
        "clientcompanies": [
            {
                "_id": comp_id,
                "clientId": cid,
                "name": "RKC Traders LLP",
                "gstNo": "23AABCR1234F1Z5",
                "country": "India",
                "isPrimary": True,
                "isActive": True,
            },
        ],
        "clientaccounts": [
            {
                "_id": acc_id,
                "clientId": cid,
                "companyId": comp_id,
                "marketplace": "Amazon.in",
                "plan": "Elite",
                "supportType": "Paid",
                "stage": "Support Ongoing",
                "manager": "Nishu Sarathe",
                "managerId": eid,
                "amount": 15000,
                "merchantToken": "SENSITIVE_MERCHANT_TOK",
            },
        ],
        "teams": [
            {
                "_id": team_id,
                "name": "PSOA-AM",
                "code": "TEAM-PSOA-AM",
                "slug": "psoa-am",
                "leadId": eid,
                "memberIds": [eid],
                "status": "active",
                "clientCount": 1,
            },
        ],
        "tasks": [
            {
                "_id": tid,
                "taskId": "TSK-9901",
                "title": "A+ Content Audit",
                "description": "<p>Audit brand storefront</p>",
                "status": "Completed",
                "priority": "High",
                "client": {"name": "RAJESH KUMAR CHELARAMANI", "id": cid, "accountId": acc_id},
                "assignee": {"name": "Nishu Sarathe", "id": eid},
                "owner": "Nishu Sarathe",
                "dueDate": "2026-10-15",
                "updates": ["Completed storefront banner review"],
                "createdAt": datetime(2026, 10, 10, 9, 0),
            },
        ],
        "notes": [
            {
                "_id": ObjectId(),
                "clientId": cid,
                "accountId": acc_id,
                "author": "Nishu Sarathe",
                "authorId": eid,
                "content": "5 NEW LISTINGS CREATED",
                "date": datetime(2026, 10, 12, 11, 30),
            },
        ],
        "subscriptionevents": [
            {
                "_id": ObjectId(),
                "clientId": cid,
                "accountId": acc_id,
                "invoiceId": inv_id,
                "kind": "renewal",
                "status": "applied",
                "planName": "Elite",
                "amountPaise": 1770000,
                "reference": "PAY-REF-99",
                "createdAt": datetime(2026, 10, 1, 10, 0),
            },
        ],
        "supporttickets": [
            {
                "_id": ObjectId(),
                "ticketCode": "TCK-2026-00099",
                "clientId": cid,
                "clientAccountId": acc_id,
                "subject": "Catalog suppression issue",
                "category": "technical",
                "priority": "urgent",
                "status": "closed",
                "rating": 5,
                "messages": [{"senderName": "RAJESH", "senderRole": "client", "message": "ASIN blocked", "at": datetime(2026, 10, 11, 8, 0)}],
                "createdAt": datetime(2026, 10, 11, 8, 0),
            },
        ],
        "invoices": [
            {
                "_id": inv_id,
                "invoiceNumber": "FISIPL2526IN9999",
                "client": {"name": "RAJESH KUMAR CHELARAMANI", "email": "rajesh@example-dummy.com", "company": "RKC Traders LLP", "id": cid},
                "amount": 17700,
                "subTotal": 15000,
                "status": "Paid",
                "paymentMethod": "Razorpay",
                "items": [{"description": "Elite Monthly Plan", "hsnCode": "998311", "quantity": 1, "unitPrice": 15000}],
                "date": datetime(2026, 10, 1, 10, 0),
            },
        ],
        "leads": [
            {
                "_id": ld_id,
                "leadCode": "LEAD-9999",
                "name": "RAJESH KUMAR CHELARAMANI",
                "email": "rajesh@example-dummy.com",
                "company": "RKC Traders LLP",
                "status": "converted",
                "score": 92,
                "serviceInterested": "Amazon Account Management",
                "assignedTo": eid,
                "assignedToName": "Nishu Sarathe",
                "notes": [{"body": "Converted after Elite demo call"}],
            },
        ],
        "approvalrequests": [
            {
                "_id": ObjectId(),
                "ref": {"model": "Task", "id": tid},
                "type": "task_completion",
                "status": "approved",
                "clientName": "RAJESH KUMAR CHELARAMANI",
                "decidedBy": eid,
                "decisions": [{"by": eid, "byName": "Kuldeep Maurya"}],
                "createdAt": datetime(2026, 10, 16, 12, 0),
            },
        ],
        "reportingrelations": [
            {"_id": ObjectId(), "subordinateUserId": eid, "managerUserId": eid, "effectiveTo": None},
        ],
        "leavebalances": [
            {"_id": ObjectId(), "userId": eid, "fiscalYear": "2026-2027", "casualBalance": 12, "casualUsed": 1, "sickBalance": 12, "sickUsed": 0, "earnedBalance": 15, "earnedUsed": 2},
        ],
        "scoresnapshots": [
            {"_id": ObjectId(), "scopeId": eid, "scopeType": "user", "periodStart": datetime(2026, 10, 1), "periodEnd": datetime(2026, 10, 31), "directScore": 88.5, "finalScore": 90.0, "rank": 2, "totalWeight": 100},
        ],
        "profileupdaterequests": [
            {"_id": ObjectId(), "client": cid, "status": "approved", "adminNotes": "Verified GST", "oldData": {"phone": "111"}, "newData": {"phone": "9826012345"}, "createdAt": datetime(2026, 10, 2)},
        ],
    }
    return _FakeDatabase(data), cid, eid


def test_client_360_hydrates_all_mapped_collections_and_strips_secrets() -> None:
    """Verifies super-admin client query pulls profile, companies, accounts, tasks, notes, events, tickets, invoices, leads, approvals."""
    invalidate_entity_cache()
    db, _, _ = _build_fixture_db()
    chunks = lookup_entity_360("RAJESH KUMAR CHELARAMANI ke bare me puri info batao", role="super-admin", db=db)  # type: ignore[arg-type]
    sources = {c.source for c in chunks}
    expected = {
        "mongo:users",
        "mongo:clientcompanies",
        "mongo:clientaccounts",
        "mongo:tasks",
        "mongo:notes",
        "mongo:subscriptionevents",
        "mongo:supporttickets",
        "mongo:invoices",
        "mongo:leads",
        "mongo:approvalrequests",
        "mongo:profileupdaterequests",
    }
    assert expected.issubset(sources)
    combined = "\n".join(c.text for c in chunks)
    assert "23AABCR1234F1Z5" in combined
    assert "TSK-9901" in combined
    assert "5 NEW LISTINGS CREATED" in combined
    assert "TCK-2026-00099" in combined
    assert "FISIPL2526IN9999" in combined
    assert "secret-hashed-password-never-expose" not in combined
    assert "SENSITIVE_MERCHANT_TOK" not in combined


def test_employee_360_hydrates_portfolio_tasks_reporting_leaves_and_scores() -> None:
    """Verifies employee query by EMP ID resolves teams, reporting, leaves, managed accounts, tasks, leads, and KPI scores."""
    invalidate_entity_cache()
    db, _, _ = _build_fixture_db()
    chunks = lookup_entity_360("EMP-1026 Nishu Sarathe details", role="super-admin", db=db)  # type: ignore[arg-type]
    sources = {c.source for c in chunks}
    assert {"mongo:users", "mongo:teams", "mongo:reportingrelations", "mongo:leavebalances", "mongo:clientaccounts", "mongo:tasks", "mongo:notes", "mongo:leads", "mongo:scoresnapshots"}.issubset(sources)
    combined = "\n".join(c.text for c in chunks)
    assert "emp-hashed-password" not in combined
    assert "PSOA-AM" in combined
    assert "RAJESH KUMAR CHELARAMANI" in combined
    assert "90.0" in combined


def test_rbac_blocks_finance_operations_hr_and_client_unauthorized_lookups() -> None:
    """Verifies RBAC prevents employee from seeing finance/hr/operations sections and blocks client role completely."""
    invalidate_entity_cache()
    db, _, _ = _build_fixture_db()

    # Employee role must never receive finance invoices, operations approvals, or HR profile update requests
    emp_chunks = lookup_entity_360("RAJESH KUMAR CHELARAMANI details", role="employee", db=db)  # type: ignore[arg-type]
    emp_sources = {c.source for c in emp_chunks}
    assert "mongo:users" in emp_sources
    assert "mongo:tasks" in emp_sources
    assert "mongo:invoices" not in emp_sources
    assert "mongo:approvalrequests" not in emp_sources
    assert "mongo:profileupdaterequests" not in emp_sources

    # Employee querying invoice code directly must get 0 chunks
    assert lookup_entity_360("FISIPL2526IN9999 details", role="employee", db=db) == []  # type: ignore[arg-type]
    # Finance role gets invoice + client core info without operational approvals
    fin_chunks = lookup_entity_360("FISIPL2526IN9999 details", role="finance", db=db)  # type: ignore[arg-type]
    assert any(c.source == "mongo:invoices" for c in fin_chunks)
    assert not any(c.source == "mongo:approvalrequests" for c in fin_chunks)

    # Client role must get 0 chunks for any cross-entity lookup
    assert lookup_entity_360("RAJESH KUMAR CHELARAMANI details", role="client", db=db) == []  # type: ignore[arg-type]
    assert lookup_entity_360("TSK-9901", role="client", db=db) == []  # type: ignore[arg-type]


def test_explicit_codes_pronoun_followups_and_non_interference() -> None:
    """Verifies task/ticket codes, pronoun follow-ups, and non-interception on list/policy queries."""
    invalidate_entity_cache()
    db, _, _ = _build_fixture_db()

    tsk_chunks = lookup_entity_360("TSK-9901 complete history", role="super-admin", db=db)  # type: ignore[arg-type]
    assert any("Completed storefront banner review" in c.text for c in tsk_chunks)

    tk_chunks = lookup_entity_360("TCK-2026-00099 messages", role="super-admin", db=db)  # type: ignore[arg-type]
    assert any("ASIN blocked" in c.text for c in tk_chunks)

    # Pronoun follow-up picks up entity from recent history
    hist = [{"role": "user", "content": "RAJESH KUMAR CHELARAMANI ke bare me batao"}]
    followup = lookup_entity_360("iske tasks aur notes batao", role="employee", history=hist, db=db)  # type: ignore[arg-type]
    assert any("5 NEW LISTINGS CREATED" in c.text for c in followup)

    # Broad list, filtered plan, and policy questions return [] so regular RAG runs
    assert lookup_entity_360("complete client list", role="super-admin", db=db) == []  # type: ignore[arg-type]
    assert lookup_entity_360("Amazon Kickstart plan ke clients", role="super-admin", db=db) == []  # type: ignore[arg-type]
    assert lookup_entity_360("Which VPN software client must be used?", role="super-admin", db=db) == []  # type: ignore[arg-type]
