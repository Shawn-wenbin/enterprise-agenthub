"""Keep settings tests independent of the developer's environment."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from agenthub.core.config import get_settings


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("env", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
