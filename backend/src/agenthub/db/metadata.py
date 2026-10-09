"""Import current models exactly once for Alembic metadata discovery."""

from agenthub.artifacts.models import Artifact
from agenthub.db.base import Base

__all__ = ["Artifact", "Base"]
