"""Phase 1 comprehensive security, tenant isolation, sync safety, and grounding test suite."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any  # Dynamic BSON/MongoDB test fixtures

import chromadb
import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app
from app.middleware.rate_limit import limiter
from app.orchestration import nodes
from app.rag.entity_lookup import invalidate_entity_cache, lookup_entity_360
from app.rag.entity_pagination import build_tenant_mongo_filter
from app.rag.entity_tools import export_entity_data, get_entity_details, search_entity
from app.rag.identity import invalidate_identity_cache, resolve_user_identity
from app.rag.incremental_sync import reconcile_collection_deletions
from app.rag.record_chunker import _build_base_metadata
from app.rag.retriever import RetrievedChunk, _build_where_clause
from app.rag.security_validator import check_numerical_grounding, validate_grounded_answer
from datetime import datetime, timezone

from app.rag.mongo_catalog import APPROVED_COLLECTIONS
from app.rag.sync_state import CheckpointManager, CollectionCheckpoint, SyncLockManager

client = TestClient(app)


class _FakeCursor:
    """In-memory pymongo cursor subset supporting sort and iteration."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, key: str, direction: int = 1) -> _FakeCursor:
        self._docs.sort(key=lambda d: str(d.get(key, "")), reverse=(direction < 0))
        return self

    def skip(self, n: int) -> _FakeCursor:
        self._docs = self._docs[n:]
        return self

    def limit(self, n: int) -> _FakeCursor:
        self._docs = self._docs[:n]
        return self

    def __iter__(self) -> Any:
        return iter(self._docs)


def _matches_filter(doc: dict[str, Any], filt: dict[str, Any]) -> bool:
    """Evaluates subset of MongoDB query operators used across isolation checks."""
    if not filt:
        return True
    for k, cond in filt.items():
        if k == "$and":
            if not all(_matches_filter(doc, sub) for sub in cond):
                return False
            continue
        if k == "$or":
            if not any(_matches_filter(doc, sub) for sub in cond):
                return False
            continue
        val = doc.get(k.split(".")[0]) if "." not in k else (doc.get(k.split(".")[0]) or {}).get(k.split(".")[1])
        if isinstance(cond, dict):
            if "$in" in cond:
                if isinstance(val, list):
                    if not any(x in cond["$in"] for x in val):
                        return False
                elif val not in cond["$in"]:
                    return False
            if "$regex" in cond:
                import re

                flags = re.IGNORECASE if "i" in cond.get("$options", "") else 0
                if not re.search(cond["$regex"], str(val or ""), flags):
                    return False
        elif val != cond:
            return False
    return True


class _FakeCollection:
    """Minimal in-memory MongoDB collection for isolation verification."""

    def __init__(self, name: str, docs: list[dict[str, Any]]) -> None:
        self.name = name
        self._docs = docs

    def find(self, filt: dict[str, Any] | None = None, proj: dict[str, int] | None = None) -> _FakeCursor:
        del proj
        q = filt or {}
        return _FakeCursor([d for d in self._docs if _matches_filter(d, q)])

    def find_one(self, filt: dict[str, Any] | None = None, proj: dict[str, int] | None = None) -> dict[str, Any] | None:
        del proj
        q = filt or {}
        for d in self._docs:
            if _matches_filter(d, q):
                return d
        return None

    def count_documents(self, filt: dict[str, Any] | None = None) -> int:
        q = filt or {}
        return sum(1 for d in self._docs if _matches_filter(d, q))

    def index_information(self) -> dict[str, Any]:
        return {"_id_": {"key": [("_id", 1)]}}


class _FakeDB:
    """In-memory multi-tenant MongoDB database fixture."""

    def __init__(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self._data = data

    def list_collection_names(self) -> list[str]:
        return list(self._data.keys())

    def __getitem__(self, name: str) -> _FakeCollection:
        return _FakeCollection(name, self._data.get(name, []))


@pytest.fixture(autouse=True)
def _reset_caches() -> Any:
    limiter.reset()
    invalidate_entity_cache()
    invalidate_identity_cache()
    yield
    invalidate_entity_cache()
    invalidate_identity_cache()


def test_sync_checkpoint_isolation_and_live_file_untouched() -> None:
    """Verifies tests write sync checkpoints only to temporary isolated paths, never live repo data."""
    assert "SYNC_CHECKPOINT_PATH" in os.environ
    assert os.environ["SYNC_CHECKPOINT_PATH"] != str(os.path.abspath("data/sync_checkpoints.json"))

    mgr = CheckpointManager()
    assert "sync_state" in str(mgr.checkpoint_path)
    mgr.update_checkpoint(
        collection_name="tasks",
        last_sync_time=datetime.now(timezone.utc),
        record_hashes={"r1": "h1"},
        total_records=1,
        total_chunks=2,
    )
    assert mgr.checkpoint_path.exists()

    lock = SyncLockManager()
    assert "sync_state" in str(lock.lock_file)


def test_mass_deletion_guard_aborts_on_partial_or_bulk_drop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies deletion reconciliation aborts when active IDs drop catastrophically."""
    class _FakeCol:
        def __init__(self) -> None:
            self.deleted_wheres: list[dict[str, Any]] = []

        def delete(self, where: dict[str, Any] | None = None, ids: list[str] | None = None) -> None:
            if where:
                self.deleted_wheres.append(where)
            if ids:
                self.deleted_wheres.append({"ids": ids})

    col = _FakeCol()
    spec = APPROVED_COLLECTIONS["tasks"]

    class _PartialCursor:
        def __init__(self, docs: list[dict[str, Any]]) -> None:
            self._docs = docs

        def __iter__(self) -> Any:
            return iter(self._docs)

    class _PartialCollection:
        def __init__(self, count: int, docs: list[dict[str, Any]]) -> None:
            self._count = count
            self._docs = docs

        def count_documents(self, filt: dict[str, Any]) -> int:
            del filt
            return self._count

        def find(self, filt: dict[str, Any], proj: dict[str, int]) -> _PartialCursor:
            del filt, proj
            return _PartialCursor(self._docs)

    class _PartialDB:
        def __init__(self, count: int, docs: list[dict[str, Any]]) -> None:
            self._c = _PartialCollection(count, docs)

        def __getitem__(self, name: str) -> _PartialCollection:
            return self._c

    # 1. Partial scan guard: total_count=100 in Mongo, cursor returned only 10 docs (<80%)
    known_100 = {f"id_{i}": "h" for i in range(100)}
    db_partial = _PartialDB(100, [{"_id": f"id_{i}"} for i in range(10)])
    n1, ids1 = reconcile_collection_deletions(db_partial, spec, col, known_100)  # type: ignore[arg-type]
    assert (n1, ids1) == (0, [])

    # 2. >50% mass deletion threshold when >10 known docs
    known_20 = {f"t_{i}": "h" for i in range(20)}
    db_mass = _PartialDB(5, [{"_id": f"t_{i}"} for i in range(5)])
    n2, ids2 = reconcile_collection_deletions(db_mass, spec, col, known_20)  # type: ignore[arg-type]
    assert (n2, ids2) == (0, [])

    # 3. Normal single-record deletion proceeds cleanly (19/20 active)
    db_normal = _PartialDB(19, [{"_id": f"t_{i}"} for i in range(19)])
    n3, ids3 = reconcile_collection_deletions(db_normal, spec, col, known_20)  # type: ignore[arg-type]
    assert n3 == 1
    assert ids3 == ["t_19"]


def test_multi_tenant_company_isolation_mongo_and_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies strict Company A vs Company B isolation and fail-closed missing company_id behavior."""
    comp_a_emp = ObjectId()
    comp_b_emp = ObjectId()
    comp_a_cli = ObjectId()
    comp_b_cli = ObjectId()
    db = _FakeDB({
        "users": [
            {"_id": comp_a_emp, "name": "Alice Staff", "role": "employee", "employeeId": "EMP-1001", "email": "alice@compa.com", "companyId": "comp-A"},
            {"_id": comp_b_emp, "name": "Bob Staff", "role": "employee", "employeeId": "EMP-2001", "email": "bob@compb.com", "companyId": "comp-B"},
            {"_id": comp_a_cli, "name": "Alpha Seller", "role": "client", "email": "alpha@compa.com", "companyId": "comp-A"},
            {"_id": comp_b_cli, "name": "Beta Seller", "role": "client", "email": "beta@compb.com", "companyId": "comp-B"},
        ],
        "tasks": [
            {"_id": ObjectId(), "taskId": "TSK-101", "title": "Alpha Audit", "status": "Pending", "assignee": {"id": comp_a_emp}, "client": {"id": comp_a_cli}, "companyId": "comp-A"},
            {"_id": ObjectId(), "taskId": "TSK-202", "title": "Beta Secret", "status": "Pending", "assignee": {"id": comp_b_emp}, "client": {"id": comp_b_cli}, "companyId": "comp-B"},
        ],
        "clientaccounts": [
            {"_id": ObjectId(), "clientId": comp_a_cli, "managerId": comp_a_emp, "marketplace": "Amazon", "plan": "Elite", "companyId": "comp-A"},
            {"_id": ObjectId(), "clientId": comp_b_cli, "managerId": comp_b_emp, "marketplace": "Flipkart", "plan": "Pro", "companyId": "comp-B"},
        ],
        "clientcompanies": [],
        "leads": [],
        "teams": [],
        "reportingrelations": [],
        "leavebalances": [],
        "notes": [],
        "subscriptionevents": [],
        "supporttickets": [],
        "invoices": [],
        "approvalrequests": [],
        "scoresnapshots": [],
    })

    # Company A staff can find Alice & TSK-101, but NEVER Bob or TSK-202
    assert len(search_entity("Alice Staff", role="employee", company_id="comp-A", db=db)) == 1  # type: ignore[arg-type]
    assert search_entity("Bob Staff", role="employee", company_id="comp-A", db=db) == []  # type: ignore[arg-type]
    assert lookup_entity_360("TSK-202", role="employee", company_id="comp-A", db=db) == []  # type: ignore[arg-type]
    assert len(lookup_entity_360("TSK-101", role="employee", company_id="comp-A", db=db)) >= 1  # type: ignore[arg-type]

    # Export under Company A fails if target belongs to Company B
    with pytest.raises(ValueError):
        export_entity_data("Bob Staff", role="admin", company_id="comp-A", db=db)  # type: ignore[arg-type]

    # Strict mode: missing company_id fails closed
    monkeypatch.setattr(settings, "require_tenant_isolation", True)
    assert build_tenant_mongo_filter({}, role="employee", company_id=None, collection_name="tasks") is None
    assert nodes.authorize_node({"user_id": "u1", "role": "employee", "company_id": None})["status"] == "denied"  # type: ignore[typeddict-item]
    assert nodes.authorize_node({"user_id": "u1", "role": "employee", "company_id": "comp-A"})["status"] == "authorized"  # type: ignore[typeddict-item]

    no_co_tok = create_access_token(user_id="u1", role="employee")
    assert client.get("/entities/search?q=Alice", headers={"Authorization": f"Bearer {no_co_tok}"}).status_code == 403


def test_client_isolation_chunker_chroma_and_mongo(tmp_path: Any) -> None:
    """Verifies record_chunker client_id/company_id indexing and strict Client A vs Client B isolation."""
    c_a_oid = ObjectId()
    c_b_oid = ObjectId()

    meta_a = _build_base_metadata(
        {"_id": ObjectId(), "clientId": c_a_oid, "companyId": "comp-A", "marketplace": "Amazon"},
        collection_name="clientaccounts",
        department="operations",
        access_level="employee",
        record_id="rec_a",
    )
    meta_b = _build_base_metadata(
        {"_id": ObjectId(), "clientId": c_b_oid, "companyId": "comp-A", "marketplace": "Flipkart"},
        collection_name="clientaccounts",
        department="operations",
        access_level="employee",
        record_id="rec_b",
    )
    assert meta_a["client_id"] == str(c_a_oid)
    assert meta_a["company_id"] == "comp-A"

    # Real ephemeral ChromaDB verification of nested $and/$or/$in filter
    ch_client = chromadb.EphemeralClient()
    col_name = f"iso_test_{hashlib.md5(str(tmp_path).encode()).hexdigest()[:8]}"
    col = ch_client.create_collection(col_name)
    col.add(
        ids=["pub", "cli_a", "cli_b", "emp_other"],
        documents=["Public policy doc", "Alpha private account", "Beta private account", "Internal HR payroll"],
        metadatas=[
            {"source": "md:policy", "department": "general", "access_level": "all", "company_id": "comp-A"},
            meta_a,
            meta_b,
            {"source": "mongo:users", "department": "hr", "access_level": "hr", "company_id": "comp-B"},
        ],
    )
    where_a = _build_where_clause("client", client_id=str(c_a_oid), company_id="comp-A")
    res_a = col.get(where=where_a)
    got_ids = set(res_a["ids"])
    assert "pub" in got_ids
    assert "cli_a" in got_ids
    assert "cli_b" not in got_ids
    assert "emp_other" not in got_ids

    # Mongo Entity 360 self-only scope for client
    db = _FakeDB({
        "users": [
            {"_id": c_a_oid, "name": "Alpha Owner", "role": "client", "email": "a@example.com", "companyId": "comp-A"},
            {"_id": c_b_oid, "name": "Beta Owner", "role": "client", "email": "b@example.com", "companyId": "comp-A"},
        ],
        "clientaccounts": [
            {"_id": ObjectId(), "clientId": c_a_oid, "marketplace": "Amazon", "plan": "Growth", "companyId": "comp-A"},
            {"_id": ObjectId(), "clientId": c_b_oid, "marketplace": "Meesho", "plan": "Elite", "companyId": "comp-A"},
        ],
        "clientcompanies": [],
        "subscriptionevents": [],
        "supporttickets": [],
        "invoices": [],
    })
    self_chunks = lookup_entity_360("show my subscription plan", role="client", client_id=str(c_a_oid), company_id="comp-A", db=db)  # type: ignore[arg-type]
    joined = "\n".join(c.text for c in self_chunks)
    assert "Amazon" in joined
    assert "Meesho" not in joined
    assert lookup_entity_360("Beta Owner details", role="client", client_id=str(c_a_oid), company_id="comp-A", db=db) == []  # type: ignore[arg-type]


def test_identity_regex_escaping_and_bounded_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies special regex characters are escaped and identity LRU cache stays bounded."""
    import app.rag.identity as id_mod

    class _SpyUsers:
        def __init__(self) -> None:
            self.queries: list[dict[str, Any]] = []

        def find_one(self, q: dict[str, Any], proj: dict[str, int] | None = None, **kwargs: Any) -> dict[str, Any] | None:
            del proj, kwargs
            self.queries.append(q)
            return None

    class _SpyDB:
        def __init__(self) -> None:
            self.users = _SpyUsers()

        def __getitem__(self, name: str) -> _SpyUsers:
            return self.users

    class _SpyClient:
        def __init__(self, db: _SpyDB) -> None:
            self._db = db

        def __getitem__(self, name: str) -> _SpyDB:
            return self._db

    spy_db = _SpyDB()
    monkeypatch.setattr(id_mod, "_get_mongo_client", lambda: _SpyClient(spy_db))
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "mongodb_uri", "mongodb://localhost:27017")

    resolve_user_identity(".*+?(test)", role="employee", company_id="comp-A")
    assert spy_db.users.queries
    q0 = spy_db.users.queries[0]
    email_regex = q0["$and"][0]["$or"][2]["email"]["$regex"]
    assert email_regex == r"^\.\*\+\?\(test\)$"
    assert {"companyId": "comp-A"} in q0["$and"][1]["$or"]

    # Bounded LRU eviction verification
    for i in range(id_mod._IDENTITY_MAX_SIZE + 25):
        id_mod._cache_put(f"u_{i}:employee::comp-A", {"user_id": f"u_{i}"}, 1000.0 + i)
    assert len(id_mod._IDENTITY_CACHE) <= id_mod._IDENTITY_MAX_SIZE


def test_financial_numerical_grounding_and_retry_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies comma normalization and high-risk financial hallucination blocking with safe retry."""
    chunks = [
        RetrievedChunk(
            text="Invoice FISIPL2526IN001: subTotal INR 15,000.00, CGST 1,350, SGST 1,350, Total INR 17700.00.",
            source="mongo:invoices",
            department="finance",
            access_level="finance",
            score=0.95,
            chunk_index=0,
        )
    ]
    # 17,700 matches 17700.00 after comma & trailing-zero normalization
    assert check_numerical_grounding("The invoice total is ₹17,700.", chunks) == []

    # Fabricated revenue figure triggers retry & blocks ungrounded financial answer
    bad = "Total invoice revenue is ₹99,500.00 [DOC-1]."
    val = validate_grounded_answer(bad, chunks, "What is invoice revenue?")
    assert val.is_valid is False
    assert val.should_retry is True
    assert any("unsupported_numerical_claim" in r for r in val.reasons)

    # _attempt_generation retries and falls back safely if retry still fabricates numbers
    calls = {"n": 0}

    def _fake_stream(messages: list[dict[str, str]]) -> Any:
        calls["n"] += 1
        yield "Total invoice revenue is ₹99,500.00." if calls["n"] == 1 else "Still hallucinating ₹88,000."

    monkeypatch.setattr(nodes, "stream_llm", _fake_stream)
    res, retries = nodes._attempt_generation(
        [{"role": "user", "content": "What is invoice revenue?"}],
        chunks,
        "What is invoice revenue?",
    )
    assert retries == 1
    assert "99,500" not in res.cleaned_answer
    assert "88,000" not in res.cleaned_answer
