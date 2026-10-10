"""仅导入一次当前模型，供 Alembic 发现元数据。"""

from agenthub.artifacts.models import Artifact
from agenthub.db.base import Base

__all__ = ["Artifact", "Base"]
