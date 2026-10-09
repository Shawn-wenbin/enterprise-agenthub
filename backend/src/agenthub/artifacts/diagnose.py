"""Privileged internal read-only diagnostic CLI; never repairs or deletes.

Run from the repository root: uv run python -m agenthub.artifacts.diagnose
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
        # Even the storage root is not created by a diagnostic run.
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
