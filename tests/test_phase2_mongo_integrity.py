"""Phase 2 Automated Test Suite: MongoDB Structured Serialization, Record Chunking, Catalog, and Data Integrity."""
from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from bson import Decimal128, ObjectId

from app.rag.embedder import get_embedding_function
from app.rag.mongo_catalog import (
    APPROVED_COLLECTIONS,
    CollectionSpec,
    SENSITIVE_EXCLUDED_COLLECTIONS,
    SYSTEM_EXCLUDED_COLLECTIONS,
    audit_mongo_collections,
)
from app.rag.mongo_ingest import ingest_collection_structured
from app.rag.mongo_serializer import (
    extract_record_identifier,
    is_sensitive_field,
    normalize_bson_value,
    serialize_mongo_document,
)
from app.rag.record_chunker import (
    MongoChunk,
    chunk_record,
    compute_content_hash,
)


# ── 1. Serialization Tests (Simple, Nested, Arrays, Types) ─────────────────────


def test_simple_document_serialization() -> None:
    """Verifies that flat key-values are cleanly serialized into structured YAML-like markdown."""
    doc = {
        "_id": ObjectId("64b8f0a1c25f4b1a2c3d4e5f"),
        "code": "DEP-SALES",
        "name": "Sales Department",
        "active": True,
        "headcount": 14,
    }
    text = serialize_mongo_document(doc, collection_name="departments")
    assert "Record ID: DEP-SALES" in text
    assert "code: DEP-SALES" in text
    assert "name: Sales Department" in text
    assert "active: true" in text
    assert "headcount: 14" in text


def test_nested_document_serialization() -> None:
    """Verifies that nested objects maintain hierarchical parent-child relationships."""
    doc = {
        "_id": ObjectId("64b8f0a1c25f4b1a2c3d4e5f"),
        "invoiceNumber": "INV-2026-001",
        "client": {
            "name": "Rahul Sharma",
            "company": "Acme Retail Ltd",
            "address": {"city": "Bhopal", "state": "MP"},
        },
        "amount": 45000,
    }
    text = serialize_mongo_document(doc, collection_name="invoices")
    assert "Record ID: INV-2026-001" in text
    assert "client:" in text
    assert "  name: Rahul Sharma" in text
    assert "  company: Acme Retail Ltd" in text
    assert "  address:" in text
    assert "    city: Bhopal" in text


def test_arrays_containing_objects_serialization() -> None:
    """Verifies that arrays of items or sub-documents preserve structured bullet points."""
    doc = {
        "_id": ObjectId("64b8f0a1c25f4b1a2c3d4e5f"),
        "recordId": "ORD-501",
        "items": [
            {"product": "Business Laptop", "quantity": 2, "rate": 50000},
            {"product": "Wireless Mouse", "quantity": 5, "rate": 800},
        ],
        "status": "Delivered",
    }
    text = serialize_mongo_document(doc, collection_name="orders")
    assert "Record ID: ORD-501" in text
    assert "items:" in text
    assert "- product: Business Laptop" in text
    assert "  quantity: 2" in text
    assert "  rate: 50000" in text
    assert "- product: Wireless Mouse" in text
    assert "status: Delivered" in text


def test_objectid_and_decimal_reference_serialization() -> None:
    """Verifies ObjectIds and Decimal128 values are converted to standard strings and floats."""
    oid = ObjectId("64b8f0a1c25f4b1a2c3d4e5f")
    ref_oid = ObjectId("64b8f0a1c25f4b1a2c3d4e60")
    dec = Decimal128("125000.75")
    doc = {
        "_id": oid,
        "managerId": ref_oid,
        "totalRevenue": dec,
    }
    text = serialize_mongo_document(doc, collection_name="metrics")
    assert "64b8f0a1c25f4b1a2c3d4e5f" in text
    assert "managerId: 64b8f0a1c25f4b1a2c3d4e60" in text
    assert "totalRevenue: 125000.75" in text


def test_date_and_timestamp_formatting() -> None:
    """Verifies that datetime and date objects are serialized to ISO 8601 strings."""
    created_dt = datetime.datetime(2026, 4, 15, 10, 30, 0, tzinfo=datetime.timezone.utc)
    target_d = datetime.date(2026, 12, 31)
    doc = {
        "_id": "task_1",
        "createdAt": created_dt,
        "deadline": target_d,
    }
    text = serialize_mongo_document(doc, collection_name="tasks")
    assert "createdAt: 2026-04-15T10:30:00+00:00" in text
    assert "deadline: 2026-12-31" in text


def test_missing_and_null_values_handling() -> None:
    """Verifies that null and empty list fields are handled without dropping keys."""
    doc = {
        "_id": "doc_empty",
        "notes": None,
        "tags": [],
        "description": "",
    }
    text = serialize_mongo_document(doc, collection_name="settings")
    assert "notes: null" in text
    assert "tags: []" in text


# ── 2. Sensitive Field Redaction Tests ─────────────────────────────────────────


def test_sensitive_field_exclusion() -> None:
    """Verifies that passwords, API keys, tokens, and payment methods are never serialized."""
    doc = {
        "_id": "user_sec",
        "name": "Admin User",
        "email": "admin@fakhriitservices.com",
        "password": "hashed_secret_password_123",
        "passwordHash": "$2b$12$e89asdjfasd",
        "apiKey": "sk_live_secretkey999",
        "refreshToken": "jwt_refresh_token_abc",
        "paymentMethods": [{"card": "4111222233334444", "cvv": "123"}],
        "accountAccessUrl": "https://auth.internal/access?token=secret",
    }
    text = serialize_mongo_document(doc, collection_name="users")
    assert "name: Admin User" in text
    assert "email: admin@fakhriitservices.com" in text
    assert "password" not in text
    assert "hashed_secret_password_123" not in text
    assert "apiKey" not in text
    assert "sk_live_secretkey999" not in text
    assert "paymentMethods" not in text
    assert "4111222233334444" not in text
    assert "accountAccessUrl" not in text


# ── 3. Record-Aware Chunking Tests ────────────────────────────────────────────


def test_record_aware_chunking_small_record_atomic() -> None:
    """Verifies that a compact MongoDB record is kept whole in a single chunk."""
    doc = {
        "_id": ObjectId("64b8f0a1c25f4b1a2c3d4e5f"),
        "invoiceNumber": "INV-2026-042",
        "amount": 25000,
        "status": "Paid",
        "client": {"name": "Zaid Khan", "city": "Indore"},
    }
    serialized = serialize_mongo_document(doc, collection_name="invoices")
    chunks = chunk_record(doc, serialized, "invoices", "finance", "finance", max_words=180)

    assert len(chunks) == 1
    assert chunks[0].metadata["record_id"] == "INV-2026-042"
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[0].metadata["department"] == "finance"
    assert chunks[0].metadata["access_level"] == "finance"
    assert chunks[0].metadata["source"] == "mongo:invoices"
    assert len(chunks[0].metadata["content_hash"]) == 16


def test_record_aware_chunking_large_record_header_propagation() -> None:
    """Verifies that a large record is split across blocks with header propagation."""
    doc = {
        "_id": "SOP-100",
        "title": "Amazon Account Setup Standard Operating Procedure",
        "sectionA": "Paragraph content A with detailed instructions. " * 30,
        "sectionB": "Paragraph content B with validation criteria. " * 30,
    }
    serialized = serialize_mongo_document(doc, collection_name="soptemplates")
    chunks = chunk_record(doc, serialized, "soptemplates", "operations", "employee", max_words=50)

    assert len(chunks) > 1
    for i, c in enumerate(chunks):
        assert c.metadata["chunk_index"] == i
        assert c.metadata["record_id"] == "SOP-100"
        # Each chunk carries context identifier
        assert "SOP-100" in c.text


def test_chunk_metadata_completeness_and_hash() -> None:
    """Verifies all required metadata fields (including timestamps and hash) are present."""
    doc = {
        "_id": "EMP-900",
        "name": "Sara Khan",
        "createdAt": datetime.datetime(2026, 1, 1, 9, 0, 0, tzinfo=datetime.timezone.utc),
        "updatedAt": datetime.datetime(2026, 2, 1, 10, 0, 0, tzinfo=datetime.timezone.utc),
    }
    serialized = serialize_mongo_document(doc, collection_name="teammembers")
    chunks = chunk_record(doc, serialized, "teammembers", "hr", "all")

    assert len(chunks) == 1
    meta = chunks[0].metadata
    assert meta["source"] == "mongo:teammembers"
    assert meta["collection_name"] == "teammembers"
    assert meta["record_id"] == "EMP-900"
    assert meta["department"] == "hr"
    assert meta["access_level"] == "all"
    assert meta["created_at"] == "2026-01-01T09:00:00+00:00"
    assert meta["updated_at"] == "2026-02-01T10:00:00+00:00"
    assert len(meta["content_hash"]) == 16


# ── 4. Collection Discovery & Catalog Governance Tests ─────────────────────────


def test_collection_discovery_and_categorization() -> None:
    """Verifies audit_mongo_collections properly groups collections into approved, sensitive, system, and unreviewed."""
    mock_db = MagicMock()
    mock_db.list_collection_names.return_value = [
        "invoices", "users", "otps", "salaryslips",
        "cronstates", "ratelimits", "proofUploads.chunks",
        "leads", "attendances", "accounttype_migration_snapshot_1791371360357",
    ]
    audit = audit_mongo_collections(mock_db)

    assert "invoices" in audit["approved"]
    assert "users" in audit["approved"]
    assert "otps" in audit["sensitive_excluded"]
    assert "salaryslips" in audit["sensitive_excluded"]
    assert "cronstates" in audit["system_excluded"]
    assert "ratelimits" in audit["system_excluded"]
    assert "accounttype_migration_snapshot_1791371360357" in audit["system_excluded"]
    assert "leads" in audit["unreviewed"]
    assert "attendances" in audit["unreviewed"]


def test_approved_collection_catalog_coverage() -> None:
    """Verifies that all approved collections have valid departments and RBAC access levels."""
    for name, spec in APPROVED_COLLECTIONS.items():
        assert spec.name == name
        assert spec.department in ("general", "operations", "hr", "sales", "finance", "support", "legal", "marketing")
        assert spec.access_level in ("all", "employee", "hr", "finance", "operations")


# ── 5. Ingestion Pipeline & Error Resilience Tests ─────────────────────────────


def test_invalid_record_resilience_and_fail_soft() -> None:
    """Verifies that a single malformed document does not halt ingestion of valid records."""
    mock_db = MagicMock()
    valid_doc = {"_id": "V1", "name": "Valid Service"}
    malformed_doc = None  # None/corrupt record
    mock_db["services"].find.return_value = [malformed_doc, valid_doc]

    mock_chroma = MagicMock()
    spec = APPROVED_COLLECTIONS["services"]

    count = ingest_collection_structured(mock_db, spec, mock_chroma)
    assert count == 1
    mock_chroma.upsert.assert_called_once()


def test_phase1_embedding_compatibility_with_mongo_chunks() -> None:
    """Verifies that MongoChunks are compatible with Phase 1 embed_documents and yield 384-d vectors."""
    doc = {
        "_id": "INV-777",
        "invoiceNumber": "INV-777",
        "client": {"name": "Continental Ecommerce", "amount": 95000},
    }
    serialized = serialize_mongo_document(doc, collection_name="invoices")
    chunks = chunk_record(doc, serialized, "invoices", "finance", "finance")

    ef = get_embedding_function()
    chunk_texts = [c.text for c in chunks]
    embs = ef.embed_documents(chunk_texts)

    assert len(embs) == len(chunk_texts)
    assert len(embs[0]) == 384
