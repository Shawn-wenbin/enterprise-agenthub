"""HTTP 边界只暴露最小身份，密码不会进入响应。"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def normalize_login_name(value: str) -> str:
    # 只允许 ASCII 标识；首尾空白去除、大小写不敏感，不做 Unicode 同形映射。
    normalized = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@+-]{0,127}", normalized):
        raise ValueError("Invalid login name")
    return normalized


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_name: str = Field(min_length=1, max_length=256)
    password: SecretStr = Field(min_length=1, max_length=256)

    @field_validator("login_name")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalize_login_name(value)


class UserIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    login_name: str


class CsrfResponse(BaseModel):
    csrf_token: str
    purpose: Literal["login", "session"]
