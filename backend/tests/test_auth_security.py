"""密码、输入限制、签名凭证与生产启动边界。"""

import asyncio
import secrets
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agenthub.auth.schemas import LoginRequest, normalize_login_name
from agenthub.auth.security import (
    CsrfSecurity,
    PasswordSecurity,
    hash_session_token,
    new_session_token,
)
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.main import create_app


async def test_real_argon2_and_nonexistent_user_dummy() -> None:
    passwords = PasswordSecurity(2)
    await passwords.initialize()
    encoded = await passwords.hash("fixture-password")
    assert encoded.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert encoded != await passwords.hash("fixture-password")
    assert await passwords.verify("fixture-password", encoded)
    assert not await passwords.verify("wrong", encoded)
    assert not await passwords.verify("fixture-password", None)
    assert not await passwords.verify("fixture-password", "broken")


async def test_password_work_does_not_block_event_loop_and_is_bounded() -> None:
    passwords = PasswordSecurity(2)
    workers = 0
    peak = 0
    lock = threading.Lock()
    gate = threading.Event()

    def operation() -> bool:
        nonlocal workers, peak
        with lock:
            workers += 1
            peak = max(peak, workers)
        gate.wait(2)
        with lock:
            workers -= 1
        return True

    tasks = [asyncio.create_task(passwords.calculate(operation)) for _ in range(5)]
    try:
        for _ in range(100):
            if peak == 2:
                break
            await asyncio.sleep(0.01)
        assert peak == 2
        # 主事件循环可以运行到此，而后台两个计算尚未结束。
        assert not any(task.done() for task in tasks)
    finally:
        gate.set()
        assert all(await asyncio.gather(*tasks))
    assert peak == 2


def test_normalized_login_and_limits() -> None:
    assert normalize_login_name(" Alice.Example@TEST ") == "alice.example@test"
    for name in ("", "Ａlice", "alice/other", "a" * 129):
        with pytest.raises(ValueError):
            normalize_login_name(name)
    for size in (0, 257):
        with pytest.raises(ValidationError):
            LoginRequest(login_name="alice", password="x" * size)


def production_settings(**updates: object) -> Settings:
    values: dict[str, object] = dict(
        env="production",
        database_url="postgresql+asyncpg://u:isolated-test-secret-password@db/agenthub",
        redis_url="redis://:isolated-test-secret-password@redis/0",
        trusted_origins=["https://hub.example.test"],
        csrf_secret=secrets.token_hex(32),
        _env_file=None,
    )
    values.update(updates)
    return Settings(**values)


@pytest.mark.parametrize(
    "updates",
    [
        {"session_cookie_secure": False},
        {"session_cookie_httponly": False},
        {"session_cookie_name": "plain"},
        {"trusted_origins": ["http://hub.example.test"]},
        {"trusted_origins": ["https://localhost"]},
        {"trusted_origins": ["*"]},
        {"trusted_origins": ["https://hub.example.test/path"]},
        {"trusted_origins": []},
        {"trusted_origins": ["https://user@hub.example.test"]},
        {"csrf_secret": None},
        {"csrf_secret": "0" * 64},
        {"redis_url": "redis://redis/0"},
        {
            "database_url": "postgresql+asyncpg://u:replace_with_local_password@db/agenthub"
        },
    ],
)
def test_unsafe_production_config_rejected(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        production_settings(**updates)


def test_production_defaults_and_injected_invalid_settings_fail_startup() -> None:
    settings = production_settings()
    assert settings.session_cookie_name == "__Host-agenthub_session"
    assert settings.session_cookie_secure and settings.session_cookie_httponly
    invalid = settings.model_copy(update={"session_cookie_secure": False})
    with pytest.raises(ValidationError), TestClient(create_app(invalid)):
        pass
    with pytest.raises(ValidationError):
        Settings(session_cookie_samesite="none", session_cookie_secure=False)


def test_signed_credentials_expiry_rotation_binding_and_token_entropy() -> None:
    csrf = CsrfSecurity(secrets.token_hex(32), 28800, 600)
    prelogin = csrf.issue()
    csrf.verify(prelogin, cookie=prelogin)
    for cookie in (None, "arbitrary"):
        with pytest.raises(AppError):
            csrf.verify(prelogin, cookie=cookie)
    token = csrf.issue("session-one")
    csrf.verify(token, "session-one")
    with pytest.raises(AppError):
        csrf.verify(token, "session-two")
    with patch("itsdangerous.timed.time.time", return_value=1):
        expired = csrf.issue()
    with pytest.raises(AppError):
        csrf.verify(expired, cookie=expired)
    with pytest.raises(AppError):
        CsrfSecurity(secrets.token_hex(32), 28800, 600).verify(token, "session-one")
    generated = {new_session_token() for _ in range(100)}
    assert len(generated) == 100 and all(len(t) == 43 for t in generated)
    assert all(len(hash_session_token(t)) == 64 for t in generated)


def test_csrf_fetch_origin_and_body_limit_without_dependencies() -> None:
    with TestClient(create_app()) as client:
        for headers in (
            {},
            {"Origin": "https://evil.test"},
            {"Sec-Fetch-Site": "cross-site", "X-CSRF-Fetch": "1"},
        ):
            assert client.get("/api/v1/auth/csrf", headers=headers).status_code == 403
        response = client.get(
            "/api/v1/auth/csrf",
            headers={"Sec-Fetch-Site": "same-origin", "X-CSRF-Fetch": "1"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        for content in ("x" * 4097, (part for part in [b"x" * 3000, b"x" * 3000])):
            response = client.post("/api/v1/auth/login", content=content)
            assert response.status_code == 413
            assert response.json()["request_id"] == response.headers["x-request-id"]


def test_real_unreachable_database_authentication_is_503() -> None:
    with TestClient(create_app()) as client:
        client.cookies.set("agenthub_session", new_session_token())
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 503
        assert response.json()["code"] == "dependency_unavailable"


@pytest.mark.parametrize(
    "origin",
    ["https://bad host.test", "https://bad\\host.test", "http://public.example.test"],
)
def test_invalid_or_insecure_remote_development_origins_rejected(origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings(trusted_origins=[origin])


def test_httponly_environment_boolean_and_disabled_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SESSION_COOKIE_HTTPONLY", "true")
    assert Settings().session_cookie_httponly is True
    monkeypatch.setenv("SESSION_COOKIE_HTTPONLY", "false")
    with pytest.raises(ValidationError, match="HttpOnly"):
        Settings()
