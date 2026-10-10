"""配置默认值、来源优先级与校验边界。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agenthub.core.config import Settings, get_settings


def test_missing_env_uses_development_default() -> None:
    assert get_settings().env == "development"


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_env_is_read_without_a_prefix(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENV", environment)
    assert get_settings().env == environment


@pytest.mark.parametrize("environment", ["staging", "", "Development"])
def test_invalid_env_reports_the_field(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENV", environment)
    with pytest.raises(ValidationError) as caught:
        get_settings()
    error = caught.value.errors()[0]
    assert error["loc"] == ("ENV",)
    assert error["type"] == "literal_error"


def test_dotenv_is_loaded_from_the_working_directory(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ENV=test\n", encoding="utf-8")
    assert get_settings().env == "test"


def test_environment_overrides_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("ENV=development\n", encoding="utf-8")
    monkeypatch.setenv("ENV", "production")
    assert get_settings().env == "production"


def test_settings_can_be_injected_without_loading_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ENV=invalid\n", encoding="utf-8")
    assert Settings(env="test", _env_file=None).env == "test"


def test_cached_settings_can_be_reloaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "test")
    first = get_settings()
    monkeypatch.setenv("ENV", "production")
    assert get_settings() is first
    get_settings.cache_clear()
    assert get_settings().env == "production"


def test_missing_required_urls_fail_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.delenv("REDIS_URL")
    with pytest.raises(ValidationError) as caught:
        get_settings()
    assert {e["loc"][0] for e in caught.value.errors()} == {"DATABASE_URL", "REDIS_URL"}


def test_invalid_url_does_not_expose_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "mysql://user:private-password@localhost/db")
    with pytest.raises(ValidationError) as caught:
        get_settings()
    assert "private-password" not in str(caught.value)
