"""Unit 1.5 的严格边界 Schema；未知字段和不受控路径一律拒绝。"""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def relative_path(value: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9_.\-/]+", value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or value.startswith("/")
    ):
        raise ValueError("unsafe_relative_path")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PublicCase(Contract):
    case_id: Slug
    dataset_version: Slug
    task_family: Slug
    problem_group: Slug
    fixture_version: Slug
    synthetic: Literal[True]
    query: NonEmpty
    allowed_sources: list[NonEmpty] = Field(min_length=1)

    @model_validator(mode="after")
    def check_sources(self) -> Self:
        if len(set(self.allowed_sources)) != len(self.allowed_sources):
            raise ValueError("duplicate_allowed_source")
        for value in self.allowed_sources:
            relative_path(value)
        return self


class DatasetEntry(Contract):
    case_id: Slug
    dataset_version: Slug
    task_family: Slug
    problem_group: Slug
    input_ref: NonEmpty
    input_sha256: Digest
    fixture_version: Slug
    grader_ref: None = None
    grader_status: Literal["not_implemented"]
    policy_assertions: list[str] = Field(max_length=0)


class DatasetManifest(Contract):
    dataset_version: Slug
    synthetic: Literal[True]
    visibility: Literal["evaluation_metadata_only"]
    cases: list[DatasetEntry] = Field(min_length=1)


class Environment(Contract):
    python: Literal["3.12"]
    dependency_source: Literal["root_uv_lock"]
    dependencies: dict[NonEmpty, NonEmpty] = Field(min_length=1)
    repository_checksums: dict[NonEmpty, Digest] = Field(min_length=1)


class FixtureManifest(Contract):
    fixture_id: Literal["orderlab"]
    fixture_version: Slug
    synthetic: Literal[True]
    symptom: NonEmpty
    checksum_algorithm: Literal["SHA-256"]
    source_root: Literal["source"]
    source_checksum: Digest
    source_checksum_rule: Literal["sha256(sorted(path + NUL + file_sha256 + LF))"]
    files: dict[NonEmpty, Digest] = Field(min_length=1)
    public_tests: list[NonEmpty] = Field(min_length=1)
    reproduction_guide: NonEmpty
    environment: Environment
