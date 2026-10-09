"""POSIX local volume: dirfd confinement, no-follow IO and no-clobber publishing."""

import asyncio
import hashlib
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from agenthub.artifacts.store import StorageError, StoredContent, StoreEntry
from agenthub.core.request_context import log_event

KEY_PATTERN = re.compile(r"obj_[0-9a-f]{64}\Z")
TEMP_PATTERN = re.compile(r"\.tmp_[0-9a-f]{64}\Z")
CHUNK_BYTES = 1024 * 1024


async def offload[T](operation: Callable[[], T]) -> T:
    # Cancellation cannot stop a running OS thread. Drain it before allowing
    # lifespan teardown to close the pinned directory descriptor.
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


class LocalFileArtifactStore:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root.absolute()
        self.max_bytes = max_bytes
        self._directory_fd: int | None = None

    async def open(self, *, create_root: bool = True) -> None:
        if self._directory_fd is not None:
            raise StorageError("artifact_store_already_open")

        def open_root() -> None:
            self._directory_fd = self._open_root(create_root=create_root)

        await offload(open_root)

    def _open_root(self, *, create_root: bool) -> int:
        # Walk every component using directory descriptors. No resolve() that
        # could silently accept a symlink; directories are pinned after opening.
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.root.anchor, flags)
        try:
            for component in self.root.parts[1:]:
                if component in {".", ".."}:
                    raise StorageError("artifact_invalid_root")
                if create_root:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            root_info = os.fstat(fd)
            if root_info.st_uid != os.geteuid() or root_info.st_mode & 0o022:
                raise StorageError("artifact_unsafe_root_permissions")
            return fd
        except BaseException:
            os.close(fd)
            raise

    async def close(self) -> None:
        if self._directory_fd is not None:
            os.close(self._directory_fd)
            self._directory_fd = None

    def _fd(self) -> int:
        if self._directory_fd is None:
            raise StorageError("artifact_store_closed")
        return self._directory_fd

    @staticmethod
    def _validate_key(key: str) -> None:
        if not KEY_PATTERN.fullmatch(key):
            raise StorageError("artifact_invalid_key")

    async def save(self, storage_key: str, content: bytes) -> StoredContent:
        self._validate_key(storage_key)
        if len(content) > self.max_bytes:
            raise StorageError("artifact_too_large")
        return await offload(lambda: self._save(storage_key, content))

    def _write(self, fd: int, content: bytes) -> StoredContent:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(os.dup(fd), "wb") as output:
            for offset in range(0, len(content), CHUNK_BYTES):
                chunk = content[offset : offset + CHUNK_BYTES]
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.lseek(fd, 0, os.SEEK_SET)
        # Verify the actual saved bytes, not just the caller's input.
        saved = self._read_fd(fd)
        result = StoredContent(digest.hexdigest(), size)
        if (
            len(saved) != size
            or hashlib.sha256(saved).hexdigest() != result.content_hash
        ):
            raise StorageError("artifact_integrity_error")
        return result

    def _publish(self, temporary: str, key: str, directory: int) -> None:
        # link() atomically fails with EEXIST. Unlike replace(), it never
        # overwrites an existing regular file or symlink at the destination.
        os.link(
            temporary,
            key,
            src_dir_fd=directory,
            dst_dir_fd=directory,
            follow_symlinks=False,
        )

    def _cleanup_owned(
        self,
        name: str,
        identity: tuple[int, int],
        directory: int,
    ) -> bool:
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                return False
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _save(self, key: str, content: bytes) -> StoredContent:
        directory = self._fd()
        temporary = f".tmp_{uuid4().hex}{uuid4().hex}"
        fd: int | None = None
        identity: tuple[int, int] | None = None
        published = False
        stage = "artifact_write_failed"
        try:
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            info = os.fstat(fd)
            identity = (info.st_dev, info.st_ino)
            saved = self._write(fd, content)
            # Revalidate the owned source before publication. This controlled
            # root must not be writable by untrusted host users/processes.
            current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise StorageError("artifact_source_changed")
            stage = "artifact_publish_failed"
            self._publish(temporary, key, directory)
            published = True
            os.fsync(directory)
            if not self._cleanup_owned(temporary, identity, directory):
                raise StorageError(
                    "artifact_cleanup_failed", published=True, cleanup_failed=True
                )
            return saved
        except Exception as exc:
            cleanup_failed = False
            if identity is not None:
                cleanup_failed = not self._cleanup_owned(temporary, identity, directory)
            code = exc.code if isinstance(exc, StorageError) else stage
            if isinstance(exc, FileExistsError):
                code = "artifact_key_conflict"
            log_event(
                "artifact_storage_failed",
                code=code,
                storage_key=key,
                temporary_key=temporary,
                published=published,
                cleanup_failed=cleanup_failed,
            )
            raise StorageError(
                code, published=published, cleanup_failed=cleanup_failed
            ) from None
        finally:
            if fd is not None:
                os.close(fd)

    def _read_fd(self, fd: int) -> bytes:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise StorageError("artifact_unsafe_file")
        if info.st_size > self.max_bytes:
            raise StorageError("artifact_too_large")
        with os.fdopen(os.dup(fd), "rb") as source:
            content = source.read(self.max_bytes + 1)
        if len(content) > self.max_bytes:
            raise StorageError("artifact_too_large")
        return content

    def _read(self, key: str) -> bytes:
        try:
            # NONBLOCK prevents a malicious FIFO from hanging before fstat().
            fd = os.open(
                key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd()
            )
            try:
                return self._read_fd(fd)
            finally:
                os.close(fd)
        except FileNotFoundError:
            raise StorageError("artifact_missing") from None
        except OSError:
            raise StorageError("artifact_unsafe_file") from None

    async def read(self, storage_key: str) -> bytes:
        self._validate_key(storage_key)
        return await offload(lambda: self._read(storage_key))

    def _inventory(self) -> list[StoreEntry]:
        entries = []
        directory = self._fd()
        for name in sorted(os.listdir(directory)):
            try:
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                continue  # Concurrent publication/removal: this is a snapshot.
            kind = "unknown_entry"
            if stat.S_ISLNK(info.st_mode):
                kind = "unsafe_entry"
            elif stat.S_ISREG(info.st_mode):
                if TEMP_PATTERN.fullmatch(name):
                    kind = "temporary"
                elif KEY_PATTERN.fullmatch(name):
                    kind = "object"
            entries.append(StoreEntry(name, kind))
        return entries

    async def inventory(self) -> list[StoreEntry]:
        return await offload(self._inventory)
