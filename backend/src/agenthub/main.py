"""Assemble the HTTP application without opening external connections."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agenthub.api.health import router as health_router
from agenthub.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.settings = (
            settings if settings is not None else get_settings()
        )
        yield

    application = FastAPI(title="Enterprise AgentHub", lifespan=lifespan)
    application.include_router(health_router)
    return application


app = create_app()
