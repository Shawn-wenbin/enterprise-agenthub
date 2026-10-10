"""真实 PostgreSQL 与本地字节存储，以及限定作用范围的受控故障注入。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from test_local_store import key

from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.models import Artifact
from agenthub.artifacts.service import ArtifactService, InternalArtifactScope
from agenthub.artifacts.store import StorageError
from agenthub.core.errors import AppError
from agenthub.db.database import Database

pytestmark = pytest.mark.integration


@pytest.fixture
def service(database: Database, store: LocalFileArtifactStore) -> ArtifactService:
    return ArtifactService(database, store, 1024, pending_seconds=1)


async def own_record(database: Database, scope: InternalArtifactScope) -> Artifact:
    async with database.session() as session:
        artifact = await session.scalar(
            select(Artifact).where(
                Artifact.tenant_id == scope.tenant_id,
                Artifact.project_id == scope.project_id,
            )
        )
        assert artifact is not None
        return artifact


async def test_save_read_and_scope_enforcement(
    service: ArtifactService,
    scope: InternalArtifactScope,
) -> None:
    metadata = await service.save(scope, b"test artifact", "text/plain")
    assert metadata.status == "ready" and metadata.size_bytes == 13
    assert metadata.created_at.utcoffset() == timedelta(0)
    assert await service.read(scope, metadata.artifact_id) == b"test artifact"
    for unauthorized in (
        InternalArtifactScope(tenant_id="other", project_id=scope.project_id),
        InternalArtifactScope(tenant_id=scope.tenant_id, project_id="other"),
    ):
        with pytest.raises(AppError) as caught:
            await service.read(unauthorized, metadata.artifact_id)
        assert caught.value.code == "artifact_not_found"


async def test_service_key_conflict_preserves_existing_object(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agenthub.artifacts import service as service_module

    occupied = key()
    await store.save(occupied, b"preserved orphan")
    monkeypatch.setattr(service_module.secrets, "token_hex", lambda _: occupied[4:])
    with pytest.raises(AppError) as caught:
        await service.save(scope, b"must not overwrite")
    assert caught.value.code == "artifact_key_conflict"
    artifact = await own_record(database, scope)
    assert artifact.status == "failed"
    assert await store.read(occupied) == b"preserved orphan"


async def test_service_post_publication_failure_stays_pending(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "_cleanup_owned", lambda *args: False)
    with pytest.raises(AppError) as caught:
        await service.save(scope, b"preserve")
    assert caught.value.details["published"] is True
    artifact = await own_record(database, scope)
    assert artifact.status == "pending"
    assert await store.read(artifact.storage_key) == b"preserve"
    issues = await service.diagnose()
    assert {"temporary", "unconfirmed_file"} <= {issue.code for issue in issues}


@pytest.mark.parametrize("stage", ["_write", "_publish"])
async def test_file_failure_marks_failed_and_blocks_read(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    def fail(*args: object) -> None:
        assert database.engine.pool.checkedout() == 0  # 文件读写期间不持有事务。
        raise OSError("injected")

    monkeypatch.setattr(store, stage, fail)
    with pytest.raises(AppError):
        await service.save(scope, b"x")
    artifact = await own_record(database, scope)
    assert artifact.status == "failed" and artifact.failure_code is not None
    assert not await store.inventory()
    with pytest.raises(AppError) as caught:
        await service.read(scope, artifact.artifact_id)
    assert caught.value.code == "artifact_not_ready"


async def test_pending_commit_failure_never_writes_file(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 在提交边界本身注入失败，而非在文件或 INSERT 操作中注入失败。
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def fail_commit(*args: object, **kwargs: object) -> None:
        raise OperationalError("injected", {}, Exception("unavailable"))

    event.listen(Session, "before_commit", fail_commit)
    spy = AsyncMock(wraps=store.save)
    monkeypatch.setattr(store, "save", spy)
    try:
        with pytest.raises(AppError) as caught:
            await service.save(scope, b"x")
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert caught.value.code == "dependency_unavailable"
    spy.assert_not_called()
    assert not await store.inventory()
    async with database.session() as session:
        assert (
            await session.scalar(
                select(Artifact).where(
                    Artifact.tenant_id == scope.tenant_id,
                )
            )
            is None
        )


async def test_ready_commit_failure_retains_pending_file_and_blocks_read(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import event

    def fail_ready_commit(session) -> None:
        if any(
            isinstance(obj, Artifact) and obj.status == "ready"
            for obj in session.identity_map.values()
        ):
            raise OperationalError("injected", {}, Exception("unavailable"))

    from sqlalchemy.orm import Session

    event.listen(Session, "before_commit", fail_ready_commit)
    try:
        with pytest.raises(AppError) as caught:
            await service.save(scope, b"preserve for diagnosis")
    finally:
        event.remove(Session, "before_commit", fail_ready_commit)
    assert caught.value.code == "artifact_metadata_unconfirmed"
    artifact = await own_record(database, scope)
    assert artifact.status == "pending"
    assert await store.read(artifact.storage_key) == b"preserve for diagnosis"
    with pytest.raises(AppError) as blocked:
        await service.read(scope, artifact.artifact_id)
    assert blocked.value.code == "artifact_not_ready"
    assert "unconfirmed_file" in {issue.code for issue in await service.diagnose()}


async def test_failed_update_failure_keeps_inspectable_pending(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def fail_commit(session) -> None:
        if any(
            isinstance(obj, Artifact) and obj.status == "failed"
            for obj in session.identity_map.values()
        ):
            raise OperationalError("injected", {}, Exception("unavailable"))

    async def fail_save(*args: object) -> None:
        raise StorageError("artifact_write_failed")

    event.listen(Session, "before_commit", fail_commit)
    monkeypatch.setattr(store, "save", fail_save)
    try:
        with pytest.raises(AppError):
            await service.save(scope, b"x")
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert (await own_record(database, scope)).status == "pending"


@pytest.mark.parametrize("damage", ["missing", "hash", "size"])
async def test_ready_missing_or_corrupt_file_is_explicit(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
    damage: str,
) -> None:
    metadata = await service.save(scope, b"good")
    artifact = await own_record(database, scope)
    path = store.root / artifact.storage_key
    if damage == "missing":
        path.unlink()  # 仅删除本测试自己的临时文件，不操作共享卷。
    else:
        path.write_bytes(b"evil" if damage == "hash" else b"different length")
    with pytest.raises(AppError) as caught:
        await service.read(scope, metadata.artifact_id)
    assert caught.value.code == (
        "artifact_missing" if damage == "missing" else "artifact_integrity_error"
    )
    assert caught.value.code in {issue.code for issue in await service.diagnose()}


async def test_dry_run_finds_stale_pending_temp_orphan_and_preserves_everything(
    service: ArtifactService,
    database: Database,
    store: LocalFileArtifactStore,
    scope: InternalArtifactScope,
) -> None:
    metadata = await service.save(scope, b"normal")
    artifact = await own_record(database, scope)
    async with database.session() as session, session.begin():
        stale = await session.get(Artifact, artifact.artifact_id)
        assert stale is not None
        stale.status = "pending"
        stale.created_at = datetime.now(UTC) - timedelta(hours=1)
    orphan = key()
    await store.save(orphan, b"orphan")
    temporary = ".tmp_" + "a" * 64
    (store.root / temporary).write_bytes(b"residue")
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    issues = await service.diagnose()
    assert {"stale_pending", "unconfirmed_file", "temporary", "orphan_object"} <= {
        issue.code for issue in issues
    }
    assert any(
        issue.storage_key == orphan and issue.code == "orphan_object"
        for issue in issues
    )
    assert before == {p.name: p.read_bytes() for p in store.root.iterdir()}
    assert (await own_record(database, scope)).status == "pending"
    with pytest.raises(AppError, match="not ready"):
        await service.read(scope, metadata.artifact_id)
