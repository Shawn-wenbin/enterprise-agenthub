"""固定能力与默认拒绝合同，不用伪造 Principal 代替真实授权链路。"""

from dataclasses import FrozenInstanceError

import pytest
from pydantic import SecretStr, ValidationError

from agenthub.auth.security import PasswordSecurity
from agenthub.authorization.policy import Action, Role, role_allows
from agenthub.authorization.schemas import normalize_slug
from agenthub.authorization.service import Principal
from agenthub.cli.seed import DEMO_LOGINS, DemoSeedService
from agenthub.core.config import Settings
from agenthub.core.errors import AppError
from agenthub.db.database import Database


@pytest.mark.parametrize(
    "role,allowed,denied",
    [
        (Role.TENANT_ADMIN, Action.PROJECT_MANAGE, Action.EVIDENCE_READ),
        (Role.DEVELOPER, Action.RUN_CREATE, Action.APPROVAL_DECIDE),
        (Role.REVIEWER, Action.APPROVAL_DECIDE, Action.ACTION_DRAFT),
        (Role.VIEWER, Action.REPORT_READ, Action.RUN_CREATE),
    ],
)
def test_role_capability_contract(role: Role, allowed: Action, denied: Action) -> None:
    assert role_allows(frozenset({role}), allowed)
    assert not role_allows(frozenset({role}), denied)
    assert not role_allows(frozenset(), allowed)


def test_combined_roles_and_immutable_principal() -> None:
    roles = frozenset({Role.DEVELOPER, Role.REVIEWER})
    assert role_allows(roles, Action.RUN_CREATE)
    assert role_allows(roles, Action.APPROVAL_DECIDE)
    principal = Principal("user", "tenant", "membership", roles)
    with pytest.raises(FrozenInstanceError):
        principal.tenant_id = "other"  # type: ignore[misc]


def test_slug_normalization_and_unknown_environment() -> None:
    assert normalize_slug(" Demo-A ") == "demo-a"
    for invalid in ("", "a/b", "Ａ", "a b", "a" * 64):
        with pytest.raises(ValueError):
            normalize_slug(invalid)
    with pytest.raises(ValidationError):
        Settings(env="staging")  # type: ignore[arg-type,call-arg]


@pytest.mark.parametrize("mode", ["production", "missing", "short", "partial"])
async def test_seed_gate_before_database_or_password_access(mode: str) -> None:
    settings = Settings()  # type: ignore[call-arg]
    if mode == "production":
        # 服务在数据库访问之前白名单校验；独立测试已有 Settings 的生产安全校验。
        settings = settings.model_copy(update={"env": "production"})
    elif mode in {"short", "partial"}:
        settings.demo_seed_passwords = {
            login: SecretStr("short" if mode == "short" else "x" * 20)
            for login in (DEMO_LOGINS if mode == "short" else DEMO_LOGINS[:1])
        }
    database = Database(settings)
    try:
        with pytest.raises(AppError) as caught:
            await DemoSeedService(settings, database, PasswordSecurity(1)).run()
        assert caught.value.code == (
            "seed_environment_forbidden"
            if mode == "production"
            else "seed_configuration_required"
        )
        assert database.engine.pool.checkedout() == 0
    finally:
        await database.close()
