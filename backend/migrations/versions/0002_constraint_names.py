"""使 CHECK 约束名称与元数据一致，不重写已经执行的首个迁移版本。

版本标识：0002_constraint_names
上一版本：0001_artifacts
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_constraint_names"
down_revision: str | None = "0001_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 使用固定标识符，仅操作元数据；不重建表或丢失数据。
    op.execute(
        "ALTER TABLE artifacts RENAME CONSTRAINT "
        "ck_artifacts_ck_artifacts_status TO ck_artifacts_status"
    )
    op.execute(
        "ALTER TABLE artifacts RENAME CONSTRAINT "
        "ck_artifacts_ck_artifacts_size TO ck_artifacts_size"
    )
    op.execute(
        "ALTER TABLE artifacts RENAME CONSTRAINT "
        "ck_artifacts_ck_artifacts_ready_metadata TO ck_artifacts_ready_metadata"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE artifacts RENAME CONSTRAINT "
        "ck_artifacts_status TO ck_artifacts_ck_artifacts_status"
    )
    op.execute(
        "ALTER TABLE artifacts RENAME CONSTRAINT "
        "ck_artifacts_size TO ck_artifacts_ck_artifacts_size"
    )
    op.execute(
        "ALTER TABLE artifacts RENAME CONSTRAINT "
        "ck_artifacts_ready_metadata TO ck_artifacts_ck_artifacts_ready_metadata"
    )
