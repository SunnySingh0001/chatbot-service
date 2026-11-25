"""Server-side conversation session manager enforcing ownership, IDOR defense, and message persistence."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import settings
from app.rag.history_budget import estimate_tokens

_logger = logging.getLogger("chatbot.rag.session_manager")

# In-memory session store used as fallback only in explicit development/test mode
_MEMORY_SESSIONS: dict[str, dict[str, Any]] = {}
_MONGO_INDEXED: bool = False
_MONGO_COL: Collection[dict[str, Any]] | None = None
_MONGO_TRIED: bool = False


def _get_utc_now_iso() -> str:
    """Returns the current UTC timestamp formatted as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def validate_session_id(session_id: str | None) -> str | None:
    """Validates session ID length and format, rejecting malformed input with 400 Bad Request."""
    if not session_id or not session_id.strip():
        return None
    clean = session_id.strip()
    if len(clean) > 128 or not re.match(r"^[a-zA-Z0-9_-]+$", clean):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid session ID format: must be 1-128 alphanumeric, hyphen, or underscore characters",
        )
    return clean


def is_session_expired(session_doc: dict[str, Any], max_age_hours: int | None = None) -> bool:
    """Determines whether a session has expired server-side based on updated_at timestamp."""
    ttl_hours = max_age_hours if max_age_hours is not None else getattr(settings, "session_ttl_hours", 24)
    updated_str = session_doc.get("updated_at")
    if not updated_str:
        return False
    try:
        updated_at = datetime.fromisoformat(updated_str)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
        return age_seconds > (ttl_hours * 3600)
    except Exception:
        return False


def _enforce_production_persistence_check(exc: Exception | None = None) -> None:
    """Fails closed in production environments if MongoDB persistence is unavailable."""
    if getattr(settings, "app_env", "").lower() == "production":
        _logger.error("Production MongoDB persistence failure: %s", type(exc).__name__ if exc else "Unknown")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database persistence unavailable: session operation cannot proceed in production",
        )


def _get_mongo_collection() -> Collection[dict[str, Any]] | None:
    """Attempts to acquire MongoDB chat_sessions collection, caching status to avoid latency."""
    global _MONGO_INDEXED, _MONGO_COL, _MONGO_TRIED
    if _MONGO_TRIED:
        return _MONGO_COL
    _MONGO_TRIED = True
    if not settings.mongodb_uri:
        return None
    try:
        from app.rag.mongo_ingest import _connect_mongo  # noqa: PLC0415

        db: Database[dict[str, Any]] = _connect_mongo()
        col = db["chat_sessions"]
        if not _MONGO_INDEXED:
            col.create_index([("session_id", 1), ("user_id", 1)], unique=True)
            col.create_index([("user_id", 1), ("updated_at", -1)])
            _MONGO_INDEXED = True
        _MONGO_COL = col
        return _MONGO_COL
    except Exception as exc:
        _logger.debug("MongoDB session storage unavailable (%s)", type(exc).__name__)
        _MONGO_COL = None
        return None


def reset_memory_store() -> None:
    """Clears all in-memory sessions; primarily used to isolate automated tests."""
    global _MEMORY_SESSIONS
    _MEMORY_SESSIONS.clear()


class SessionManager:
    """Manages chat session lifecycle, ownership validation, and message persistence."""

    def __init__(self, col: Collection[dict[str, Any]] | None = None) -> None:
        """Initializes SessionManager with an optional explicit collection."""
        self._col = col

    def _col_or_fallback(self) -> Collection[dict[str, Any]] | None:
        """Resolves active MongoDB collection, failing closed in production if unavailable."""
        col = self._col if self._col is not None else _get_mongo_collection()
        if col is None:
            _enforce_production_persistence_check(RuntimeError("MongoDB collection unavailable"))
        return col

    def get_session(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        """Retrieves session record, validating that authenticated user matches owner (IDOR defense)."""
        clean_sid = validate_session_id(session_id)
        if not clean_sid:
            return None

        col = self._col_or_fallback()
        if col is not None:
            try:
                rec = col.find_one({"session_id": clean_sid})
                if not rec:
                    return None
                self._verify_ownership(rec, user_id, clean_sid)
                return None if is_session_expired(rec) else rec
            except HTTPException:
                raise
            except Exception as exc:
                _enforce_production_persistence_check(exc)
                _logger.warning("MongoDB find_one error (%s); falling back to memory", type(exc).__name__)

        rec = _MEMORY_SESSIONS.get(clean_sid)
        if not rec:
            return None
        self._verify_ownership(rec, user_id, clean_sid)
        return None if is_session_expired(rec) else rec

    def _verify_ownership(self, rec: dict[str, Any], user_id: str, session_id: str) -> None:
        """Verifies session ownership, raising 403 Forbidden on user mismatch."""
        if rec.get("user_id") != user_id:
            _logger.warning("IDOR attempt detected: user %r accessed session %r", user_id, session_id)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: session belongs to another user",
            )

    def create_session(self, session_id: str | None, user_id: str, role: str) -> dict[str, Any]:
        """Creates a new session record tied strictly to the authenticated user ID."""
        now = _get_utc_now_iso()
        valid_sid = validate_session_id(session_id)
        sid = valid_sid if valid_sid else str(uuid.uuid4())
        doc: dict[str, Any] = {
            "session_id": sid,
            "user_id": user_id,
            "role": role,
            "created_at": now,
            "updated_at": now,
            "messages": [],
            "summary": None,
            "metadata": {"turn_count": 0},
        }

        col = self._col_or_fallback()
        if col is not None:
            try:
                col.update_one({"session_id": sid}, {"$setOnInsert": doc}, upsert=True)
                return doc
            except Exception as exc:
                _enforce_production_persistence_check(exc)
                _logger.warning("MongoDB session insert error (%s); saving to memory", type(exc).__name__)

        _MEMORY_SESSIONS[sid] = doc
        return doc

    def get_or_create_session(
        self,
        session_id: str | None,
        user_id: str,
        role: str,
    ) -> tuple[str, list[dict[str, Any]], str | None]:
        """Resolves or creates a session, returning (session_id, messages, summary)."""
        valid_sid = validate_session_id(session_id)
        if valid_sid:
            existing = self.get_session(valid_sid, user_id)
            if existing:
                # Prevent cross-role session history leakage if the user's role changed
                if existing.get("role") and existing.get("role") != role:
                    _logger.warning(
                        "Cross-role session reuse blocked for user=%r: session role=%r vs current=%r",
                        user_id,
                        existing.get("role"),
                        role,
                    )
                    new_doc = self.create_session(None, user_id, role)
                    return str(new_doc["session_id"]), [], None
                return (
                    str(existing["session_id"]),
                    list(existing.get("messages", [])),
                    existing.get("summary"),
                )
            # Session was missing or expired; create a new session under a fresh UUID
            new_doc = self.create_session(None, user_id, role)
            return str(new_doc["session_id"]), [], None

        new_doc = self.create_session(None, user_id, role)
        return str(new_doc["session_id"]), [], None

    def save_user_message(
        self,
        session_id: str,
        user_id: str,
        content: str,
        token_count: int | None = None,
    ) -> str:
        """Appends a validated user message to authenticated session history, returning message_id."""
        self.get_session(session_id, user_id)
        tokens = token_count if token_count is not None else estimate_tokens(content)
        msg_id = str(uuid.uuid4())
        msg_doc: dict[str, Any] = {
            "message_id": msg_id,
            "role": "user",
            "content": content,
            "timestamp": _get_utc_now_iso(),
            "token_count": tokens,
            "metadata": {},
        }
        self._append_message(session_id, user_id, msg_doc)
        return msg_id

    def save_assistant_message(
        self,
        session_id: str,
        user_id: str,
        content: str,
        sources: list[str] | None = None,
        token_count: int | None = None,
        metadata: dict[str, Any] | None = None,
        user_message_id: str | None = None,
    ) -> None:
        """Appends a verified assistant message strictly after security validation succeeds."""
        self.get_session(session_id, user_id)
        tokens = token_count if token_count is not None else estimate_tokens(content)
        meta = {"sources": sources or [], **(metadata or {})}
        if user_message_id:
            meta["reply_to"] = user_message_id

        msg_doc: dict[str, Any] = {
            "message_id": str(uuid.uuid4()),
            "role": "assistant",
            "content": content,
            "timestamp": _get_utc_now_iso(),
            "token_count": tokens,
            "metadata": meta,
        }
        self._append_message(session_id, user_id, msg_doc)

    def _append_message(self, session_id: str, user_id: str, msg_doc: dict[str, Any]) -> None:
        """Internal helper writing a message turn to storage and bumping updated_at."""
        now = _get_utc_now_iso()
        col = self._col_or_fallback()
        if col is not None:
            try:
                col.update_one(
                    {"session_id": session_id, "user_id": user_id},
                    {
                        "$push": {"messages": msg_doc},
                        "$set": {"updated_at": now},
                        "$inc": {"metadata.turn_count": 1},
                    },
                )
                return
            except Exception as exc:
                _enforce_production_persistence_check(exc)
                _logger.warning("MongoDB message append error (%s); updating in-memory", type(exc).__name__)

        rec = _MEMORY_SESSIONS.get(session_id)
        if rec and rec.get("user_id") == user_id:
            rec.setdefault("messages", []).append(msg_doc)
            rec["updated_at"] = now
            rec.setdefault("metadata", {})["turn_count"] = len(rec["messages"])

    def update_summary(self, session_id: str, user_id: str, summary: str) -> None:
        """Updates the conversation summary for a session after ownership verification."""
        self.get_session(session_id, user_id)
        col = self._col_or_fallback()
        if col is not None:
            try:
                col.update_one(
                    {"session_id": session_id, "user_id": user_id},
                    {"$set": {"summary": summary, "updated_at": _get_utc_now_iso()}},
                )
                return
            except Exception as exc:
                _enforce_production_persistence_check(exc)

        rec = _MEMORY_SESSIONS.get(session_id)
        if rec and rec.get("user_id") == user_id:
            rec["summary"] = summary

    def clear_session(self, session_id: str, user_id: str) -> bool:
        """Permanently deletes a conversation session owned by the authenticated user."""
        self.get_session(session_id, user_id)
        col = self._col_or_fallback()
        if col is not None:
            try:
                res = col.delete_one({"session_id": session_id, "user_id": user_id})
                return res.deleted_count > 0
            except Exception as exc:
                _enforce_production_persistence_check(exc)

        if session_id in _MEMORY_SESSIONS and _MEMORY_SESSIONS[session_id].get("user_id") == user_id:
            del _MEMORY_SESSIONS[session_id]
            return True
        return False

    def get_entity_context(self, session_id: str, user_id: str) -> dict[str, Any]:
        """Returns structured EntityConversationContext saved in session metadata."""
        rec = self.get_session(session_id, user_id)
        if not rec:
            return {}
        ctx = (rec.get("metadata") or {}).get("entity_context")
        return dict(ctx) if isinstance(ctx, dict) else {}

    def update_entity_context(
        self,
        session_id: str,
        user_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Merges and persists structured entity, filter, analytics, and continuation state."""
        rec = self.get_session(session_id, user_id)
        if not rec:
            return {}
        merged = dict((rec.get("metadata") or {}).get("entity_context") or {})
        merged.update(updates)
        now = _get_utc_now_iso()
        col = self._col_or_fallback()
        if col is not None:
            try:
                col.update_one(
                    {"session_id": session_id, "user_id": user_id},
                    {"$set": {"metadata.entity_context": merged, "updated_at": now}},
                )
                return merged
            except Exception as exc:
                _enforce_production_persistence_check(exc)
        mem_rec = _MEMORY_SESSIONS.get(session_id)
        if mem_rec and mem_rec.get("user_id") == user_id:
            mem_rec.setdefault("metadata", {})["entity_context"] = merged
            mem_rec["updated_at"] = now
        return merged

    def list_user_sessions(
        self,
        user_id: str,
        *,
        search: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Lists sessions owned strictly by user_id with optional keyword and archive filtering."""
        col = self._col_or_fallback()
        q_clean = search.strip().lower() if search else ""
        items: list[dict[str, Any]] = []
        if col is not None:
            try:
                docs = list(col.find({"user_id": user_id}).sort("updated_at", -1).limit(200))
            except Exception as exc:
                _enforce_production_persistence_check(exc)
                docs = [v for v in _MEMORY_SESSIONS.values() if v.get("user_id") == user_id]
        else:
            docs = [v for v in _MEMORY_SESSIONS.values() if v.get("user_id") == user_id]

        for doc in docs:
            if is_session_expired(doc):
                continue
            meta = doc.get("metadata") or {}
            if not include_archived and meta.get("archived"):
                continue
            msgs = doc.get("messages") or []
            title = str(meta.get("title") or (msgs[0].get("content", "")[:60] if msgs else "New Conversation"))
            if q_clean and q_clean not in title.lower() and not any(q_clean in str(m.get("content", "")).lower() for m in msgs):
                continue
            items.append({
                "session_id": str(doc["session_id"]),
                "title": title,
                "role": str(doc.get("role", "")),
                "pinned": bool(meta.get("pinned", False)),
                "archived": bool(meta.get("archived", False)),
                "turn_count": int(meta.get("turn_count", len(msgs))),
                "created_at": str(doc.get("created_at", "")),
                "updated_at": str(doc.get("updated_at", "")),
                "entity_context": meta.get("entity_context") or {},
            })
            if len(items) >= limit:
                break
        items.sort(key=lambda x: (not x["pinned"], x["updated_at"]), reverse=False)
        items.sort(key=lambda x: x["pinned"], reverse=True)
        return items

    def update_session_metadata(
        self,
        session_id: str,
        user_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> dict[str, Any]:
        """Updates session title, pin, or archive flags after ownership check."""
        self.get_session(session_id, user_id)
        patch: dict[str, Any] = {}
        if title is not None:
            patch["metadata.title"] = title.strip()[:120]
        if pinned is not None:
            patch["metadata.pinned"] = bool(pinned)
        if archived is not None:
            patch["metadata.archived"] = bool(archived)
        now = _get_utc_now_iso()
        col = self._col_or_fallback()
        if col is not None and patch:
            col.update_one({"session_id": session_id, "user_id": user_id}, {"$set": {**patch, "updated_at": now}})
        rec = _MEMORY_SESSIONS.get(session_id)
        if rec and rec.get("user_id") == user_id:
            meta = rec.setdefault("metadata", {})
            if title is not None:
                meta["title"] = title.strip()[:120]
            if pinned is not None:
                meta["pinned"] = bool(pinned)
            if archived is not None:
                meta["archived"] = bool(archived)
            rec["updated_at"] = now
        return self.get_session(session_id, user_id) or {}


# Module singleton
session_manager = SessionManager()
