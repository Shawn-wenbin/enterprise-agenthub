"""固定多角色成员关系；用户表不保存全局租户或角色。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from agenthub.auth.models import utc_now
from agenthub.db.base import Base

SLUG_CHECK = "slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'"


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint(SLUG_CHECK, name="slug"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    slug: Mapped[str] = mapped_column(String(63), unique=True)
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


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", name="fk_memberships_tenant", ondelete="RESTRICT"),
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", name="fk_memberships_user", ondelete="RESTRICT"),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class MembershipRole(Base):
    __tablename__ = "membership_roles"
    __table_args__ = (
        CheckConstraint(
            "role IN ('tenant_admin', 'developer', 'reviewer', 'viewer')", name="role"
        ),
    )

    membership_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(
            "memberships.id", name="fk_membership_roles_membership", ondelete="CASCADE"
        ),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
