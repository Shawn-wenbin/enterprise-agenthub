"""每个进程管理一个引擎，使用独立会话与显式事务。"""

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
        # 提交后仍可访问已加载的值，不触发隐式异步读写。
        # 这些值只是快照；需刷新或重新查询才能观察其他会话的变更。
        self.sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, close_resets_only=False
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        # 关闭会话会回滚未完成的操作。服务层负责 session.begin() 和提交。
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
