"""FastAPI application entrypoint configuring CORS, rate limiting, and routes."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.chat import router as chat_router
from app.api.health import router as health_router
from app.config import settings
from app.middleware.rate_limit import limiter


def _handle_rate_limit(request: Request, exc: RateLimitExceeded) -> Response:
    """Builds rate limit exceeded JSON response."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


def _validate_collection_model_on_startup() -> None:
    """Verifies at startup that ChromaDB collection model and mode match configured settings."""
    import chromadb  # noqa: PLC0415
    from app.rag.retriever import get_collection_name  # noqa: PLC0415
    client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
    col_name = get_collection_name(settings.chunking_mode)
    try:
        col = client.get_collection(name=col_name)
    except Exception:
        return
    if col.metadata:
        stored_model = col.metadata.get("embedding_model")
        if stored_model and stored_model != settings.embedding_model:
            raise RuntimeError(
                f"Collection {col_name!r} was created with model {stored_model!r}, "
                f"cannot mix with configured {settings.embedding_model!r}"
            )
        stored_mode = col.metadata.get("chunking_mode")
        if stored_mode and stored_mode != settings.chunking_mode:
            raise RuntimeError(
                f"Collection {col_name!r} was created with chunking mode {stored_mode!r}, "
                f"cannot mix with configured {settings.chunking_mode!r}"
            )


def _validate_orchestrator_on_startup() -> None:
    """Verifies that configured orchestrator is strictly 'classic' or 'langgraph'."""
    if settings.orchestrator not in ("classic", "langgraph"):
        raise RuntimeError(
            f"Invalid ORCHESTRATOR={settings.orchestrator!r}. Must be 'classic' or 'langgraph'."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Runs startup checks and lifespan management."""
    _validate_orchestrator_on_startup()
    _validate_collection_model_on_startup()
    yield


def create_app() -> FastAPI:
    """Instantiates and configures the FastAPI application."""
    application = FastAPI(
        title="Internal Employee Chatbot Service",
        description="RAG-powered internal assistant with role-filtered document access",
        version="1.0.0",
        lifespan=lifespan,
    )

    # State and handlers for rate limiting
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _handle_rate_limit)
    application.add_middleware(SlowAPIMiddleware)

    # CORS configuration allowing only origins from settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Mount routes
    application.include_router(health_router)
    application.include_router(chat_router)

    return application


app = create_app()
