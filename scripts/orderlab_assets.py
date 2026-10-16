"""显式执行受信任的 synthetic 快照，生成或对比固定素材；不接受外部源码路径。"""

import argparse
import asyncio
import importlib
import json
import sys
from importlib.metadata import version
from pathlib import Path
from types import ModuleType

from agenthub.evaluation.schemas import PublicCase
from agenthub.evaluation.validation import (
    inventory,
    require,
    sha256,
    source_checksum,
    verify_fixture,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "fixtures" / "orderlab"


def canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()


def jsonl(rows: list[dict[str, object]]) -> bytes:
    return "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    ).encode()


def trusted_runtime() -> tuple[ModuleType, ModuleType]:
    # 这个固定路径只在用户显式运行本工具/验证入口时加载，平台扫描不调用它。
    sys.path.insert(0, str(FIXTURE / "source"))
    return importlib.import_module("orderlab"), importlib.import_module("scenario")


def generated_materials() -> dict[str, bytes]:
    app, scenario = trusted_runtime()
    observation = asyncio.run(scenario.replay())
    return {
        "specs/openapi.json": canonical_json(app.create_app().openapi()),
        "requests/client_requests.jsonl": jsonl(observation["client_events"]),
        "logs/server_events.jsonl": jsonl(observation["server_events"]),
        "requests/schema_probes.jsonl": jsonl(asyncio.run(scenario.schema_probe())),
        "requests/uncertain_requests.jsonl": jsonl([scenario.uncertain_request()]),
    }


def check_generated() -> list[str]:
    verify_fixture(REPO)
    generated = generated_materials()
    for name, content in generated.items():
        require(
            (FIXTURE / name).read_bytes() == content,
            f"generated_material_drift: {name}",
        )
    return list(generated)


def initialize() -> None:
    require(
        not (FIXTURE / "manifest.json").exists(),
        "fixture_already_initialized_no_refresh",
    )
    require(
        not (REPO / "datasets/manifests/orderlab_v1.json").exists(),
        "dataset_already_initialized_no_refresh",
    )
    for name, content in generated_materials().items():
        path = FIXTURE / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    files = {name: sha256((FIXTURE / name).read_bytes()) for name in inventory(FIXTURE)}
    manifest = {
        "fixture_id": "orderlab",
        "fixture_version": "orderlab_v1",
        "synthetic": True,
        "symptom": "移动端 2.3 创建订单超时重试后，同一业务操作观察到两笔订单。",
        "checksum_algorithm": "SHA-256",
        "source_root": "source",
        "source_checksum": source_checksum(files),
        "source_checksum_rule": "sha256(sorted(path + NUL + file_sha256 + LF))",
        "files": files,
        "public_tests": [
            "tests/test_public_reproduction.py::test_timeout_retry_creates_one_order"
        ],
        "reproduction_guide": "README.md",
        "environment": {
            "python": "3.12",
            "dependency_source": "root_uv_lock",
            "dependencies": {
                name: version(name)
                for name in (
                    "fastapi",
                    "pydantic",
                    "starlette",
                    "httpx2",
                    "pytest",
                    "pytest-asyncio",
                )
            },
            "repository_checksums": {
                name: sha256((REPO / name).read_bytes())
                for name in ("pyproject.toml", "uv.lock", ".python-version")
            },
        },
    }
    (FIXTURE / "manifest.json").write_bytes(canonical_json(manifest))
    entries = []
    for path in sorted((REPO / "datasets/public").glob("*.json")):
        content = path.read_bytes()
        case = PublicCase.model_validate_json(content)
        entries.append(
            {
                **{
                    key: getattr(case, key)
                    for key in (
                        "case_id",
                        "dataset_version",
                        "task_family",
                        "problem_group",
                        "fixture_version",
                    )
                },
                "input_ref": "public/" + path.name,
                "input_sha256": sha256(content),
                "grader_status": "not_implemented",
                "policy_assertions": [],
            }
        )
    target = REPO / "datasets/manifests/orderlab_v1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        canonical_json(
            {
                "dataset_version": "v1",
                "synthetic": True,
                "visibility": "evaluation_metadata_only",
                "cases": entries,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--initialize", action="store_true", help="仅首次冻结；已有 manifest 时拒绝覆盖"
    )
    args = parser.parse_args()
    if args.initialize:
        initialize()
        print("Initialized synthetic orderlab_v1 / dataset v1")
    else:
        print(
            json.dumps(
                {"status": "verified", "generated_files_match": check_generated()}
            )
        )


if __name__ == "__main__":
    main()
