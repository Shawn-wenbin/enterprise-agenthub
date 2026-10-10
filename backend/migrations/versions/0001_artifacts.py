"""最小产物元数据；租户和项目外键推迟到单元 1.4 实现。

版本标识：0001_artifacts
上一版本：None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_artifacts"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(36), nullable=False),
        sa.Column("storage_key", sa.String(68), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')", name="ck_artifacts_status"
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0", name="ck_artifacts_size"
        ),
        sa.CheckConstraint(
            "status != 'ready' OR "
            "(content_hash IS NOT NULL AND size_bytes IS NOT NULL)",
            name="ck_artifacts_ready_metadata",
        ),
        sa.PrimaryKeyConstraint("artifact_id", name="pk_artifacts"),
        sa.UniqueConstraint("storage_key", name="uq_artifacts_storage_key"),
    )
    op.create_index("ix_artifacts_scope", "artifacts", ["tenant_id", "project_id"])
    op.create_index("ix_artifacts_pending", "artifacts", ["status", "created_at"])


def downgrade() -> None:
    # 仅在操作人员显式调用 downgrade 时删除数据。
    op.drop_index("ix_artifacts_pending", table_name="artifacts")
    op.drop_index("ix_artifacts_scope", table_name="artifacts")
    op.drop_table("artifacts")
