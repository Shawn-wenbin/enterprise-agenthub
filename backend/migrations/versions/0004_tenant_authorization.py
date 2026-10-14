"""新增租户授权与可为空的已确认产物归属，不修改历史归属。

版本标识：0004_tenant_authorization
上一版本：0003_users_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_tenant_authorization"
down_revision: str | None = "0003_users_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 所有新表初始为空；旧 Artifact 关联保持 NULL，不推断或伪造归属。
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name=op.f("ck_tenants_slug")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_memberships_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )
    op.create_index(
        op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{0,62}$'", name=op.f("ck_projects_slug")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_projects_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_projects_tenant_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_projects_tenant_slug"),
    )
    op.create_table(
        "membership_roles",
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "role IN ('tenant_admin', 'developer', 'reviewer', 'viewer')",
            name=op.f("ck_membership_roles_role"),
        ),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["memberships.id"],
            name="fk_membership_roles_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "membership_id", "role", name=op.f("pk_membership_roles")
        ),
    )
    op.create_table(
        "resource_acl",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("membership_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('read', 'develop', 'review', 'manage')",
            name=op.f("ck_resource_acl_scope"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_resource_acl_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_resource_acl_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource_acl")),
        sa.UniqueConstraint(
            "membership_id", "project_id", "scope", name="uq_resource_acl_grant"
        ),
    )
    op.create_index(
        "ix_resource_acl_project",
        "resource_acl",
        ["tenant_id", "project_id"],
        unique=False,
    )
    op.add_column(
        "artifacts",
        sa.Column("authorized_project_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_artifacts_authorized_project",
        "artifacts",
        "projects",
        ["tenant_id", "authorized_project_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_artifacts_authorized_project_matches"),
        "artifacts",
        "authorized_project_id IS NULL OR authorized_project_id = project_id",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_artifacts_authorized_project_matches"), "artifacts", type_="check"
    )
    op.drop_constraint(
        "fk_artifacts_authorized_project", "artifacts", type_="foreignkey"
    )
    op.drop_column("artifacts", "authorized_project_id")
    op.drop_index("ix_resource_acl_project", table_name="resource_acl")
    op.drop_table("resource_acl")
    op.drop_table("membership_roles")
    op.drop_table("projects")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("tenants")
