from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api import contracts, health
from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.services.recovery import run_startup_recovery


def create_app(
    *,
    session_factory: Callable[[], Session] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the API. Tests pass their own session factory and settings for startup work."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved = settings or get_settings()
        run_startup_recovery(
            session_factory or get_sessionmaker(),
            idle_timeout=timedelta(minutes=resolved.agent_stuck_timeout_minutes),
        )
        yield

    app = FastAPI(title="Contract Review Agent API", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(contracts.router)
    return app


app = create_app()
