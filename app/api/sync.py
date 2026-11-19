"""Admin endpoints for triggering on-demand knowledge synchronization from MongoDB Atlas."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth.jwt import get_current_user
from app.models.schemas import AuthenticatedUser
from app.rag.mongo_ingest import sync_mongodb_to_rag

_logger = logging.getLogger("chatbot.api.sync")
router = APIRouter(prefix="/sync", tags=["Sync"])


class SyncResponse(BaseModel):
    """Schema for sync operation outcome."""

    status: str
    message: str
    total_chunks: int


@router.post("/mongo", response_model=SyncResponse)
def trigger_mongo_sync(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SyncResponse:
    """Synchronizes all MongoDB Atlas collections to ChromaDB; restricted to admins."""
    allowed_roles = {"admin", "super-admin", "superadmin", "founder"}
    if current_user.role not in allowed_roles:
        _logger.warning("Unauthorized sync attempt by user=%r role=%r", current_user.user_id, current_user.role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admin or Founder privileges required to trigger sync.",
        )

    _logger.info("MongoDB sync initiated by user=%r (role=%r)", current_user.user_id, current_user.role)
    try:
        total = sync_mongodb_to_rag(purge_legacy=False)
        return SyncResponse(
            status="ok",
            message="MongoDB Atlas knowledge base synchronized successfully.",
            total_chunks=total,
        )
    except Exception as exc:
        _logger.error("Error during MongoDB sync: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {exc}",
        ) from exc
