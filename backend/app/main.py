"""FastAPI application factory. `/health` is liveness only: it must not
touch Neo4j, or CI would depend on a paid cloud database being awake."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import Settings
from app.retrieval.api.demo_routes import build_demo_router
from app.retrieval.api.routes import build_router
from app.retrieval.service import RetrievalService

VERSION = "0.0.1"

_DEMO_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def create_app(
    settings: Settings | None = None,
    retrieval_service: RetrievalService | None = None,
    demo_service: RetrievalService | None = None,
) -> FastAPI:
    app = FastAPI(title="why-code", version=VERSION)
    app.state.settings = settings
    app.state.retrieval_service = retrieval_service
    app.state.demo_service = demo_service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(_DEMO_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": "why-code", "status": "ok", "version": VERSION}

    app.include_router(build_router(retrieval_service))
    if demo_service is not None:
        app.include_router(build_demo_router(demo_service))
    return app


def create_demo_app() -> FastAPI:
    """The prepared, offline judge demo.

    The same service serves ``/ask`` and ``/demo/*``. The demo route only
    accepts the script; ``/ask`` remains available for direct inspection.
    """

    from app.retrieval.demo import create_demo_service

    service = create_demo_service()
    return create_app(retrieval_service=service, demo_service=service)


__all__ = ["VERSION", "create_app", "create_demo_app"]
