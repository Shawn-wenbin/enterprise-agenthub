"""短事务认证服务；密码计算时不占数据库连接。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.auth.models import Session, User
from agenthub.auth.schemas import UserIdentity
from agenthub.auth.security import (
    PasswordSecurity,
    hash_session_token,
    new_session_token,
    unauthorized,
)
from agenthub.core.errors import AppError
from agenthub.db.database import Database


@dataclass(frozen=True)
class AuthenticatedSession:
    session_id: str
    user: UserIdentity


class AuthService:
    def __init__(
        self, database: Database, passwords: PasswordSecurity, ttl: int
    ) -> None:
        self.database = database
        self.passwords = passwords
        self.ttl = ttl

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        # 驱动建连接时的 OS/超时异常可能尚未被 SQLAlchemy 包装。
        try:
            async with self.database.session() as db:
                yield db
        except (OSError, TimeoutError):
            raise AppError(
                "dependency_unavailable", "Service dependency is unavailable", 503
            ) from None

    async def login(self, login_name: str, password: str) -> tuple[UserIdentity, str]:
        async with self._session() as db:
            user = await db.scalar(select(User).where(User.login_name == login_name))
        # 账号不存在、禁用、密码错误均执行一次相同参数的验证。
        valid = await self.passwords.verify(
            password, user.password_hash if user else None
        )
        if not valid or user is None or not user.is_active:
            raise unauthorized()
        token = new_session_token()
        now = datetime.now(UTC)
        async with self._session() as db, db.begin():
            # 密码计算期间允许撤权；行锁序列化建会话与账号禁用/密码变更。
            current = await db.scalar(
                select(User).where(User.id == user.id).with_for_update()
            )
            if (
                current is None
                or not current.is_active
                or current.password_hash != user.password_hash
            ):
                raise unauthorized()
            db.add(
                Session(
                    id=str(uuid4()),
                    user_id=current.id,
                    token_hash=hash_session_token(token),
                    created_at=now,
                    expires_at=now + timedelta(seconds=self.ttl),
                )
            )
            identity = UserIdentity(id=current.id, login_name=current.login_name)
        return identity, token

    async def authenticate(self, token: str) -> AuthenticatedSession:
        hashed = hash_session_token(token)
        async with self._session() as db:
            row = (
                await db.execute(
                    select(Session.id, User.id, User.login_name)
                    .join(User, User.id == Session.user_id)
                    .where(
                        Session.token_hash == hashed,
                        Session.revoked_at.is_(None),
                        Session.expires_at > datetime.now(UTC),
                        User.is_active.is_(True),
                    )
                )
            ).one_or_none()
        if row is None:
            raise unauthorized()
        return AuthenticatedSession(row[0], UserIdentity(id=row[1], login_name=row[2]))

    async def logout(self, current: AuthenticatedSession) -> None:
        now = datetime.now(UTC)
        async with self._session() as db, db.begin():
            # 执行前重查有效性，重复退出返回 401，只撤销这一条会话。
            result = await db.execute(
                update(Session)
                .where(
                    Session.id == current.session_id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > now,
                    Session.user_id.in_(
                        select(User.id).where(User.is_active.is_(True))
                    ),
                )
                .values(revoked_at=now)
                .returning(Session.id)
            )
            if result.scalar_one_or_none() is None:
                raise unauthorized()
