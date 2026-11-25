"""Comprehensive suite for Dynamic Entity Intelligence, Schema Discovery, Multi-Hop Traversal, Pagination, Tokens, RBAC, and Exports."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any  # Mock BSON fixture records
from unittest.mock import patch

from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.main import app
from app.middleware.rate_limit import limiter
from app.rag.entity_tools import (
    count_related_records,
    discover_database_schema,
    discover_relationships,
    export_entity_data,
    get_entity_details,
    get_next_batch,
    resolve_entity,
    run_entity_discovery_for_chat,
    search_entity,
    verify_completeness,
)
from app.rag.history_budget import fit_chunks_to_token_budget
from app.rag.mongo_catalog import SENSITIVE_EXCLUDED_COLLECTIONS
from app.rag.retriever import RetrievedChunk


class _Cursor:
    """In-memory cursor mimicking sort, skip, limit, and max_time_ms cleanly."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, spec: Any, direction: int = 1) -> _Cursor:
        """Supports both single-field and multi-field deterministic sorting."""
        if isinstance(spec, list):
            for field_name, d_dir in reversed(spec):
                self._docs.sort(key=lambda r: str(r.get(field_name) or ""), reverse=(d_dir < 0))
        else:
            self._docs.sort(key=lambda r: str(r.get(spec) or ""), reverse=(direction < 0))
        return self

    def skip(self, n: int) -> _Cursor:
        """Advances cursor offset safely."""
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int) -> _Cursor:
        """Caps cursor output count."""
        self._docs = self._docs[:n]
        return self

    def max_time_ms(self, ms: int) -> _Cursor:
        """Accepts timeout guard smoothly."""
        del ms
        return self

    def __iter__(self) -> Any:  # Iterator over docs
        return iter(self._docs)


class _Collection:
    """In-memory MongoDB collection supporting $and, $or, $in, $regex, count_documents, and index_information."""

    def __init__(self, docs: list[dict[str, Any]], indexes: list[str] | None = None) -> None:
        self._docs = docs
        self._indexes = indexes or ["_id_"]

    @staticmethod
    def _val(doc: dict[str, Any], path: str) -> Any:  # Dotted nested value resolver
        cur: Any = doc
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    @classmethod
    def _match(cls, doc: dict[str, Any], filt: dict[str, Any]) -> bool:
        """Evaluates parameterized query filters deterministically."""
        if not filt:
            return True
        if "$and" in filt:
            return all(cls._match(doc, sub) for sub in filt["$and"])
        if "$or" in filt:
            return any(cls._match(doc, sub) for sub in filt["$or"])
        for k, cond in filt.items():
            v = cls._val(doc, k)
            if isinstance(cond, dict):
                if "$in" in cond:
                    if isinstance(v, list):
                        if not any(x in cond["$in"] for x in v):
                            return False
                    elif v not in cond["$in"]:
                        return False
                if "$regex" in cond:
                    import re  # noqa: PLC0415

                    flags = re.IGNORECASE if "i" in str(cond.get("$options", "")) else 0
                    if not re.search(str(cond["$regex"]), str(v or ""), flags):
                        return False
            elif isinstance(v, list):
                if cond not in v and v != cond:
                    return False
            elif v != cond:
                return False
        return True

    def find(self, filt: dict[str, Any] | None = None, projection: dict[str, int] | None = None) -> _Cursor:
        """Returns iterable sortable cursor matching filt."""
        del projection
        f = filt or {}
        return _Cursor([d for d in self._docs if self._match(d, f)])

    def find_one(self, filt: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Returns first document matching filt."""
        for d in self.find(filt or {}):
            return d
        return None

    def count_documents(self, filt: dict[str, Any] | None = None, maxTimeMS: int = 2500) -> int:
        """Counts matching documents."""
        del maxTimeMS
        return len(list(self.find(filt or {})))

    def index_information(self) -> dict[str, dict[str, Any]]:
        """Returns index names dictionary mimicking pymongo."""
        return {name: {"v": 2} for name in self._indexes}


class _Database:
    """In-memory MongoDB database with custom index metadata support."""

    def __init__(self, cols: dict[str, list[dict[str, Any]]]) -> None:
        self._cols = {k: _Collection(v, indexes=["_id_", f"{k}_idx_1"]) for k, v in cols.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._cols.get(name, _Collection([]))


def _build_rich_graph_db(num_amazon_accounts: int = 250) -> tuple[_Database, dict[str, Any]]:
    """Constructs realistic fixture with Marketplace Amazon, indirect links, 250 accounts, cycles, and ambiguity."""
    amz_id = ObjectId()
    team_id = ObjectId()
    mgr_id = ObjectId()
    comp_id = ObjectId()
    cli_id = ObjectId()
    sunny1_id = ObjectId()
    sunny2_id = ObjectId()
    sunny3_id = ObjectId()

    accounts: list[dict[str, Any]] = []
    for i in range(num_amazon_accounts):
        accounts.append({
            "_id": ObjectId(),
            "clientId": cli_id,
            "companyId": comp_id,
            "accountTypeId": amz_id,
            "managerId": mgr_id,
            "marketplace": "Amazon.in",
            "plan": "Elite" if i % 2 == 0 else "Amazon Kickstart",
            "stage": "Support Ongoing",
            "name": f"Amazon Seller Account #{i + 1}",
            "merchantToken": f"TOK-SECRET-{i}",
            "createdAt": datetime(2026, 1, 1 + (i % 28), 10, i % 60),
        })

    cols: dict[str, list[dict[str, Any]]] = {
        "accounttypes": [
            {"_id": amz_id, "name": "Amazon", "slug": "amazon", "order": 1},
            {"_id": ObjectId(), "name": "Flipkart", "slug": "flipkart", "order": 2},
        ],
        "pricingplans": [
            {"_id": ObjectId(), "name": "Amazon Kickstart", "subtitle": "Starter", "description": "Basic Amazon plan"},
            {"_id": ObjectId(), "name": "Elite", "subtitle": "Growth", "description": "Elite plan"},
        ],
        "teams": [
            {"_id": team_id, "name": "PSOA-AM", "code": "TEAM-PSOA-AM", "marketplace": "Amazon", "leadId": mgr_id, "memberIds": [mgr_id], "status": "active", "clientCount": num_amazon_accounts},
        ],
        "clientcompanies": [
            {"_id": comp_id, "clientId": cli_id, "name": "Apex Ecommerce Pvt Ltd", "gstNo": "23AABCA9999F1Z1", "isPrimary": True, "isActive": True},
        ],
        "clientaccounts": accounts,
        "users": [
            {"_id": cli_id, "name": "Apex Client", "email": "apex@example.com", "role": "client", "company": "Apex Ecommerce Pvt Ltd", "password": "pw1"},
            {"_id": mgr_id, "name": "Nishu Sarathe", "employeeId": "EMP-1026", "email": "nishu@example.com", "role": "admin", "designation": "Account Manager", "department": "Ecom Management"},
            {"_id": sunny1_id, "name": "Sunny Sharma", "email": "sunny.s@example.com", "role": "client", "company": "Sunny Traders"},
            {"_id": sunny2_id, "name": "Sunny Kapoor", "email": "sunny.k@example.com", "role": "client", "company": "Kapoor Mart"},
            {"_id": sunny3_id, "name": "Sunny Verma", "employeeId": "EMP-1099", "email": "sunny.v@example.com", "role": "admin", "designation": "BDE", "department": "Sales"},
        ],
        "reportingrelations": [
            # Self-cycle + hierarchy test
            {"_id": ObjectId(), "subordinateUserId": mgr_id, "managerUserId": mgr_id, "effectiveTo": None},
        ],
        "tasks": [
            {"_id": ObjectId(), "taskId": "TSK-501", "title": "Amazon Catalog Optimization", "status": "In Progress", "priority": "High", "client": {"id": cli_id, "accountId": accounts[0]["_id"], "accountTypeName": "Amazon"}, "assignee": {"id": mgr_id, "name": "Nishu Sarathe"}},
        ],
        "invoices": [
            {"_id": ObjectId(), "invoiceNumber": "FISIPL2526IN555", "client": {"id": cli_id, "email": "apex@example.com", "name": "Apex Client"}, "amount": 17700, "status": "Paid"},
        ],
        "approvalrequests": [
            {"_id": ObjectId(), "ref": {"model": "ClientAccount", "id": accounts[0]["_id"]}, "type": "account_review", "status": "approved", "clientName": "Apex Client", "decidedBy": mgr_id},
        ],
        # Sensitive excluded collection containing dummy secret to assert it is NEVER discovered
        "salaryslips": [{"_id": ObjectId(), "userId": mgr_id, "netPay": 999999}],
    }
    return _Database(cols), {"amz_id": str(amz_id), "cli_id": str(cli_id), "mgr_id": str(mgr_id)}


def test_dynamic_schema_discovery_excludes_sensitive_and_respects_rbac() -> None:
    """Verifies runtime schema discovery detects ObjectId refs, indexes, and blocks sensitive/restricted collections."""
    db, _ = _build_rich_graph_db(5)
    schema_emp = discover_database_schema(role="employee", db=db)  # type: ignore[arg-type]
    emp_cols = {c.collection: c for c in schema_emp.collections}

    assert "clientaccounts" in emp_cols
    assert "accounttypes" in emp_cols
    assert "invoices" not in emp_cols
    assert "approvalrequests" not in emp_cols
    for sens in SENSITIVE_EXCLUDED_COLLECTIONS:
        assert sens not in emp_cols

    acc_schema = emp_cols["clientaccounts"]
    ref_map = {f.name: f.inferred_target for f in acc_schema.fields if f.is_reference}
    assert ref_map.get("accountTypeId") == "accounttypes"
    assert ref_map.get("clientId") == "users"
    assert ref_map.get("companyId") == "clientcompanies"
    assert "merchantToken" in {f.name for f in acc_schema.fields if f.is_sensitive_masked}

    schema_admin = discover_database_schema(role="super-admin", db=db)  # type: ignore[arg-type]
    admin_cols = {c.collection for c in schema_admin.collections}
    assert {"invoices", "approvalrequests", "clientaccounts", "users"}.issubset(admin_cols)


def test_entity_identification_ambiguity_and_resolution() -> None:
    """Verifies unambiguous resolution for Amazon/TSK-501 and explicit ambiguity flagging for Sunny."""
    db, ids = _build_rich_graph_db(5)

    # Exact AccountType "Amazon" resolves cleanly without confusing with "Amazon Kickstart" plan
    resolved_amz, is_amb_amz, _ = resolve_entity("Amazon", role="super-admin", db=db)  # type: ignore[arg-type]
    assert is_amb_amz is False
    assert resolved_amz is not None
    assert resolved_amz.id == ids["amz_id"]
    assert resolved_amz.type == "AccountType"

    # Ambiguous partial name "Sunny" matches 3 users -> must set ambiguous=True and return candidates
    resolved_sun, is_amb_sun, sun_cands = resolve_entity("Sunny", role="super-admin", db=db)  # type: ignore[arg-type]
    assert resolved_sun is None
    assert is_amb_sun is True
    assert len(sun_cands) == 3

    details_sun = get_entity_details("Sunny", role="super-admin", db=db)  # type: ignore[arg-type]
    assert details_sun.ambiguous is True
    assert details_sun.entity is None
    assert len(details_sun.candidates) == 3

    # Chat discovery chunk must ask clarifying question rather than guessing
    disambig_chunks = run_entity_discovery_for_chat("about Sunny", role="super-admin", db=db)  # type: ignore[arg-type]
    assert len(disambig_chunks) == 1
    assert "Multiple Entities Matched" in disambig_chunks[0].text
    assert "Sunny Sharma" in disambig_chunks[0].text


def test_multi_hop_relationship_traversal_cycles_and_large_dataset_pagination() -> None:
    """Verifies direct + indirect hops, cycle safety, 250-record batching, deduplication, and completeness checks."""
    db, ids = _build_rich_graph_db(250)

    # Self-loop cycle in reportingrelations on EMP-1026 must complete cleanly within max_depth=3
    emp_details = get_entity_details("EMP-1026", role="super-admin", max_depth=3, batch_size=20, db=db)  # type: ignore[arg-type]
    assert emp_details.entity is not None
    assert emp_details.entity.name == "Nishu Sarathe"
    assert emp_details.retrieval.complete is False  # Only 20 of 250 managed clientaccounts loaded!
    assert any("emp_managed_accounts" in p for p in emp_details.retrieval.partial_relationships)

    # Amazon multi-hop graph: Direct (clientaccounts, teams) + Indirect Hop 2 (clientcompanies, users, tasks)
    amz_details = get_entity_details("Amazon", role="super-admin", max_depth=2, batch_size=25, db=db)  # type: ignore[arg-type]
    assert amz_details.entity is not None
    rel_map = {r.relation_name: r for r in amz_details.relationships}
    assert rel_map["accounttype_clientaccounts"].hop == 1
    assert rel_map["accounttype_clientaccounts"].total_records == 250
    assert rel_map["accounttype_clientaccounts"].retrieved_records == 25
    assert rel_map["accounttype_clientaccounts"].has_more is True
    assert rel_map["account_company"].hop == 2
    assert rel_map["account_company"].path == ["accounttypes", "clientaccounts", "clientcompanies"]
    assert "indirect hop-2" in amz_details.relationship_tree
    assert amz_details.retrieval.complete is False

    # Paginate all 250 clientaccounts in batches of 100 -> 100 + 100 + 50, zero duplicates
    b1 = get_next_batch("Amazon", "accounttype_clientaccounts", role="super-admin", offset=0, batch_size=100, db=db)  # type: ignore[arg-type]
    b2 = get_next_batch("Amazon", "accounttype_clientaccounts", role="super-admin", cursor=b1.next_cursor if b1 else None, batch_size=100, db=db)  # type: ignore[arg-type]
    b3 = get_next_batch("Amazon", "accounttype_clientaccounts", role="super-admin", cursor=b2.next_cursor if b2 else None, batch_size=100, db=db)  # type: ignore[arg-type]
    assert b1 is not None and b2 is not None and b3 is not None
    assert b1.batch_label == "Records 1–100 of 250" and b1.has_more is True and b1.complete is False
    assert b2.batch_label == "Records 101–200 of 250" and b2.has_more is True and b2.complete is False
    assert b3.batch_label == "Records 201–250 of 250" and b3.has_more is False and b3.complete is True
    all_ids = [r["_id"] for r in (b1.records + b2.records + b3.records)]
    assert len(all_ids) == 250
    assert len(set(all_ids)) == 250
    assert all("merchantToken" not in r for r in b1.records)

    # Completeness verification passes strictly when batch retrieved == total
    edges = discover_relationships("accounttypes", role="super-admin", max_depth=1)
    assert len(edges) >= 3
    assert count_related_records("Amazon", "accounttype_clientaccounts", role="super-admin", db=db) == 250  # type: ignore[arg-type]


def test_csv_json_export_completeness_and_token_budget_overflow_handling() -> None:
    """Verifies CSV/JSON exports serialize all 250 accessible records safely and token budgeting prevents silent clipping."""
    db, _ = _build_rich_graph_db(250)

    csv_bytes, mime_csv, fname_csv = export_entity_data("Amazon", role="super-admin", fmt="csv", relation_name="accounttype_clientaccounts", db=db)  # type: ignore[arg-type]
    assert "text/csv" in mime_csv and fname_csv.endswith(".csv")
    reader = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    assert len(reader) == 250
    assert "TOK-SECRET" not in csv_bytes.decode("utf-8")

    json_bytes, mime_json, fname_json = export_entity_data("Amazon", role="super-admin", fmt="json", relation_name="accounttype_clientaccounts", db=db)  # type: ignore[arg-type]
    assert "application/json" in mime_json and fname_json.endswith(".json")
    parsed = json.loads(json_bytes.decode("utf-8"))
    assert parsed["completeness"]["complete"] is True
    assert parsed["completeness"]["exported_records"] == 250

    # Token budget manager must split oversized context and inject continuation + export guidance
    big_chunks = [
        RetrievedChunk(text=("Amazon record data block " * 250), source="mongo:clientaccounts", department="sales", access_level="employee", score=1.0, chunk_index=i)
        for i in range(6)
    ]
    fitted, metrics = fit_chunks_to_token_budget(big_chunks, max_context_tokens=2000, system_reserve=400, output_reserve=300)
    assert metrics["truncated"] is True
    assert int(metrics["omitted_chunks"]) >= 1
    assert "Token Budget Notice" in fitted[-1].text
    assert "CSV/JSON export" in fitted[-1].text


def test_rbac_api_isolation_and_db_failure_graceful_degradation() -> None:
    """Verifies /entities HTTP routes enforce RBAC (blocking client role & finance data) and survive DB failures."""
    db, ids = _build_rich_graph_db(5)
    limiter.reset()
    client = TestClient(app)

    cli_tok = create_access_token("cli-user", "client")
    emp_tok = create_access_token("emp-user", "employee")
    adm_tok = create_access_token("adm-user", "super-admin")

    # 1. Client role blocked with 403 across all /entities routes
    assert client.get("/entities/schema", headers={"Authorization": f"Bearer {cli_tok}"}).status_code == 403
    assert client.get("/entities/search?q=Amazon", headers={"Authorization": f"Bearer {cli_tok}"}).status_code == 403
    assert client.get("/entities/export?identifier=Amazon&fmt=csv", headers={"Authorization": f"Bearer {cli_tok}"}).status_code == 403

    # 2. Employee role sees Apex Client but restricted invoices relationship discloses ZERO count/records
    with patch("app.rag.entity_tools._get_read_db", return_value=db), patch("app.rag.entity_lookup._get_read_db", return_value=db):
        emp_resp = client.get(f"/entities/details?identifier={ids['cli_id']}", headers={"Authorization": f"Bearer {emp_tok}"})
        assert emp_resp.status_code == 200
        body = emp_resp.json()
        assert len(body["inaccessible_relationships"]) >= 1
        inv_rels = [r for r in body["relationships"] if r["target"] == "invoices"]
        assert inv_rels and inv_rels[0]["accessible"] is False and inv_rels[0]["total_records"] == 0

        # Admin exports CSV over HTTP cleanly
        exp_resp = client.get("/entities/export?identifier=Amazon&fmt=csv", headers={"Authorization": f"Bearer {adm_tok}"})
        assert exp_resp.status_code == 200
        assert "Amazon_export.csv" in exp_resp.headers.get("content-disposition", "")

    # 3. DB failure/timeout must degrade safely without unhandled exceptions
    class _BrokenDB:
        def __getitem__(self, name: str) -> Any:
            raise TimeoutError(f"Simulated timeout on {name}")

    assert search_entity("Amazon", role="super-admin", db=_BrokenDB()) == []  # type: ignore[arg-type]
    broken_details = get_entity_details("Amazon", role="super-admin", db=_BrokenDB())  # type: ignore[arg-type]
    assert broken_details.entity is None
    assert verify_completeness([]).complete is True
    limiter.reset()
