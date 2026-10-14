"""保留历史归属标识，已确认项目关联由复合外键约束。"""

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "authorized_project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_artifacts_authorized_project",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "authorized_project_id IS NULL OR authorized_project_id = project_id",
            name="authorized_project_matches",
        ),
        CheckConstraint("status IN ('pending', 'ready', 'failed')", name="status"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size"),
        CheckConstraint(
            "status != 'ready' OR "
            "(content_hash IS NOT NULL AND size_bytes IS NOT NULL)",
            name="ready_metadata",
        ),
        Index("ix_artifacts_scope", "tenant_id", "project_id"),
        Index("ix_artifacts_pending", "status", "created_at"),
    )

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(68), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str] = mapped_column(String(128))
    authorized_project_id: Mapped[str | None] = mapped_column(String(36))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
