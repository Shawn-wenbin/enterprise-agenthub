"""使配置测试独立于开发者的本地环境。"""

import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url

from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.service import InternalArtifactScope
from agenthub.core.config import Settings, get_settings
from agenthub.db.database import Database


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--integration",
        action="store_true",
        help="Run real PostgreSQL/Redis tests on an isolated test database",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--integration"):
        url = os.environ.get("TEST_DATABASE_URL", "")
        try:
            name = make_url(url).database or ""
        except Exception:
            name = ""
        if not re.fullmatch(
            r"agenthub_unit1[23]_test_[0-9a-f]{32}", name
        ) or not os.environ.get("TEST_REDIS_URL"):
            raise pytest.UsageError(
                "--integration requires TEST_DATABASE_URL targeting an isolated "
                "agenthub_unit12_test_* or agenthub_unit13_test_* database "
                "and TEST_REDIS_URL"
            )
        return
    deselected = [item for item in items if item.get_closest_marker("integration")]
    items[:] = [item for item in items if item not in deselected]
    config.hook.pytest_deselected(items=deselected)


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("env", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/test"
    )
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    for key in (
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DEPENDENCY_TIMEOUT_SECONDS",
        "ARTIFACT_STORE",
        "ARTIFACT_MAX_BYTES",
        "ARTIFACT_PENDING_SECONDS",
        "SESSION_TTL_SECONDS",
        "SESSION_COOKIE_NAME",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_HTTPONLY",
        "SESSION_COOKIE_SAMESITE",
        "TRUSTED_ORIGINS",
        "CSRF_SECRET",
        "LOGIN_CSRF_TTL_SECONDS",
        "PASSWORD_CONCURRENCY",
        "LOGIN_RATE_WINDOW_SECONDS",
        "LOGIN_RATE_SOURCE_LIMIT",
        "LOGIN_RATE_ACCOUNT_LIMIT",
        "LOGIN_RATE_NAMESPACE",
    ):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def scope() -> InternalArtifactScope:
    return InternalArtifactScope(tenant_id=str(uuid4()), project_id=str(uuid4()))


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[LocalFileArtifactStore]:
    local = LocalFileArtifactStore(tmp_path / "volume", max_bytes=1024)
    await local.open()
    try:
        yield local
    finally:
        await local.close()


@pytest.fixture
def integration_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_url=os.environ["TEST_DATABASE_URL"],
        redis_url=os.environ["TEST_REDIS_URL"],
        artifact_root=tmp_path / "volume",
        _env_file=None,
    )


@pytest.fixture
async def database(integration_settings: Settings) -> AsyncIterator[Database]:
    db = Database(integration_settings)
    try:
        yield db
    finally:
        await db.close()
