"""Real PostgreSQL transactions, migration replay and lifecycle ownership."""

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError

from agenthub.artifacts.models import Artifact
from agenthub.core.config import Settings
from agenthub.db.database import Database
from agenthub.main import create_app

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]


def record() -> Artifact:
    return Artifact(
        artifact_id=str(uuid4()),
        storage_key=f"obj_{uuid4().hex}{uuid4().hex}",
        tenant_id="transaction-test",
        project_id="transaction-test",
        status="pending",
        media_type="text/plain",
    )


async def test_commit_rollback_independent_sessions_and_close(
    database: Database,
) -> None:
    committed, rolled_back = record(), record()
    async with database.session() as session:
        async with session.begin():
            session.add(committed)
        assert committed.status == "pending"  # expire_on_commit=False snapshot.
    with pytest.raises(InvalidRequestError):
        await session.execute(text("SELECT 1"))  # Permanently closed, not reusable.
    with pytest.raises(RuntimeError):
        async with database.session() as session, session.begin():
            session.add(rolled_back)
            await session.flush()
            raise RuntimeError("rollback")
    async with database.session() as first, database.session() as second:
        assert first is not second
        assert await second.get(Artifact, committed.artifact_id) is not None
        assert await second.get(Artifact, rolled_back.artifact_id) is None
    pool = database.engine.pool
    assert pool.checkedout() == 0


async def test_database_failure_rolls_back_without_poisoning_next_session(
    database: Database,
) -> None:
    original = record()
    async with database.session() as session, session.begin():
        session.add(original)
    duplicate = record()
    duplicate.storage_key = original.storage_key
    with pytest.raises(IntegrityError):
        async with database.session() as session, session.begin():
            session.add(duplicate)
    async with database.session() as session:
        assert await session.get(Artifact, duplicate.artifact_id) is None
        assert await session.get(Artifact, original.artifact_id) is not None


async def test_uncommitted_data_not_visible_between_sessions(
    database: Database,
) -> None:
    pending = record()
    async with database.session() as first, database.session() as second:
        async with first.begin():
            first.add(pending)
            await first.flush()
            assert await second.get(Artifact, pending.artifact_id) is None
        assert await second.get(Artifact, pending.artifact_id) is not None


async def test_upgrade_head_replay_preserves_rows(database: Database) -> None:
    preserved = record()
    async with database.session() as session, session.begin():
        session.add(preserved)
    environment = {
        **os.environ,
        "DATABASE_URL": os.environ["TEST_DATABASE_URL"],
        "REDIS_URL": os.environ["TEST_REDIS_URL"],
        "ENV": "test",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "backend/alembic.ini",
        "upgrade",
        "head",
        cwd=REPO,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    assert process.returncode == 0, (stdout + stderr).decode()
    async with database.session() as session:
        assert await session.get(Artifact, preserved.artifact_id) is not None
        assert (
            await session.scalar(text("SELECT version_num FROM alembic_version"))
            == "0002_constraint_names"
        )
        tables = (
            (
                await session.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
        assert set(tables) == {"artifacts", "alembic_version"}
        names = (
            (
                await session.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'artifacts'::regclass"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert set(names) == {
            constraint.name for constraint in Artifact.__table__.constraints
        }


def test_lifespan_disposes_engine_and_closes_redis(
    integration_settings: Settings,
) -> None:
    app = create_app(integration_settings)
    disposed: list[bool] = []
    with TestClient(app) as client:
        engine = app.state.database.engine
        event.listen(
            engine.sync_engine, "engine_disposed", lambda _: disposed.append(True)
        )
        assert client.get("/health/ready").status_code == 200
        assert engine.pool.size() > 0
        store = app.state.artifacts.store
    assert disposed == [True]
    assert engine.pool.checkedout() == 0
    assert store._directory_fd is None
    assert not app.state.redis.connection_pool._in_use_connections


@pytest.mark.parametrize("unavailable", ["postgresql", "redis"])
def test_real_unreachable_dependency_reports_503(
    integration_settings: Settings,
    unavailable: str,
) -> None:
    settings = integration_settings.model_copy(
        update={
            "dependency_timeout_seconds": 0.2,
        }
    )
    from pydantic import SecretStr

    if unavailable == "postgresql":
        settings.database_url = SecretStr(
            "postgresql+asyncpg://x:private@127.0.0.1:1/x"
        )
    else:
        settings.redis_url = SecretStr("redis://:private@127.0.0.1:1/0")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["details"]["dependencies"][unavailable] == "unavailable"
        assert client.get("/health").status_code == 200
        assert "private" not in response.text
