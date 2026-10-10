"""围绕受控文件读写使用短元数据事务；仅供内部调用。"""

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from agenthub.artifacts.models import Artifact
from agenthub.artifacts.store import ArtifactStore, StorageError, StoredContent
from agenthub.core.errors import AppError
from agenthub.core.request_context import log_event
from agenthub.db.database import Database


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class InternalArtifactScope(BaseModel):
    """由可信服务端代码构造，不绑定 HTTP 请求或模型参数。

    此处限定资源归属范围，不提供身份认证。单元 1.4 将补充 Principal、
    成员关系与 ACL 检查，以及租户和项目外键。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)


class ArtifactMetadata(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    artifact_id: str
    tenant_id: str
    project_id: str
    content_hash: str | None
    size_bytes: int | None
    media_type: str
    status: Literal["pending", "ready", "failed"]
    created_at: datetime
    updated_at: datetime


class DiagnosticIssue(BaseModel):
    code: str
    artifact_id: str | None = None
    storage_key: str | None = None


class ArtifactService:
    def __init__(
        self,
        database: Database,
        store: ArtifactStore,
        max_bytes: int,
        pending_seconds: int = 300,
    ) -> None:
        self.database = database
        self.store = store
        self.max_bytes = max_bytes
        self.pending_seconds = pending_seconds

    async def save(
        self,
        scope: InternalArtifactScope,
        content: bytes,
        media_type: str = "application/octet-stream",
    ) -> ArtifactMetadata:
        if len(content) > self.max_bytes:
            raise AppError("artifact_too_large", "Artifact exceeds size limit", 413)
        if not media_type or len(media_type) > 255:
            raise AppError("validation_error", "Invalid artifact media type", 422)
        artifact_id = str(uuid4())
        key = f"obj_{secrets.token_hex(32)}"
        try:
            async with self.database.session() as session, session.begin():
                session.add(
                    Artifact(
                        artifact_id=artifact_id,
                        storage_key=key,
                        tenant_id=scope.tenant_id,
                        project_id=scope.project_id,
                        media_type=media_type,
                        status="pending",
                    )
                )
        except SQLAlchemyError:
            # pending 状态提交失败或结果未知时，不进入文件保存阶段。
            raise AppError(
                "dependency_unavailable",
                "Artifact metadata could not be saved",
                503,
                {"artifact_id": artifact_id},
            ) from None
        try:
            saved = await self.store.save(key, content)
        except StorageError as exc:
            if not exc.published:
                await self._mark_failed(artifact_id, exc.code)
            # 保留已发布的对象，不将其报告为 ready，也不删除。
            raise AppError(
                exc.code,
                "Artifact storage failed",
                503,
                {
                    "artifact_id": artifact_id,
                    "published": exc.published,
                    "cleanup_failed": exc.cleanup_failed,
                },
            ) from None
        try:
            return await self._mark_ready(artifact_id, saved)
        except SQLAlchemyError:
            log_event("artifact_ready_commit_failed", artifact_id=artifact_id)
            # 提交结果可能未知。此处仅执行只读对账。
            raise AppError(
                "artifact_metadata_unconfirmed",
                "Artifact completion could not be confirmed",
                503,
                {"artifact_id": artifact_id},
            ) from None

    async def _mark_ready(
        self, artifact_id: str, saved: StoredContent
    ) -> ArtifactMetadata:
        async with self.database.session() as session:
            async with session.begin():
                artifact = await session.get(
                    Artifact, artifact_id, with_for_update=True
                )
                if artifact is None or artifact.status != "pending":
                    raise AppError(
                        "artifact_state_conflict", "Artifact state changed", 409
                    )
                artifact.content_hash = saved.content_hash
                artifact.size_bytes = saved.size_bytes
                artifact.status = "ready"
                await session.flush()
                result = ArtifactMetadata.model_validate(artifact)
            return result  # 仅在提交成功后返回。

    async def _mark_failed(self, artifact_id: str, code: str) -> None:
        try:
            async with self.database.session() as session, session.begin():
                artifact = await session.get(
                    Artifact, artifact_id, with_for_update=True
                )
                if artifact is not None and artifact.status == "pending":
                    artifact.status = "failed"
                    artifact.failure_code = code
        except SQLAlchemyError:
            log_event(
                "artifact_failed_commit_failed", artifact_id=artifact_id, code=code
            )

    async def read(self, scope: InternalArtifactScope, artifact_id: str) -> bytes:
        async with self.database.session() as session:
            artifact = await session.scalar(
                select(Artifact).where(
                    Artifact.artifact_id == artifact_id,
                    Artifact.tenant_id == scope.tenant_id,
                    Artifact.project_id == scope.project_id,
                )
            )
            if artifact is None:
                raise AppError("artifact_not_found", "Artifact not found", 404)
            if artifact.status != "ready":
                raise AppError("artifact_not_ready", "Artifact is not ready", 409)
            key, digest, size = (
                artifact.storage_key,
                artifact.content_hash,
                artifact.size_bytes,
            )
        # 在任何文件读写之前关闭读取事务和会话。
        try:
            content = await self.store.read(key)
        except StorageError as exc:
            raise AppError(exc.code, "Artifact content is unavailable", 503) from None
        actual_hash = await asyncio.to_thread(content_hash, content)
        if len(content) != size or actual_hash != digest:
            raise AppError(
                "artifact_integrity_error", "Artifact content is corrupt", 503
            )
        return content

    async def diagnose(self) -> list[DiagnosticIssue]:
        """仅供可信操作人员执行的只读预演，无 HTTP 端点，不修改数据。

        结果为并发快照，不代表全局恢复决策。
        """
        async with self.database.session() as session:
            artifacts = list((await session.scalars(select(Artifact))).all())
        known = {artifact.storage_key: artifact for artifact in artifacts}
        issues: list[DiagnosticIssue] = []
        threshold = datetime.now(UTC) - timedelta(seconds=self.pending_seconds)
        for artifact in artifacts:

            def issue(code: str, current: Artifact = artifact) -> DiagnosticIssue:
                return DiagnosticIssue(
                    code=code,
                    artifact_id=current.artifact_id,
                    storage_key=current.storage_key,
                )

            if artifact.status == "pending" and artifact.created_at < threshold:
                issues.append(issue("stale_pending"))
            try:
                content = await self.store.read(artifact.storage_key)
            except StorageError as exc:
                if exc.code != "artifact_missing" or artifact.status == "ready":
                    issues.append(issue(exc.code))
                continue
            if artifact.status != "ready":
                issues.append(issue("unconfirmed_file"))
            actual_hash = await asyncio.to_thread(content_hash, content)
            if (
                artifact.size_bytes is not None and len(content) != artifact.size_bytes
            ) or (
                artifact.content_hash is not None
                and actual_hash != artifact.content_hash
            ):
                issues.append(issue("artifact_integrity_error"))
        for entry in await self.store.inventory():
            if entry.kind == "object" and entry.key not in known:
                issues.append(
                    DiagnosticIssue(code="orphan_object", storage_key=entry.key)
                )
            elif entry.kind != "object":
                issues.append(DiagnosticIssue(code=entry.kind, storage_key=entry.key))
        return issues
