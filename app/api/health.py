"""Health check endpoint to verify service availability and status."""
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.models.schemas import HealthResponse
from app.rag.llm import get_active_llm_provider

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Returns current service health status, API version, orchestrator, and active LLM provider."""
    return HealthResponse(
        status="ok",
        version="1.0.0",
        orchestrator=settings.orchestrator,
        llm_provider=get_active_llm_provider(),
    )
