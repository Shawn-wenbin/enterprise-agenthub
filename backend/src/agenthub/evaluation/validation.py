"""只读验证固定合成素材，不导入/执行素材代码，不连接数据库或外网。

输入限可信本地、静止的工作副本；逐段拒绝符号链接，不承诺抵御同 UID 并发换文件。
"""

import hashlib
import json
import re
import stat
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from agenthub.evaluation.schemas import (
    DatasetManifest,
    FixtureManifest,
    PublicCase,
    relative_path,
)

CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})
FORBIDDEN = re.compile(
    r"expected[_ -](?:root[_ -]cause|evidence)|reference[_ -]patch|"
    r"hidden[_ -]tests?|grader[_ -]only|rubric|oracle|"
    r"(?:authorization|cookie|api[_ -]?key|password|secret)\s*[\"']?\s*[:=]",
    re.IGNORECASE,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def safe_directory(root: Path, value: str) -> Path:
    relative_path(value)
    require(root.is_dir() and not root.is_symlink(), "unsafe_root")
    candidate = root
    for part in value.split("/"):
        candidate = candidate / part
        require(not candidate.is_symlink(), f"symlink_rejected: {value}")
        require(candidate.is_dir(), f"missing_directory: {value}")
    return candidate


def safe_file(root: Path, value: str) -> Path:
    relative_path(value)
    require(not root.is_symlink() and root.is_dir(), "unsafe_root")
    candidate = root
    for part in value.split("/"):
        candidate = candidate / part
        require(not candidate.is_symlink(), f"symlink_rejected: {value}")
    require(candidate.is_file(), f"missing_file: {value}")
    require(candidate.resolve().is_relative_to(root.resolve()), "path_escape")
    require(stat.S_ISREG(candidate.stat().st_mode), "not_regular_file")
    return candidate


def inventory(root: Path) -> list[str]:
    require(root.is_dir() and not root.is_symlink(), "unsafe_root")
    result: list[str] = []
    for child in sorted(root.iterdir()):
        require(not child.is_symlink(), f"symlink_rejected: {child.name}")
        if child.is_dir():
            if child.name not in CACHE_DIRS:
                result.extend(f"{child.name}/{name}" for name in inventory(child))
        else:
            require(stat.S_ISREG(child.stat().st_mode), "not_regular_file")
            result.append(child.name)
    return result


def source_checksum(files: dict[str, str]) -> str:
    entries = [
        f"{path}\0{digest}\n"
        for path, digest in sorted(files.items())
        if path.startswith("source/")
    ]
    require(bool(entries), "empty_source_snapshot")
    return sha256("".join(entries).encode("utf-8"))


def check_public_text(text: str) -> None:
    require(FORBIDDEN.search(text) is None, "public_sensitive_marker")
    require(
        re.search(r"-----BEGIN .*PRIVATE KEY|\b(?:sk-|ghp_)[A-Za-z0-9]{20,}", text)
        is None,
        "credential_marker",
    )


def verify_fixture(repo: Path) -> FixtureManifest:
    root = safe_directory(repo, "fixtures/orderlab")
    # 先做路径/清单检查，扫描永远不会将 Python 当成可加载模块。
    actual = set(inventory(root)) - {"manifest.json"}
    manifest_content = safe_file(root, "manifest.json").read_bytes()
    check_public_text(manifest_content.decode("utf-8"))
    manifest = FixtureManifest.model_validate_json(manifest_content)
    require(manifest.fixture_version == "orderlab_v1", "unsupported_fixture_version")
    require(actual == set(manifest.files), "fixture_inventory_mismatch")
    for name, digest in manifest.files.items():
        content = safe_file(root, name).read_bytes()
        require(sha256(content) == digest, f"checksum_mismatch: {name}")
        text = content.decode("utf-8")
        check_public_text(text)
        if name.endswith((".py", ".md")):
            require("synthetic" in text, f"missing_synthetic_marker: {name}")
        if name.endswith(".json"):
            json.loads(text)
        if name.endswith(".jsonl"):
            for line in text.splitlines():
                row = json.loads(line)
                require(row.get("synthetic") is True, "missing_synthetic_marker")
                require(
                    row.get("fixture_version") == manifest.fixture_version,
                    "event_version_mismatch",
                )
    require(
        source_checksum(manifest.files) == manifest.source_checksum,
        "source_checksum_mismatch",
    )
    for ref in manifest.public_tests:
        path, separator, node = ref.partition("::")
        require(path in manifest.files and separator == "::", "invalid_public_test")
        require(bool(re.fullmatch(r"test_[a-z_]+", node)), "invalid_test_node")
        require(
            f"def {node}(" in safe_file(root, path).read_text(), "missing_test_node"
        )
    require(manifest.reproduction_guide in manifest.files, "missing_guide")
    env = manifest.environment
    require(sys.version_info[:2] == (3, 12), "python_version_mismatch")
    require(
        set(env.repository_checksums)
        == {"pyproject.toml", "uv.lock", ".python-version"},
        "dependency_contract_mismatch",
    )
    for name, digest in env.repository_checksums.items():
        require(
            sha256(safe_file(repo, name).read_bytes()) == digest,
            f"dependency_checksum_mismatch: {name}",
        )
    lock = tomllib.loads(safe_file(repo, "uv.lock").read_text())
    locked = {package["name"]: package["version"] for package in lock["package"]}
    require(
        set(env.dependencies)
        == {"fastapi", "pydantic", "starlette", "httpx2", "pytest", "pytest-asyncio"},
        "dependency_set_mismatch",
    )
    for name, declared in env.dependencies.items():
        require(
            version(name) == declared == locked[name],
            f"dependency_version_mismatch: {name}",
        )
    return manifest


def verify_dataset(repo: Path, fixture: FixtureManifest) -> list[PublicCase]:
    root = safe_directory(repo, "datasets")
    dataset = DatasetManifest.model_validate_json(
        safe_file(root, "manifests/orderlab_v1.json").read_bytes()
    )
    require(3 <= len(dataset.cases) <= 5, "initial_case_count_out_of_range")
    public_files = set(inventory(safe_directory(root, "public")))
    inputs: set[str] = set()
    ids: set[str] = set()
    cases = []
    for entry in dataset.cases:
        relative_path(entry.input_ref)
        require(
            entry.input_ref.startswith("public/") and entry.input_ref.endswith(".json"),
            "non_public_input",
        )
        content = safe_file(root, entry.input_ref).read_bytes()
        check_public_text(content.decode())
        case = PublicCase.model_validate_json(content)
        require(sha256(content) == entry.input_sha256, "public_input_checksum_mismatch")
        require(
            case.case_id not in ids and entry.input_ref not in inputs, "duplicate_case"
        )
        ids.add(case.case_id)
        inputs.add(entry.input_ref)
        for field in (
            "case_id",
            "dataset_version",
            "task_family",
            "problem_group",
            "fixture_version",
        ):
            require(
                getattr(entry, field) == getattr(case, field),
                f"case_manifest_mismatch: {field}",
            )
        require(
            case.dataset_version == dataset.dataset_version, "dataset_version_mismatch"
        )
        require(
            case.fixture_version == fixture.fixture_version, "unknown_fixture_version"
        )
        for ref in case.allowed_sources:
            require(ref in fixture.files, f"source_outside_fixture: {ref}")
            safe_file(repo / "fixtures" / "orderlab", ref)
        cases.append(case)
    require(
        public_files == {ref.removeprefix("public/") for ref in inputs},
        "unlisted_public_input",
    )
    return cases


def verify_materials(repo: Path) -> dict[str, Any]:
    from datetime import datetime, timedelta

    root = safe_directory(repo, "fixtures/orderlab")

    def rows(name: str) -> list[dict[str, Any]]:
        result = [
            json.loads(line) for line in safe_file(root, name).read_text().splitlines()
        ]
        require(bool(result), "empty_jsonl")
        times = []
        for row in result:
            require(row["timestamp"].endswith("Z"), "timestamp_not_utc")
            stamp = datetime.fromisoformat(row["timestamp"])
            require(stamp.utcoffset() == timedelta(0), "timestamp_not_utc")
            require(
                all(
                    row.get(key)
                    for key in (
                        "request_id",
                        "operation_id",
                        "mobile_version",
                        "path",
                        "event",
                    )
                ),
                "event_missing_context",
            )
            times.append(stamp)
        require(times == sorted(times), "timeline_not_sorted")
        return result

    client = rows("requests/client_requests.jsonl")
    server = rows("logs/server_events.jsonl")
    sent = [row for row in client if row["event"] == "request_sent"]
    received = [row for row in server if row["event"] == "request_received"]
    require(len(sent) == len(received) == 2, "request_count_mismatch")
    for request, event in zip(sent, received, strict=True):
        for field in (
            "request_id",
            "operation_id",
            "mobile_version",
            "path",
            "method",
            "body",
        ):
            require(request[field] == event[field], f"request_log_mismatch: {field}")
        related = [row for row in server if row["request_id"] == request["request_id"]]
        require(
            [row["event"] for row in related]
            == ["request_received", "order_persisted"],
            "server_sequence_mismatch",
        )
        terminal = [
            row for row in client if row["request_id"] == request["request_id"]
        ][-1]
        require(
            request["timestamp"]
            < event["timestamp"]
            < related[-1]["timestamp"]
            < terminal["timestamp"],
            "causal_order_mismatch",
        )
    require(
        [r["event"] for r in client]
        == ["request_sent", "response_timeout", "request_sent", "response_received"],
        "client_sequence_mismatch",
    )
    require(
        len({r["operation_id"] for r in sent}) == 1
        and len({r["request_id"] for r in sent}) == 2,
        "operation_identity_mismatch",
    )
    persisted = [r for r in server if r["event"] == "order_persisted"]
    require(len({r["order_id"] for r in persisted}) == 2, "order_observation_mismatch")
    require(
        client[-1]["order_id"] == persisted[-1]["order_id"], "response_order_mismatch"
    )
    probes = rows("requests/schema_probes.jsonl")
    require(
        [row["status_code"] for row in probes] == [201, 422], "schema_probe_mismatch"
    )
    rows("requests/uncertain_requests.jsonl")
    tickets = json.loads(safe_file(root, "tickets/history.json").read_bytes())
    require(
        tickets["synthetic"] is True and tickets["fixture_version"] == "orderlab_v1",
        "ticket_provenance",
    )
    ids = set()
    for ticket in tickets["tickets"]:
        require(ticket.get("synthetic") is True, "ticket_synthetic")
        require(
            all(
                ticket.get(field)
                for field in (
                    "ticket_id",
                    "title",
                    "status",
                    "created_at",
                    "affected_version",
                    "symptom",
                    "investigation_summary",
                )
            ),
            "ticket_schema",
        )
        require(ticket["ticket_id"] not in ids, "duplicate_ticket")
        ids.add(ticket["ticket_id"])
        require(ticket["created_at"].endswith("Z"), "ticket_timestamp")
        datetime.fromisoformat(ticket["created_at"])
        if ticket["status"] == "closed":
            require(bool(ticket.get("resolution_summary")), "missing_resolution")
    return {
        "client_events": len(client),
        "server_events": len(server),
        "tickets": len(ids),
    }
