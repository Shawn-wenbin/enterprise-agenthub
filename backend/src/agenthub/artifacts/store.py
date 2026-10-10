"""可替换的字节存储契约；不依赖数据库或授权组件。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredContent:
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class StoreEntry:
    key: str
    kind: str


class StorageError(Exception):
    def __init__(
        self,
        code: str,
        *,
        published: bool = False,
        cleanup_failed: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.published = published
        self.cleanup_failed = cleanup_failed


class ArtifactStore(Protocol):
    async def save(self, storage_key: str, content: bytes) -> StoredContent: ...
    async def read(self, storage_key: str) -> bytes: ...
    async def inventory(self) -> list[StoreEntry]: ...
