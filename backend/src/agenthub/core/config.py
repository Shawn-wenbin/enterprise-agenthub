"""Typed settings loaded through a single, cacheable entry point."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        populate_by_name=True,
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="ENV"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
