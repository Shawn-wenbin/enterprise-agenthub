"""仅供特权内部调用的只读诊断命令行工具；不执行修复或删除。

从仓库根目录运行：uv run python -m agenthub.artifacts.diagnose
"""

import asyncio
import json

from agenthub.artifacts.local import LocalFileArtifactStore
from agenthub.artifacts.service import ArtifactService
from agenthub.core.config import get_settings
from agenthub.db.database import Database


async def diagnose() -> None:
    settings = get_settings()
    database = Database(settings)
    store = LocalFileArtifactStore(settings.artifact_root, settings.artifact_max_bytes)
    try:
        # 诊断运行时连存储根目录也不创建。
        await store.open(create_root=False)
        service = ArtifactService(
            database,
            store,
            settings.artifact_max_bytes,
            settings.artifact_pending_seconds,
        )
        issues = await service.diagnose()
        print(
            json.dumps(
                {
                    "mode": "read_only",
                    "issues": [issue.model_dump() for issue in issues],
                },
                indent=2,
            )
        )
    finally:
        await store.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(diagnose())
