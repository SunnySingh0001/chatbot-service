"""Structured MongoDB document serialization preserving BSON types, relationships, and security boundaries."""
from __future__ import annotations

import datetime
from typing import Any

from bson import Decimal128, ObjectId

_SENSITIVE_FIELD_NAMES: frozenset[str] = frozenset({
    "password", "passwordhash", "hash", "salt", "secret", "apikey",
    "token", "accesstoken", "refreshtoken", "otp", "ssn", "bankaccount",
    "bankaccountnumber", "cvv", "pin", "authcode", "signingkey",
    "paymentmethod", "paymentmethods", "accountaccessurl", "merchanttoken",
    "pushsubscriptions", "failedloginattempts",
})


def is_sensitive_field(key: str) -> bool:
    """Checks whether a dictionary key corresponds to a sensitive credential or secret."""
    cleaned = key.lower().replace("_", "").replace("-", "")
    return any(s in cleaned for s in _SENSITIVE_FIELD_NAMES)


def normalize_bson_value(val: object) -> object:
    """Converts MongoDB BSON data types into standard Python serializable primitives."""
    if isinstance(val, ObjectId):
        return str(val)
    if isinstance(val, (datetime.datetime, datetime.date)):
        return val.isoformat()
    if isinstance(val, Decimal128):
        return float(val.to_decimal())
    if isinstance(val, bytes):
        return f"<bytes len={len(val)}>"
    return val


def _format_scalar(val: object) -> str:
    """Formats a single primitive scalar value into clean readable text."""
    normalized = normalize_bson_value(val)
    if normalized is None:
        return "null"
    if isinstance(normalized, bool):
        return "true" if normalized else "false"
    return str(normalized)


def _format_list_items(items: list[object], indent: int = 0) -> list[str]:
    """Recursively formats a list of objects or scalars with list-item bullet points."""
    lines: list[str] = []
    prefix = " " * indent
    for item in items:
        norm_item = normalize_bson_value(item)
        if isinstance(norm_item, dict):
            child_lines = _format_mapping(norm_item, indent=indent + 2)
            if child_lines:
                lines.append(f"{prefix}- {child_lines[0].lstrip()}")
                lines.extend(child_lines[1:])
        elif isinstance(norm_item, list):
            lines.append(f"{prefix}-")
            lines.extend(_format_list_items(norm_item, indent=indent + 2))
        else:
            lines.append(f"{prefix}- {_format_scalar(norm_item)}")
    return lines


def _format_mapping(data: dict[str, object], indent: int = 0) -> list[str]:
    """Recursively formats dictionary key-values into structured YAML-like indented markdown."""
    lines: list[str] = []
    prefix = " " * indent
    for key, val in data.items():
        if is_sensitive_field(key):
            continue
        norm_val = normalize_bson_value(val)
        if isinstance(norm_val, dict):
            nested = _format_mapping(norm_val, indent=indent + 2)
            if nested:
                lines.append(f"{prefix}{key}:")
                lines.extend(nested)
        elif isinstance(norm_val, list):
            if norm_val:
                lines.append(f"{prefix}{key}:")
                lines.extend(_format_list_items(norm_val, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: []")
        else:
            lines.append(f"{prefix}{key}: {_format_scalar(norm_val)}")
    return lines


def extract_record_identifier(doc: dict[str, Any]) -> str:
    """Extracts business or technical primary key identifier from a MongoDB record."""
    for candidate in ("recordId", "invoiceNumber", "employeeId", "code", "_id", "slug"):
        val = doc.get(candidate)
        if val is not None and str(val).strip():
            return str(val).strip()
    return str(doc.get("name") or doc.get("title") or "unknown_id").strip()


def serialize_mongo_document(
    doc: dict[str, Any],
    collection_name: str,
    title: str | None = None,
) -> str:
    """Serializes a MongoDB document into structured, relationship-preserving markdown text."""
    rec_id = extract_record_identifier(doc)
    doc_title = title or str(doc.get("title") or doc.get("name") or rec_id).strip()

    header = f"# Record: {doc_title} ({collection_name})\nRecord ID: {rec_id}\nCollection: {collection_name}\n"
    content_dict = {k: v for k, v in doc.items() if not is_sensitive_field(k)}
    body_lines = _format_mapping(content_dict, indent=0)
    return f"{header}\n" + "\n".join(body_lines) + "\n"
