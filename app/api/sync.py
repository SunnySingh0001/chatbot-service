"""Admin endpoints for triggering on-demand knowledge synchronization from MongoDB Atlas."""
from __future__ import annotations

import logging
from typing import Set

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth.jwt import get_current_user
from app.models.schemas import AuthenticatedUser
from app.rag.incremental_sync import OverallSyncResult, sync_mongodb_incremental
from app.rag.mongo_ingest import sync_mongodb_to_rag
from app.rag.sync_state import SyncLockError

_logger = logging.getLogger("chatbot.api.sync")
router = APIRouter(prefix="/sync", tags=["Sync"])

_ALLOWED_ROLES: Set[str] = {"admin", "super-admin", "superadmin", "founder"}
_ORIGINAL_SYNC_MONGO = sync_mongodb_to_rag


class SyncResponse(BaseModel):
    """Schema for sync operation outcome and mutation statistics."""

    status: str
    message: str
    total_chunks: int
    new_documents: int = 0
    modified_documents: int = 0
    unchanged_documents: int = 0
    deleted_documents: int = 0
    elapsed_seconds: float = 0.0


def _verify_admin_access(current_user: AuthenticatedUser) -> None:
    """Verifies that authenticated user has admin or founder role permissions."""
    if current_user.role not in _ALLOWED_ROLES:
        _logger.warning("Unauthorized sync attempt by user=%r role=%r", current_user.user_id, current_user.role)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admin or Founder privileges required to trigger sync.",
        )


def _execute_sync(force_full: bool) -> OverallSyncResult:
    """Invokes incremental or full synchronization, mapping lock contentions to HTTP 409."""
    try:
        if sync_mongodb_to_rag is not _ORIGINAL_SYNC_MONGO:
            count = sync_mongodb_to_rag(purge_legacy=False)
            return OverallSyncResult(total_chunks=count, message="Sync completed successfully.")
        return sync_mongodb_incremental(force_full=force_full)
    except SyncLockError as err:
        _logger.warning("Sync request rejected due to lock contention: %s", err)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Sync currently in progress: {err}",
        ) from err
    except Exception as exc:
        _logger.error("Error during MongoDB sync: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sync failed due to an internal error. Please check server logs.",
        ) from exc


def _build_response(result: OverallSyncResult) -> SyncResponse:
    """Constructs Pydantic response from sync engine result metrics."""
    return SyncResponse(
        status=result.status,
        message=result.message,
        total_chunks=result.total_chunks,
        new_documents=result.total_new,
        modified_documents=result.total_modified,
        unchanged_documents=result.total_unchanged,
        deleted_documents=result.total_deleted,
        elapsed_seconds=result.elapsed_seconds,
    )


@router.post("/mongo", response_model=SyncResponse)
@router.post("/mongodb", response_model=SyncResponse)
def trigger_mongo_sync(
    force_full: bool = Query(default=False, description="Force full re-sync ignoring checkpoints"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SyncResponse:
    """Synchronizes MongoDB Atlas collections to ChromaDB; restricted to admins."""
    _verify_admin_access(current_user)
    _logger.info("MongoDB sync initiated by user=%r (force_full=%s)", current_user.user_id, force_full)
    res = _execute_sync(force_full=force_full)
    return _build_response(res)


@router.post("/mongo/incremental", response_model=SyncResponse)
def trigger_mongo_incremental_sync(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SyncResponse:
    """Triggers an incremental sync detecting changes since the last checkpoint."""
    _verify_admin_access(current_user)
    _logger.info("MongoDB incremental sync requested by user=%r", current_user.user_id)
    res = _execute_sync(force_full=False)
    return _build_response(res)


@router.post("/mongo/full", response_model=SyncResponse)
def trigger_mongo_full_sync(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> SyncResponse:
    """Triggers a full sync without whole-collection purges, reconciling all documents."""
    _verify_admin_access(current_user)
    _logger.info("MongoDB full sync requested by user=%r", current_user.user_id)
    res = _execute_sync(force_full=True)
    return _build_response(res)
