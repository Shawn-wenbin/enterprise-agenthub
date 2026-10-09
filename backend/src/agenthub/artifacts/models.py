"""Only the metadata needed by Unit 1.2; ownership FKs arrive in Unit 1.4."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
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
