"""FastAPI application entrypoint configuring CORS, rate limiting, and routes."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.chat import router as chat_router
from app.api.entities import router as entities_router
from app.api.health import router as health_router
from app.api.sync import router as sync_router
from app.config import settings
from app.middleware.rate_limit import limiter


def _handle_rate_limit(request: Request, exc: RateLimitExceeded) -> Response:
    """Builds rate limit exceeded JSON response."""
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please try again later."},
    )


def _handle_unhandled_exception(request: Request, exc: Exception) -> Response:
    """Handles uncaught server exceptions with safe generic JSON response and tracking ID."""
    import uuid  # noqa: PLC0415
    request_id = f"req-{uuid.uuid4().hex[:12]}"
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "code": "INTERNAL_ERROR",
            "message": "Something went wrong. Please try again.",
            "request_id": request_id,
            "detail": "Something went wrong. Please try again.",
        },
    )


def _handle_http_exception(request: Request, exc: HTTPException) -> Response:
    """Handles HTTPExceptions ensuring 5xx errors return safe structured JSON."""
    if exc.status_code >= 500:
        import uuid  # noqa: PLC0415
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        msg = str(exc.detail) if exc.status_code == 503 else "Something went wrong. Please try again."
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": True,
                "code": "SERVICE_UNAVAILABLE" if exc.status_code == 503 else "INTERNAL_ERROR",
                "message": msg,
                "request_id": request_id,
                "detail": str(exc.detail),
            },
            headers=exc.headers,
        )
    content = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=exc.headers,
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
    """Verifies that configured orchestrator is strictly 'langgraph'."""
    if settings.orchestrator != "langgraph":
        raise RuntimeError(
            f"Invalid ORCHESTRATOR={settings.orchestrator!r}. Only 'langgraph' is supported."
        )


import asyncio


async def _check_and_trigger_startup_sync() -> None:
    """Verifies collection has indexed chunks on startup; triggers background sync if unpopulated."""
    import chromadb  # noqa: PLC0415
    from app.rag.mongo_ingest import sync_mongodb_to_rag  # noqa: PLC0415
    from app.rag.retriever import get_collection_name  # noqa: PLC0415

    try:
        client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        col_name = get_collection_name(settings.chunking_mode)
        col = client.get_or_create_collection(name=col_name)
        if col.count() < 50:
            import logging
            logging.getLogger("chatbot.main").info(
                "Collection %s has %d chunks (< 50); running initial MongoDB sync in background",
                col_name,
                col.count(),
            )
            await asyncio.to_thread(sync_mongodb_to_rag, purge_legacy=False)
    except Exception as exc:
        import logging
        logging.getLogger("chatbot.main").warning("Startup sync check encountered error: %s", exc)


async def _periodic_mongo_sync() -> None:
    """Periodically synchronizes MongoDB Atlas data to ChromaDB every 6 hours."""
    from app.rag.mongo_ingest import sync_mongodb_to_rag  # noqa: PLC0415

    while True:
        try:
            await asyncio.sleep(6 * 3600)
            await asyncio.to_thread(sync_mongodb_to_rag, purge_legacy=False)
        except asyncio.CancelledError:
            break
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Runs startup checks and lifespan management."""
    _validate_orchestrator_on_startup()
    _validate_collection_model_on_startup()
    asyncio.create_task(_check_and_trigger_startup_sync())
    sync_task = asyncio.create_task(_periodic_mongo_sync())
    try:
        yield
    finally:
        sync_task.cancel()
        try:
            await sync_task
        except asyncio.CancelledError:
            pass


def create_app() -> FastAPI:
    """Instantiates and configures the FastAPI application."""
    application = FastAPI(
        title="Fakhri IT Services Enterprise ERP Chatbot",
        description="Autonomous Enterprise ERP Copilot grounded in live MongoDB Atlas data with RBAC security",
        version="2.0.0",
        lifespan=lifespan,
    )

    # State and handlers for rate limiting and exceptions
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _handle_rate_limit)
    application.add_exception_handler(HTTPException, _handle_http_exception)
    application.add_exception_handler(Exception, _handle_unhandled_exception)
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
    application.include_router(entities_router)
    application.include_router(sync_router)

    return application


app = create_app()
