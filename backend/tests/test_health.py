"""HTTP 健康检查与启动配置校验。"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agenthub.core.config import Settings
from agenthub.main import create_app


def test_health_returns_only_process_status() -> None:
    application = create_app()
    with TestClient(application) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert application.state.settings.env == "development"


def test_invalid_settings_fail_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "invalid")
    application = create_app()
    with pytest.raises(ValidationError, match="ENV"), TestClient(application):
        pass


def test_factory_accepts_explicit_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "invalid")
    settings = Settings(env="test", _env_file=None)
    application = create_app(settings=settings)
    with TestClient(application) as client:
        assert application.state.settings == settings
        assert client.get("/health").json() == {"status": "ok"}
