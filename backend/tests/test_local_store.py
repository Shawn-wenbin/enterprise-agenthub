"""真实本地文件系统行为与受控故障注入。"""

import asyncio
import hashlib
import os
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest

from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.store import StorageError


def key() -> str:
    return f"obj_{uuid4().hex}{uuid4().hex}"


async def test_save_verifies_actual_hash_size(store: LocalFileArtifactStore) -> None:
    name = key()
    result = await store.save(name, b"hello")
    assert result.size_bytes == 5
    assert result.content_hash == hashlib.sha256(b"hello").hexdigest()
    assert await store.read(name) == b"hello"
    assert [e.kind for e in await store.inventory()] == ["object"]


@pytest.mark.parametrize(
    "invalid", ["../outside", "/etc/passwd", "obj_../x", "", "x/y"]
)
async def test_paths_are_rejected(store: LocalFileArtifactStore, invalid: str) -> None:
    with pytest.raises(StorageError, match="artifact_invalid_key"):
        await store.save(invalid, b"x")
    with pytest.raises(StorageError, match="artifact_invalid_key"):
        await store.read(invalid)


async def test_root_symlinks_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    local = LocalFileArtifactStore(tmp_path / "link" / "child", 1024)
    with pytest.raises(OSError):
        await local.open()
    assert not (outside / "child").exists()


async def test_read_only_open_does_not_create_root(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    local = LocalFileArtifactStore(root, 1024)
    with pytest.raises(FileNotFoundError):
        await local.open(create_root=False)
    assert not root.exists()


async def test_writable_by_other_users_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "unsafe"
    root.mkdir()
    root.chmod(0o777)
    local = LocalFileArtifactStore(root, 1024)
    with pytest.raises(StorageError, match="artifact_unsafe_root_permissions"):
        await local.open()


async def test_final_symlink_never_read_or_overwritten(
    store: LocalFileArtifactStore,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    name = key()
    (store.root / name).symlink_to(outside)
    with pytest.raises(StorageError, match="artifact_unsafe_file"):
        await store.read(name)
    with pytest.raises(StorageError, match="artifact_key_conflict"):
        await store.save(name, b"replace")
    assert outside.read_bytes() == b"secret"
    assert (store.root / name).is_symlink()


async def test_concurrent_key_collision_does_not_overwrite(
    store: LocalFileArtifactStore,
) -> None:
    name = key()
    results = await asyncio.gather(
        store.save(name, b"first"), store.save(name, b"second"), return_exceptions=True
    )
    errors = [result for result in results if isinstance(result, StorageError)]
    assert len(errors) == 1
    assert errors[0].code == "artifact_key_conflict"
    assert await store.read(name) == (
        b"second" if isinstance(results[0], Exception) else b"first"
    )
    assert len(await store.inventory()) == 1


async def test_size_limit_and_special_file(
    store: LocalFileArtifactStore,
) -> None:
    with pytest.raises(StorageError, match="artifact_too_large"):
        await store.save(key(), b"x" * 1025)
    name = key()
    os.mkfifo(store.root / name)
    with pytest.raises(StorageError, match="artifact_unsafe_file"):
        await asyncio.wait_for(store.read(name), timeout=1)


@pytest.mark.parametrize("stage", ["_write", "_publish"])
async def test_storage_failure_cleans_only_its_temporary(
    store: LocalFileArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    preserved = key()
    await store.save(preserved, b"preserve")

    def fail(*args: object) -> None:
        raise OSError("injected disk failure")

    monkeypatch.setattr(store, stage, fail)
    with pytest.raises(StorageError) as caught:
        await store.save(key(), b"fails")
    assert not caught.value.published
    assert not caught.value.cleanup_failed
    assert await store.read(preserved) == b"preserve"
    assert len(await store.inventory()) == 1


async def test_cleanup_failure_is_reported_and_diagnosable(
    store: LocalFileArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object) -> None:
        raise OSError("injected")

    monkeypatch.setattr(store, "_write", fail)
    monkeypatch.setattr(store, "_cleanup_owned", lambda *args: False)
    with pytest.raises(StorageError) as caught:
        await store.save(key(), b"x")
    assert caught.value.cleanup_failed
    assert [entry.kind for entry in await store.inventory()] == ["temporary"]


async def test_post_publish_failure_retains_formal_file(
    store: LocalFileArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = key()
    monkeypatch.setattr(store, "_cleanup_owned", lambda *args: False)
    with pytest.raises(StorageError) as caught:
        await store.save(name, b"retain")
    assert caught.value.published and caught.value.cleanup_failed
    assert await store.read(name) == b"retain"
    assert {e.kind for e in await store.inventory()} == {"temporary", "object"}


async def test_cancel_waits_for_thread_completion(
    store: LocalFileArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = Event(), Event()
    original = store._write

    def delayed(fd: int, content: bytes):
        entered.set()
        assert release.wait(timeout=5)
        return original(fd, content)

    monkeypatch.setattr(store, "_write", delayed)
    name = key()
    task = asyncio.create_task(store.save(name, b"finish"))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.read(name) == b"finish"
