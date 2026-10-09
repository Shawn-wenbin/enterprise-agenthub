"""One managed engine per process, independent sessions and explicit transactions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agenthub.core.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            pool_timeout=settings.dependency_timeout_seconds,
            connect_args={
                "timeout": settings.dependency_timeout_seconds,
                "command_timeout": settings.dependency_timeout_seconds,
                "server_settings": {"timezone": "UTC"},
            },
            hide_parameters=True,
        )
        # Loaded values stay available after commit without implicit async IO.
        # They are snapshots: refresh/requery to observe changes by other sessions.
        self.sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, close_resets_only=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        # Closing rolls back unfinished work. Services own session.begin()/commit.
        async with self.sessions() as session:
            try:
                yield session
            except BaseException:
                await session.rollback()
                raise

    async def close(self) -> None:
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database: Database = request.app.state.database
    async with database.session() as session:
        yield session
