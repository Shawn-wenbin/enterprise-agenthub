"""Assemble the HTTP application without opening external connections."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from agenthub.api.health import router as health_router
from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.service import ArtifactService
from agenthub.core.config import Settings, get_settings
from agenthub.core.http_errors import install_error_handlers
from agenthub.core.request_context import RequestContextMiddleware, configure_logging
from agenthub.db.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        current = settings if settings is not None else get_settings()
        application.state.settings = current
        database = Database(current)
        redis = Redis.from_url(
            current.redis_url.get_secret_value(),
            socket_connect_timeout=current.dependency_timeout_seconds,
            socket_timeout=current.dependency_timeout_seconds,
            retry_on_timeout=False,
        )
        store = LocalFileArtifactStore(
            current.artifact_root, current.artifact_max_bytes
        )
        application.state.database = database
        application.state.redis = redis
        application.state.artifacts = ArtifactService(
            database,
            store,
            current.artifact_max_bytes,
            current.artifact_pending_seconds,
        )
        try:
            await store.open()
            yield
        finally:
            try:
                await store.close()
            finally:
                try:
                    await redis.aclose()
                finally:
                    await database.close()

    application = FastAPI(title="Enterprise AgentHub", lifespan=lifespan)
    application.add_middleware(RequestContextMiddleware)
    install_error_handlers(application)
    application.include_router(health_router)
    return application


app = create_app()
