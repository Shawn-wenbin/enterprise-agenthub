"""HTTP error sanitization, correlation and dependency fault contracts."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from agenthub.core.errors import AppError
from agenthub.main import create_app


def test_correlation_generated_per_request(caplog: pytest.LogCaptureFixture) -> None:
    with (
        caplog.at_level(logging.INFO, logger="agenthub"),
        TestClient(create_app()) as c,
    ):
        first = c.get("/health", headers={"X-Request-ID": "untrusted"})
        second = c.get("/health")
    first_id = first.headers["X-Request-ID"]
    assert first_id.startswith("req_") and first_id != "untrusted"
    assert first_id != second.headers["X-Request-ID"]
    records = [json.loads(r.message) for r in caplog.records if r.name == "agenthub"]
    assert records[0]["request_id"] == first_id


@pytest.mark.parametrize("failure", ["business", "dependency", "unknown", "http"])
def test_errors_are_uniform_and_do_not_leak(
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = create_app()

    @app.get("/test-error")
    async def broken() -> None:
        if failure == "business":
            raise AppError("conflict", "State conflict", 409)
        if failure == "dependency":
            raise OperationalError("password=secret", {}, Exception("secret"))
        if failure == "http":
            raise HTTPException(403, detail="secret")
        raise RuntimeError("database://password=secret")

    with caplog.at_level(logging.INFO, logger="agenthub"), TestClient(app) as c:
        response = c.get("/test-error")
    assert (
        response.status_code
        == {"business": 409, "dependency": 503, "unknown": 500, "http": 403}[failure]
    )
    body = response.json()
    assert set(body) == {"code", "message", "request_id", "details"}
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert "secret" not in response.text
    assert "secret" not in caplog.text
    assert body["request_id"] in caplog.text


def test_validation_retains_field_location_without_echoing_input() -> None:
    app = create_app()

    class Payload(BaseModel):
        count: int

    @app.post("/test-validation")
    async def validate(payload: Payload) -> Payload:
        return payload

    with TestClient(app) as c:
        response = c.post("/test-validation", json={"count": "sensitive-secret"})
    assert response.status_code == 422
    assert response.json()["details"]["errors"][0]["loc"] == ["body", "count"]
    assert "sensitive-secret" not in response.text


@pytest.mark.parametrize("dependency", ["postgresql", "redis", "timeout", "none"])
def test_readiness_faults_are_bounded_and_liveness_survives(
    dependency: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app()

    @asynccontextmanager
    async def connect():
        if dependency == "postgresql":
            raise OSError("secret URL")
        connection = AsyncMock()
        yield connection

    async def ping() -> bool:
        if dependency == "redis":
            raise OSError("secret URL")
        if dependency == "timeout":
            await asyncio.sleep(10)
        return True

    with TestClient(app) as c:
        app.state.settings.dependency_timeout_seconds = 0.05
        monkeypatch.setattr(AsyncEngine, "connect", lambda self: connect())
        monkeypatch.setattr(app.state.redis, "ping", ping)
        response = c.get("/health/ready")
        assert c.get("/health").json() == {"status": "ok"}
    if dependency == "none":
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    else:
        assert response.status_code == 503
        assert response.json()["code"] == "dependency_unavailable"
        failed = "redis" if dependency == "timeout" else dependency
        assert response.json()["details"]["dependencies"][failed] == "unavailable"
    assert "secret" not in response.text
