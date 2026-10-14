"""项目及显式 ACL；复合外键拒绝跨租户授权记录。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.auth.models import utc_now
from agenthub.db.base import Base
from agenthub.tenants.models import SLUG_CHECK


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(SLUG_CHECK, name="slug"),
        UniqueConstraint("tenant_id", "slug", name="uq_projects_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_projects_tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", name="fk_projects_tenant", ondelete="RESTRICT"),
    )
    slug: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class ResourceACL(Base):
    __tablename__ = "resource_acl"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_resource_acl_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_resource_acl_membership",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "membership_id", "project_id", "scope", name="uq_resource_acl_grant"
        ),
        CheckConstraint(
            "scope IN ('read', 'develop', 'review', 'manage')", name="scope"
        ),
        Index("ix_resource_acl_project", "tenant_id", "project_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(String(36))
    project_id: Mapped[str] = mapped_column(String(36))
    membership_id: Mapped[str] = mapped_column(String(36))
    scope: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
