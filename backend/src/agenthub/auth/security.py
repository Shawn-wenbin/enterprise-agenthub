"""成熟密码库与签名库，分别处理低熵密码和高熵凭证。"""

import hashlib
import re
import secrets
from collections.abc import Callable
from typing import TypeVar

import anyio
from itsdangerous import BadData, URLSafeTimedSerializer
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

from agenthub.core.errors import AppError

T = TypeVar("T")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    if not TOKEN_PATTERN.fullmatch(token):
        raise unauthorized()
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def unauthorized() -> AppError:
    return AppError("authentication_failed", "Authentication failed", 401)


def csrf_error() -> AppError:
    return AppError("csrf_rejected", "Request protection failed", 403)


class PasswordSecurity:
    def __init__(self, concurrency: int) -> None:
        # 64 MiB、3 次迭代、4 路并行；每个 API 进程最多同时执行配置数量的计算。
        self.password_hash = PasswordHash(
            (Argon2Hasher(memory_cost=65536, time_cost=3, parallelism=4),)
        )
        self.limiter = anyio.CapacityLimiter(concurrency)
        self.dummy_hash = ""

    async def calculate(self, operation: Callable[..., T], *args: object) -> T:
        return await anyio.to_thread.run_sync(operation, *args, limiter=self.limiter)

    async def initialize(self) -> None:
        self.dummy_hash = await self.hash(secrets.token_urlsafe(32))

    async def hash(self, password: str) -> str:
        return await self.calculate(self.password_hash.hash, password)

    async def verify(self, password: str, encoded: str | None) -> bool:
        try:
            return await self.calculate(
                self.password_hash.verify, password, encoded or self.dummy_hash
            )
        except (ValueError, TypeError, UnknownHashError):
            # 损坏的存储值不泄露异常，也不绕过认证。
            return False


class CsrfSecurity:
    def __init__(self, secret: str, session_ttl: int, login_ttl: int) -> None:
        self.signer = URLSafeTimedSerializer(
            secret,
            salt="agenthub-csrf-v1",
            signer_kwargs={"digest_method": hashlib.sha256},
        )
        self.session_ttl = session_ttl
        self.login_ttl = login_ttl

    def issue(self, session_id: str | None = None) -> str:
        if session_id is None:
            return self.signer.dumps(
                {"purpose": "login", "nonce": secrets.token_urlsafe(32)}
            )
        return self.signer.dumps({"purpose": "session", "session_id": session_id})

    def verify(
        self,
        token: str | None,
        session_id: str | None = None,
        cookie: str | None = None,
    ) -> None:
        if not token or len(token) > 512 or not token.isascii():
            raise csrf_error()
        try:
            data = self.signer.loads(
                token, max_age=self.session_ttl if session_id else self.login_ttl
            )
        except BadData:
            raise csrf_error() from None
        if not isinstance(data, dict):
            raise csrf_error()
        if session_id is not None:
            if data.get("purpose") != "session" or data.get("session_id") != session_id:
                raise csrf_error()
        elif (
            data.get("purpose") != "login"
            or not isinstance(data.get("nonce"), str)
            or cookie is None
            or len(cookie) > 512
            or not cookie.isascii()
            or not secrets.compare_digest(token, cookie)
        ):
            raise csrf_error()
