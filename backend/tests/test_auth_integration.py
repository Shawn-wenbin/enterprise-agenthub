"""真实 PostgreSQL/Redis 与 HTTP Cookie 的认证安全闭环。"""

import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from agenthub.auth.models import Session, User
from agenthub.auth.rate_limit import LoginRateLimiter
from agenthub.auth.security import hash_session_token
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.db.database import Database
from agenthub.main import create_app

pytestmark = pytest.mark.integration
ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture
async def app(integration_settings: Settings) -> AsyncIterator[FastAPI]:
    settings = integration_settings.model_copy(
        update={
            "login_rate_namespace": f"test:unit13:{uuid4().hex}",
            "csrf_secret": None,
        }
    )
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 12345)),
        base_url=ORIGIN,
    ) as session:
        yield session


@pytest.fixture
async def user(app: FastAPI) -> tuple[User, str]:
    password = secrets.token_urlsafe(20)
    encoded = await app.state.auth.passwords.hash(password)
    record = User(login_name=f"alice-{uuid4().hex}", password_hash=encoded)
    async with app.state.database.session() as db, db.begin():
        db.add(record)
    return record, password


async def credential(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/v1/auth/csrf", headers={"Origin": ORIGIN})
    assert response.status_code == 200
    return response.json()["csrf_token"]


async def login(client: httpx.AsyncClient, user: tuple[User, str]) -> httpx.Response:
    csrf = await credential(client)
    return await client.post(
        "/api/v1/auth/login",
        json={"login_name": user[0].login_name, "password": user[1]},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )


async def test_login_me_cookie_database_logout_and_no_secrets(
    client: httpx.AsyncClient,
    app: FastAPI,
    user: tuple[User, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    response = await login(client, user)
    assert response.status_code == 200 and response.json() == {
        "id": user[0].id,
        "login_name": user[0].login_name,
    }
    settings = app.state.settings
    token = client.cookies[settings.session_cookie_name]
    header = next(
        h
        for h in response.headers.get_list("set-cookie")
        if h.startswith(settings.session_cookie_name + "=")
    )
    assert "HttpOnly" in header and "SameSite=lax" in header and "Path=/" in header
    assert (
        "Secure" not in header and "Domain=" not in header and "Max-Age=28800" in header
    )
    async with app.state.database.session() as db:
        record = await db.scalar(
            select(Session).where(Session.token_hash == hash_session_token(token))
        )
        assert record is not None and record.user_id == user[0].id
        assert record.token_hash != token and record.revoked_at is None
        assert record.expires_at - record.created_at == timedelta(hours=8)
        assert record.created_at.utcoffset() == timedelta(0)
        stored_user = await db.get(User, user[0].id)
        assert (
            stored_user.password_hash.startswith("$argon2id$")
            and user[1] not in stored_user.password_hash
        )
    assert (await client.get("/api/v1/auth/me")).json() == {
        **response.json(),
        "memberships": [],
    }
    csrf = await credential(client)
    response = await client.post(
        "/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 204 and not response.content
    assert "Max-Age=0" in response.headers["set-cookie"]
    client.cookies.set(settings.session_cookie_name, token)
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
    ).status_code == 401
    async with app.state.database.session() as db:
        assert (await db.get(Session, record.id)).revoked_at is not None
    for secret in (token, csrf, user[1], user[0].password_hash):
        assert secret not in response.text and secret not in caplog.text


async def test_wrong_missing_disabled_generic_error(
    client: httpx.AsyncClient, app: FastAPI, user: tuple[User, str]
) -> None:
    errors = []
    for name, password in (
        (user[0].login_name, "wrong"),
        ("missing-" + uuid4().hex, user[1]),
    ):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login_name": name, "password": password},
            headers={"Origin": ORIGIN, "X-CSRF-Token": await credential(client)},
        )
        assert (
            response.status_code == 401
            and app.state.settings.session_cookie_name not in response.cookies
        )
        errors.append((response.json()["code"], response.json()["message"]))
    assert errors[0] == errors[1]
    async with app.state.database.session() as db, db.begin():
        await db.execute(
            update(User).where(User.id == user[0].id).values(is_active=False)
        )
    assert (await login(client, user)).status_code == 401


@pytest.mark.parametrize(
    "change", ["expired", "revoked", "disabled", "forged", "malformed"]
)
async def test_session_invalidated_and_csrf_cannot_resurrect(
    client: httpx.AsyncClient, app: FastAPI, user: tuple[User, str], change: str
) -> None:
    assert (await login(client, user)).status_code == 200
    csrf = await credential(client)
    token = client.cookies[app.state.settings.session_cookie_name]
    async with app.state.database.session() as db, db.begin():
        if change == "expired":
            now = datetime.now(UTC)
            await db.execute(
                update(Session)
                .where(Session.token_hash == hash_session_token(token))
                .values(
                    created_at=now - timedelta(hours=9),
                    expires_at=now - timedelta(hours=1),
                )
            )
        elif change == "revoked":
            await db.execute(
                update(Session)
                .where(Session.token_hash == hash_session_token(token))
                .values(revoked_at=datetime.now(UTC))
            )
        elif change == "disabled":
            await db.execute(
                update(User).where(User.id == user[0].id).values(is_active=False)
            )
    if change in {"forged", "malformed"}:
        client.cookies.clear()
        client.cookies.set(
            app.state.settings.session_cookie_name,
            secrets.token_urlsafe(32) if change == "forged" else "123",
        )
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
    ).status_code == 401
    # 失效 Cookie 不阻止重新获取预登录凭证，旧会话凭证仍不能复用。
    assert (await client.get("/api/v1/auth/csrf", headers={"Origin": ORIGIN})).json()[
        "purpose"
    ] == "login"


async def test_csrf_origin_matrix_and_login_protection(
    client: httpx.AsyncClient, app: FastAPI, user: tuple[User, str]
) -> None:
    prelogin = await credential(client)
    payload = {"login_name": user[0].login_name, "password": user[1]}
    for origin, csrf in (
        (ORIGIN, None),
        (ORIGIN, "wrong"),
        (None, prelogin),
        ("http://127.0.0.1:5174", prelogin),
        ("http://127.0.0.1:5173.evil.test", prelogin),
        ("null", prelogin),
    ):
        headers = {}
        if origin:
            headers["Origin"] = origin
        if csrf:
            headers["X-CSRF-Token"] = csrf
        response = await client.post(
            "/api/v1/auth/login", json=payload, headers=headers
        )
        assert response.status_code == 403
    response = await client.post(
        "/api/v1/auth/login",
        data=payload,
        headers={"Origin": ORIGIN, "X-CSRF-Token": prelogin},
    )
    assert response.status_code == 403
    assert (await login(client, user)).status_code == 200
    bound = await credential(client)
    for headers in (
        {"Origin": ORIGIN},
        {"Origin": ORIGIN, "X-CSRF-Token": "wrong"},
        {"X-CSRF-Token": bound},
        {"Origin": "https://evil.test", "X-CSRF-Token": bound},
        {"Origin": ORIGIN, "X-CSRF-Token": prelogin},
    ):
        assert (
            await client.post("/api/v1/auth/logout", headers=headers)
        ).status_code == 403
    assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_independent_sessions_restart_and_concurrent_logins(
    client: httpx.AsyncClient, app: FastAPI, user: tuple[User, str]
) -> None:
    assert (await login(client, user)).status_code == 200
    token = client.cookies[app.state.settings.session_cookie_name]
    first_csrf = await credential(client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=ORIGIN
    ) as second:
        assert (await login(second, user)).status_code == 200
        assert second.cookies[app.state.settings.session_cookie_name] != token
        assert (
            await second.post(
                "/api/v1/auth/logout",
                headers={"Origin": ORIGIN, "X-CSRF-Token": first_csrf},
            )
        ).status_code == 403
        second_csrf = await credential(second)
        assert (
            await second.post(
                "/api/v1/auth/logout",
                headers={"Origin": ORIGIN, "X-CSRF-Token": second_csrf},
            )
        ).status_code == 204
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    restarted = create_app(app.state.settings)
    async with (
        restarted.router.lifespan_context(restarted),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=restarted),
            base_url=ORIGIN,
            cookies={app.state.settings.session_cookie_name: token},
        ) as third,
    ):
        assert (await third.get("/api/v1/auth/me")).status_code == 200
        assert (await third.get("/health/ready")).status_code == 200
    identities = await asyncio.gather(
        *(app.state.auth.login(user[0].login_name, user[1]) for _ in range(3))
    )
    assert len({t for _, t in identities}) == 3


async def test_transaction_and_dependency_failure_never_success(
    client: httpx.AsyncClient,
    app: FastAPI,
    user: tuple[User, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": await credential(client)}
    failure = OperationalError("safe", {}, Exception("secret must not leak"))
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", AsyncMock(side_effect=failure))
        # begin() 使用底层事务 commit，故注入 sync_session 的提交事件。
        from sqlalchemy import event

        def fail_commit(session: object) -> None:
            raise failure

        event.listen(AsyncSession.sync_session_class, "before_commit", fail_commit)
        try:
            response = await client.post(
                "/api/v1/auth/login",
                json={"login_name": user[0].login_name, "password": user[1]},
                headers=headers,
            )
        finally:
            event.remove(AsyncSession.sync_session_class, "before_commit", fail_commit)
    assert response.status_code == 503 and "secret" not in response.text
    assert app.state.settings.session_cookie_name not in response.cookies
    async with app.state.database.session() as db:
        assert (
            await db.scalar(select(Session.id).where(Session.user_id == user[0].id))
            is None
        )
    assert (await login(client, user)).status_code == 200
    csrf = await credential(client)
    from sqlalchemy import event

    event.listen(AsyncSession.sync_session_class, "before_commit", fail_commit)
    try:
        response = await client.post(
            "/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
        )
    finally:
        event.remove(AsyncSession.sync_session_class, "before_commit", fail_commit)
    assert response.status_code == 503 and "set-cookie" not in response.headers
    assert (await client.get("/api/v1/auth/me")).status_code == 200
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "execute", AsyncMock(side_effect=failure))
        assert (await client.get("/api/v1/auth/me")).status_code == 503
    # 登录限流使用真实的不可达 Redis，不采用 Mock 成功结果。
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.1)
    try:
        app.state.login_limiter.redis = broken
        response = await login(client, user)
        assert response.status_code == 503
    finally:
        await broken.aclose()


async def test_rate_limits_normalization_forged_source_and_expiry(
    client: httpx.AsyncClient, app: FastAPI, user: tuple[User, str]
) -> None:
    app.state.login_limiter.account_limit = 2
    app.state.login_limiter.source_limit = 20
    app.state.login_limiter.window = 1
    for name in (
        user[0].login_name.upper(),
        " " + user[0].login_name + " ",
        user[0].login_name,
    ):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login_name": name, "password": "wrong"},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": await credential(client),
                "X-Forwarded-For": str(uuid4()),
            },
        )
        assert response.status_code == (429 if name == user[0].login_name else 401)
    await asyncio.sleep(1.1)
    assert (await login(client, user)).status_code == 200
    app.state.login_limiter.source_limit = 1
    app.state.login_limiter.namespace += ":source"
    for index in range(2):
        response = await client.post(
            "/api/v1/auth/login",
            json={"login_name": "missing-" + uuid4().hex, "password": "wrong"},
            headers={
                "Origin": ORIGIN,
                "X-CSRF-Token": await credential(client),
                "X-Forwarded-For": str(uuid4()),
            },
        )
        assert response.status_code == (401 if index == 0 else 429)


async def test_redis_atomic_parallel_independent_clients(app: FastAPI) -> None:
    namespace = "test:unit13:parallel:" + uuid4().hex
    other = Redis.from_url(app.state.settings.redis_url.get_secret_value())
    limiters = [
        LoginRateLimiter(r, 1, 5, 5, namespace) for r in (app.state.redis, other)
    ]

    async def attempt(index: int) -> bool:
        try:
            await limiters[index % 2].check("same-ip", "same-account")
            return True
        except AppError as exc:
            assert exc.status_code == 429
            return False

    try:
        assert sum(await asyncio.gather(*(attempt(i) for i in range(30)))) == 5
        await asyncio.sleep(1.1)
        await limiters[1].check("same-ip", "same-account")
    finally:
        await other.aclose()


async def test_unique_users_hashes_fk_and_exact_table_set(
    app: FastAPI, user: tuple[User, str]
) -> None:
    database: Database = app.state.database
    with pytest.raises(IntegrityError):
        async with database.session() as db, db.begin():
            db.add(
                User(login_name=user[0].login_name, password_hash=user[0].password_hash)
            )
    with pytest.raises(IntegrityError):
        async with database.session() as db, db.begin():
            db.add(
                User(
                    login_name=user[0].login_name.upper(),
                    password_hash=user[0].password_hash,
                )
            )
    assert (await app.state.auth.login(user[0].login_name, user[1]))[0].id == user[0].id
    async with database.session() as db:
        tables = (
            (
                await db.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
    assert set(tables) == {
        "artifacts",
        "users",
        "sessions",
        "alembic_version",
        "tenants",
        "memberships",
        "membership_roles",
        "projects",
        "resource_acl",
    }


async def test_secure_host_cookie_over_https(
    app: FastAPI, user: tuple[User, str]
) -> None:
    settings = app.state.settings.model_copy(
        update={
            "session_cookie_secure": True,
            "session_cookie_name": "__Host-agenthub_session",
            "trusted_origins": ["https://hub.example.test"],
        }
    )
    secure = create_app(settings)
    origin = "https://hub.example.test"
    async with (
        secure.router.lifespan_context(secure),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=secure), base_url=origin
        ) as client,
    ):
        csrf = (
            await client.get("/api/v1/auth/csrf", headers={"Origin": origin})
        ).json()["csrf_token"]
        response = await client.post(
            "/api/v1/auth/login",
            json={"login_name": user[0].login_name, "password": user[1]},
            headers={"Origin": origin, "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        header = next(
            h
            for h in response.headers.get_list("set-cookie")
            if h.startswith("__Host-agenthub_session=")
        )
        assert all(
            attribute in header
            for attribute in ("Secure", "HttpOnly", "Path=/", "SameSite=lax")
        )
        assert "Domain=" not in header
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        csrf = (
            await client.get("/api/v1/auth/csrf", headers={"Origin": origin})
        ).json()["csrf_token"]
        response = await client.post(
            "/api/v1/auth/logout", headers={"Origin": origin, "X-CSRF-Token": csrf}
        )
        assert response.status_code == 204
        header = next(
            h
            for h in response.headers.get_list("set-cookie")
            if h.startswith("__Host-agenthub_session=")
        )
        assert "Secure" in header and "Max-Age=0" in header
        assert "__Host-agenthub_session" not in client.cookies


async def test_disable_during_password_work_rechecked_before_session(
    app: FastAPI,
    user: tuple[User, str],
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = app.state.auth.passwords.verify

    async def disable_after_verification(password: str, encoded: str | None) -> bool:
        valid = await original(password, encoded)
        async with app.state.database.session() as db, db.begin():
            await db.execute(
                update(User).where(User.id == user[0].id).values(is_active=False)
            )
        return valid

    monkeypatch.setattr(app.state.auth.passwords, "verify", disable_after_verification)
    assert (await login(client, user)).status_code == 401
    async with app.state.database.session() as db:
        assert (
            await db.scalar(select(Session.id).where(Session.user_id == user[0].id))
            is None
        )


async def test_shared_api_guard_on_future_mutations(
    app: FastAPI, client: httpx.AsyncClient, user: tuple[User, str]
) -> None:
    from fastapi import APIRouter, Depends

    from agenthub.api.router import router as protected_router
    from agenthub.auth.dependencies import get_current_user
    from agenthub.auth.schemas import UserIdentity

    probe = APIRouter(prefix="/api/v1", dependencies=protected_router.dependencies)

    @probe.post("/test-mutation")
    async def mutation(
        identity: Annotated[UserIdentity, Depends(get_current_user)],
    ) -> dict[str, str]:
        return {"id": identity.id}

    app.include_router(probe)
    assert (await login(client, user)).status_code == 200
    csrf = await credential(client)
    assert (
        await client.post("/api/v1/test-mutation", headers={"Origin": ORIGIN})
    ).status_code == 403
    response = await client.post(
        "/api/v1/test-mutation",
        json={"user_id": "forged-client-id"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200 and response.json() == {"id": user[0].id}


async def test_session_unique_hash_and_user_foreign_key(
    app: FastAPI, user: tuple[User, str]
) -> None:
    _, token = await app.state.auth.login(user[0].login_name, user[1])
    now = datetime.now(UTC)
    for user_id, token_hash in (
        (user[0].id, hash_session_token(token)),
        (str(uuid4()), hash_session_token(secrets.token_urlsafe(32))),
    ):
        with pytest.raises(IntegrityError):
            async with app.state.database.session() as db, db.begin():
                db.add(
                    Session(
                        user_id=user_id,
                        token_hash=token_hash,
                        created_at=now,
                        expires_at=now + timedelta(hours=8),
                    )
                )
