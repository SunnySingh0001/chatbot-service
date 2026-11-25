"""Authenticated RBAC-enforced REST endpoints for dynamic schema, entity exploration, pagination, and export."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth.jwt import get_current_user
from app.config import settings
from app.middleware.rate_limit import rate_limit_guard
from app.models.entities import (
    BatchPageResponse,
    DatabaseSchemaResponse,
    EntityDetailsResponse,
    EntityIdentity,
)
from app.models.schemas import AuthenticatedUser
from app.rag.entity_tools import (
    discover_database_schema,
    export_entity_data,
    get_entity_details,
    get_next_batch,
    search_entity,
)

_logger = logging.getLogger("chatbot.api.entities")
router = APIRouter(prefix="/entities", tags=["Entities"], dependencies=[Depends(rate_limit_guard)])


def _ensure_staff_access(user: AuthenticatedUser) -> None:
    """Blocks client-role callers and enforces tenant claim presence when strict isolation is enabled."""
    if user.role.lower() == "client":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Entity Explorer is restricted to internal company staff roles.",
        )
    if settings.require_tenant_isolation and not user.company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Verified company_id claim is required for Entity Explorer.",
        )


@router.get("/schema", response_model=DatabaseSchemaResponse)
def get_database_schema(
    collection: str | None = Query(default=None, description="Optional approved collection filter"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> DatabaseSchemaResponse:
    """Returns RBAC-filtered dynamic MongoDB schema, indexes, and relationship mappings."""
    _ensure_staff_access(current_user)
    return discover_database_schema(role=current_user.role, collection_filter=collection)


@router.get("/search", response_model=list[EntityIdentity])
def search_entities_endpoint(
    q: str = Query(..., min_length=1, max_length=200, description="Entity name, code, email, phone, or ID"),
    entity_type: str | None = Query(default=None, description="Optional entity type filter"),
    limit: int = Query(default=15, ge=1, le=50),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> list[EntityIdentity]:
    """Searches authorized MongoDB collections for matching entity candidates."""
    _ensure_staff_access(current_user)
    return search_entity(
        q,
        role=current_user.role,
        entity_type=entity_type,
        limit=limit,
        client_id=current_user.client_id,
        company_id=current_user.company_id,
    )


@router.get("/details", response_model=EntityDetailsResponse)
def get_entity_details_endpoint(
    identifier: str = Query(..., min_length=1, max_length=200, description="Entity name, business code, or ObjectId"),
    entity_type: str | None = Query(default=None),
    max_depth: int = Query(default=2, ge=1, le=3),
    batch_size: int = Query(default=20, ge=1, le=100),
    include_empty: bool = Query(default=False),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> EntityDetailsResponse:
    """Returns canonical entity details, multi-hop relationships, counts, and completeness verification."""
    _ensure_staff_access(current_user)
    return get_entity_details(
        identifier=identifier,
        role=current_user.role,
        entity_type=entity_type,
        max_depth=max_depth,
        batch_size=batch_size,
        include_empty=include_empty,
        client_id=current_user.client_id,
        company_id=current_user.company_id,
    )


@router.get("/records", response_model=BatchPageResponse)
def get_entity_records_batch(
    identifier: str = Query(..., min_length=1, max_length=200),
    relation_name: str = Query(..., min_length=1, max_length=120),
    entity_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = Query(default=None),
    batch_size: int = Query(default=25, ge=1, le=100),
    q: str | None = Query(default=None, max_length=100, description="Optional record text filter"),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> BatchPageResponse:
    """Retrieves next paginated batch of related records for an entity relationship."""
    _ensure_staff_access(current_user)
    page = get_next_batch(
        identifier=identifier,
        relation_name=relation_name,
        role=current_user.role,
        cursor=cursor,
        offset=offset,
        batch_size=batch_size,
        search_filter=q,
        entity_type=entity_type,
        client_id=current_user.client_id,
        company_id=current_user.company_id,
    )
    if page is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Entity or accessible relationship mapping not found.",
        )
    return page


@router.get("/export")
def export_entity_endpoint(
    identifier: str = Query(..., min_length=1, max_length=200),
    fmt: Literal["csv", "json"] = Query(default="csv"),
    relation_name: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    max_records: int = Query(default=2000, ge=1, le=5000),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    """Exports complete RBAC-filtered entity records as downloadable CSV or JSON."""
    _ensure_staff_access(current_user)
    try:
        payload, media_type, filename = export_entity_data(
            identifier=identifier,
            role=current_user.role,
            fmt=fmt,
            relation_name=relation_name,
            max_records=max_records,
            entity_type=entity_type,
            client_id=current_user.client_id,
            company_id=current_user.company_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    _logger.info("Audit entity export role=%s fmt=%s filename=%s bytes=%d", current_user.role, fmt, filename, len(payload))
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
