"""固定能力合同；项目权限不能替代未来具体资源与审批上下文检查。"""

from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    TENANT_ADMIN = "tenant_admin"
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class Action(StrEnum):
    MEMBERS_MANAGE = "members.manage"
    PROJECT_METADATA_READ = "project.metadata.read"
    PROJECT_MANAGE = "project.manage"
    SOURCES_MANAGE = "sources.manage"
    CONNECTORS_MANAGE = "connectors.manage"
    RETENTION_MANAGE = "retention.manage"
    RUN_CREATE = "run.create"
    RUN_STATUS_READ = "run.status.read"
    RUN_CANCEL_OWN = "run.cancel_own"
    REPORT_READ = "report.read"
    EVIDENCE_READ = "evidence.read"
    ACTION_DRAFT = "action.draft"
    APPROVAL_DECIDE = "approval.decide"


class ResourceScope(StrEnum):
    READ = "read"
    DEVELOP = "develop"
    REVIEW = "review"
    MANAGE = "manage"


ROLE_ACTIONS = MappingProxyType(
    {
        Role.TENANT_ADMIN: frozenset(
            {
                Action.MEMBERS_MANAGE,
                Action.PROJECT_METADATA_READ,
                Action.PROJECT_MANAGE,
                Action.SOURCES_MANAGE,
                Action.CONNECTORS_MANAGE,
                Action.RETENTION_MANAGE,
            }
        ),
        Role.DEVELOPER: frozenset(
            {
                Action.PROJECT_METADATA_READ,
                Action.RUN_CREATE,
                Action.RUN_STATUS_READ,
                Action.RUN_CANCEL_OWN,
                Action.REPORT_READ,
                Action.EVIDENCE_READ,
                Action.ACTION_DRAFT,
            }
        ),
        Role.REVIEWER: frozenset(
            {
                Action.PROJECT_METADATA_READ,
                Action.RUN_STATUS_READ,
                Action.REPORT_READ,
                Action.APPROVAL_DECIDE,
            }
        ),
        Role.VIEWER: frozenset(
            {
                Action.PROJECT_METADATA_READ,
                Action.RUN_STATUS_READ,
                Action.REPORT_READ,
                Action.EVIDENCE_READ,
            }
        ),
    }
)

# 当前只有项目元数据业务；后续动作必须在具体业务的写入/读取边界补足上下文。
PROJECT_ACTION_SCOPES = MappingProxyType(
    {
        Action.PROJECT_METADATA_READ: frozenset(ResourceScope),
        Action.PROJECT_MANAGE: frozenset({ResourceScope.MANAGE}),
    }
)
TENANT_ACTIONS = frozenset({Action.MEMBERS_MANAGE, Action.RETENTION_MANAGE})
ADMIN_METADATA_ACTIONS = frozenset(
    {Action.PROJECT_METADATA_READ, Action.PROJECT_MANAGE}
)


def role_allows(roles: frozenset[Role], action: Action) -> bool:
    return any(action in ROLE_ACTIONS[role] for role in roles)
