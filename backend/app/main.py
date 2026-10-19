"""FastAPI application factory. `/health` is liveness only: it must not
touch Neo4j, or CI would depend on a paid cloud database being awake."""

from __future__ import annotations

from fastapi import FastAPI

from app.core.config import Settings

VERSION = "0.0.1"


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="why-code", version=VERSION)
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": "why-code", "status": "ok", "version": VERSION}

    return app
