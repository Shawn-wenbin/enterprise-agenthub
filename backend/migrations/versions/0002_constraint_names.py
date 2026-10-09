"""Align CHECK names with metadata without rewriting the applied first revision.

Revision ID: 0002_constraint_names
Revises: 0001_artifacts
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_constraint_names"
down_revision: str | None = "0001_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fixed identifiers, metadata-only operations; no table rebuild or data loss.
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
