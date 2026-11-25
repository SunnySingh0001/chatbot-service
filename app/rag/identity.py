"""Resolves and caches authenticated user identity attributes from MongoDB Atlas safely."""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import Any  # Pymongo user documents contain dynamic BSON fields

from pymongo import MongoClient, ReadPreference

from app.config import settings

_logger = logging.getLogger("chatbot.rag.identity")
_IDENTITY_MAX_SIZE: int = 512
_IDENTITY_TTL_SECONDS: float = 300.0
_IDENTITY_CACHE: OrderedDict[str, tuple[float, dict[str, str]]] = OrderedDict()
_CLIENT_POOL: MongoClient[dict[str, Any]] | None = None


def invalidate_identity_cache(user_id: str | None = None) -> None:
    """Clears all cached user identities or entries matching a given user_id."""
    if user_id is None:
        _IDENTITY_CACHE.clear()
        return
    prefix = f"{user_id}:"
    for key in [k for k in _IDENTITY_CACHE if k.startswith(prefix)]:
        _IDENTITY_CACHE.pop(key, None)


def _get_mongo_client() -> MongoClient[dict[str, Any]]:
    """Returns pooled SecondaryPreferred MongoClient configured with short timeout."""
    global _CLIENT_POOL  # noqa: PLW0603
    if _CLIENT_POOL is None:
        _CLIENT_POOL = MongoClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000,
            read_preference=ReadPreference.SECONDARY_PREFERRED,
        )
    return _CLIENT_POOL


def _exact_ci_regex(value: str) -> dict[str, str]:
    """Constructs an escaped, start-to-end anchored case-insensitive regex filter."""
    return {"$regex": f"^{re.escape(value)}$", "$options": "i"}


def _apply_company_scope(query: dict[str, Any], company_id: str | None) -> dict[str, Any]:
    """Scopes user identity queries to the verified tenant company/workspace when provided."""
    if not company_id:
        return query
    return {
        "$and": [
            query,
            {"$or": [{"companyId": company_id}, {"company_id": company_id}, {"workspaceId": company_id}]},
        ]
    }


def _lookup_employee(user_id: str, company_id: str | None = None) -> dict[str, str] | None:
    """Looks up employee details by username, email, or name safely without regex injection."""
    clean_id = user_id.strip()
    if not clean_id:
        return None
    norm_name = clean_id.replace("_", " ").replace(".", " ")
    db = _get_mongo_client()[settings.mongodb_db_name]
    base_q: dict[str, Any] = {
        "$or": [
            {"username": clean_id},
            {"employeeId": clean_id},
            {"email": _exact_ci_regex(clean_id)},
            {"name": _exact_ci_regex(norm_name)},
        ]
    }
    user = db["users"].find_one(_apply_company_scope(base_q, company_id), max_time_ms=2500)
    if not user:
        return None
    desig = user.get("designation") or user.get("adminRole") or ""
    return {
        "user_id": clean_id,
        "name": str(user.get("name", norm_name.title())),
        "employee_id": str(user.get("employeeId", "")),
        "designation": str(desig).strip(),
        "department": str(user.get("department", "")),
        "email": str(user.get("email", "")),
        "role": str(user.get("role", "employee")),
        "company": "Fakhri IT Services",
    }


def _lookup_client(
    user_id: str,
    client_id: str | None,
    company_id: str | None = None,
) -> dict[str, str] | None:
    """Looks up client account details strictly constrained by client_id or escaped identity."""
    clean_id = user_id.strip()
    query: dict[str, Any] = {"role": "client"}
    if client_id:
        from bson import ObjectId  # noqa: PLC0415

        try:
            query["_id"] = ObjectId(client_id)
        except Exception:
            query["company"] = client_id
    else:
        if not clean_id:
            return None
        norm_name = clean_id.replace("_", " ").replace(".", " ")
        query["$or"] = [
            {"email": _exact_ci_regex(clean_id)},
            {"name": _exact_ci_regex(norm_name)},
            {"company": _exact_ci_regex(norm_name)},
        ]
    db = _get_mongo_client()[settings.mongodb_db_name]
    user = db["users"].find_one(_apply_company_scope(query, company_id), max_time_ms=2500)
    if not user:
        return None
    return {
        "user_id": clean_id,
        "name": str(user.get("name", "")),
        "client_id": str(user.get("_id", client_id or "")),
        "company": str(user.get("company", "")),
        "plan": str(user.get("plan", "")),
        "manager": str(user.get("manager", "")),
        "marketplace": str(user.get("marketplace", "")),
        "status": str(user.get("status", "")),
        "role": "client",
    }


def _cache_put(key: str, val: dict[str, str], now: float) -> None:
    """Inserts identity entry into bounded LRU+TTL cache, evicting oldest when full."""
    _IDENTITY_CACHE[key] = (now, val)
    _IDENTITY_CACHE.move_to_end(key)
    while len(_IDENTITY_CACHE) > _IDENTITY_MAX_SIZE:
        _IDENTITY_CACHE.popitem(last=False)


def resolve_user_identity(
    user_id: str,
    role: str,
    client_id: str | None = None,
    company_id: str | None = None,
) -> dict[str, str]:
    """Resolves authenticated user profile attributes with bounded TTL caching."""
    cache_key = f"{user_id}:{role}:{client_id or ''}:{company_id or ''}"
    now = time.monotonic()
    cached = _IDENTITY_CACHE.get(cache_key)
    if cached is not None:
        ts, payload = cached
        if (now - ts) < _IDENTITY_TTL_SECONDS:
            _IDENTITY_CACHE.move_to_end(cache_key)
            return payload
        _IDENTITY_CACHE.pop(cache_key, None)

    fallback_name = user_id.replace("_", " ").replace(".", " ").title()
    fallback: dict[str, str] = {
        "user_id": user_id,
        "name": fallback_name,
        "role": role,
        "employee_id": "",
        "designation": "",
        "department": "",
        "email": "",
        "company": company_id or "",
    }

    if settings.llm_provider == "mock" or not settings.mongodb_uri:
        _cache_put(cache_key, fallback, now)
        return fallback

    try:
        result = (
            _lookup_client(user_id, client_id, company_id=company_id)
            if role == "client"
            else _lookup_employee(user_id, company_id=company_id)
        )
        identity = result if result else fallback
    except Exception as exc:
        _logger.warning("Failed to lookup user identity (%s)", type(exc).__name__)
        identity = fallback

    _cache_put(cache_key, identity, now)
    return identity
