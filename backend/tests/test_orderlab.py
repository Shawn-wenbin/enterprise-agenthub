"""素材/输入边界与失败分类回归；不执行有意失败的公开业务测试。"""

import json
import shutil
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest
from pydantic import ValidationError

from agenthub.evaluation.reproduction import TARGET, classify_reproduction
from agenthub.evaluation.schemas import PublicCase
from agenthub.evaluation.validation import (
    check_public_text,
    inventory,
    safe_file,
    sha256,
    source_checksum,
    verify_dataset,
    verify_fixture,
    verify_materials,
)

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def copied_repo(tmp_path: Path) -> Path:
    for name in ("fixtures", "datasets"):
        shutil.copytree(
            REPO / name,
            tmp_path / name,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
        )
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        shutil.copyfile(REPO / name, tmp_path / name)
    return tmp_path


def test_static_contracts_are_readonly() -> None:
    root = REPO / "fixtures/orderlab"
    before = {name: (root / name).read_bytes() for name in inventory(root)}
    manifest = verify_fixture(REPO)
    cases = verify_dataset(REPO, manifest)
    assert len(cases) == 4
    assert cases[0].problem_group == cases[2].problem_group
    assert verify_materials(REPO) == {
        "client_events": 4,
        "server_events": 4,
        "tickets": 2,
    }
    assert before == {name: (root / name).read_bytes() for name in inventory(root)}


@pytest.mark.parametrize(
    "name",
    [
        "source/orderlab.py",
        "logs/server_events.jsonl",
        "specs/order_api.md",
        "specs/openapi.json",
    ],
)
def test_tamper_is_rejected(copied_repo: Path, name: str) -> None:
    path = copied_repo / "fixtures/orderlab" / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="checksum_mismatch"):
        verify_fixture(copied_repo)


def test_undeclared_python_is_never_executed(copied_repo: Path) -> None:
    marker = copied_repo / "executed"
    code = copied_repo / "fixtures/orderlab/source/extra.py"
    code.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(ValueError, match="inventory_mismatch"):
        verify_fixture(copied_repo)
    assert not marker.exists()


def test_static_scan_does_not_import_declared_python(copied_repo: Path) -> None:
    root = copied_repo / "fixtures/orderlab"
    marker = copied_repo / "executed"
    source = root / "source/orderlab.py"
    source.write_text(
        f'"""synthetic: 仅用于隔离副本验证扫描不执行代码。"""\n'
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
        "raise RuntimeError('must never be executed during scanning')\n"
    )
    path = root / "manifest.json"
    data = json.loads(path.read_bytes())
    data["files"]["source/orderlab.py"] = sha256(source.read_bytes())
    data["source_checksum"] = source_checksum(data["files"])
    path.write_text(json.dumps(data))
    verify_fixture(copied_repo)
    assert not marker.exists()


def test_fixture_manifest_sensitive_marker_rejected(copied_repo: Path) -> None:
    path = copied_repo / "fixtures/orderlab/manifest.json"
    data = json.loads(path.read_bytes())
    data["symptom"] = "expected_root_cause: forbidden"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="public_sensitive_marker"):
        verify_fixture(copied_repo)


@pytest.mark.parametrize(
    "ref",
    [
        "../datasets/private/answer.json",
        "/tmp/answer",
        "source/../../answer",
        "source//a",
        "source/./a",
        "source\\a",
        "file://answer",
        "%2e%2e/answer",
    ],
)
def test_unsafe_paths(ref: str) -> None:
    with pytest.raises(ValueError, match="unsafe_relative_path"):
        safe_file(REPO / "fixtures/orderlab", ref)


def test_symlink_and_symlink_parent_rejected(copied_repo: Path, tmp_path: Path) -> None:
    root = copied_repo / "fixtures/orderlab"
    link = root / "source/linked.py"
    link.symlink_to(REPO / "fixtures/orderlab/source/orderlab.py")
    with pytest.raises(ValueError, match="symlink_rejected"):
        verify_fixture(copied_repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "fixtures").symlink_to(
        copied_repo / "fixtures", target_is_directory=True
    )
    with pytest.raises(ValueError, match="symlink_rejected"):
        verify_fixture(outside)


def test_source_checksum_is_sorted_and_scoped() -> None:
    first = {"source/b.py": "b" * 64, "source/a.py": "a" * 64, "specs/a.md": "c" * 64}
    second = dict(reversed(list(first.items())))
    assert source_checksum(first) == source_checksum(second)
    second["specs/a.md"] = "d" * 64
    assert source_checksum(first) == source_checksum(second)
    second["source/a.py"] = "e" * 64
    assert source_checksum(first) != source_checksum(second)


@pytest.mark.parametrize(
    "field",
    [
        "expected_root_cause",
        "expected_evidence",
        "reference_patch",
        "hidden_tests",
        "rubric",
        "tenant_id",
        "role",
    ],
)
def test_public_schema_rejects_extra_fields(field: str) -> None:
    data = json.loads((REPO / "datasets/public/order_retry_001.json").read_bytes())
    data[field] = "untrusted"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PublicCase.model_validate(data)


@pytest.mark.parametrize("field", ["query", "task_family"])
def test_empty_case_fields_rejected(field: str) -> None:
    data = json.loads((REPO / "datasets/public/order_retry_001.json").read_bytes())
    data[field] = ""
    with pytest.raises(ValidationError):
        PublicCase.model_validate(data)


@pytest.mark.parametrize(
    "text",
    [
        "expected_evidence: []",
        "reference_patch",
        "hidden-tests",
        '"api_key": "fake"',
        "-----BEGIN RSA PRIVATE KEY-----",
    ],
)
def test_sensitive_markers_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        check_public_text(text)


def test_duplicate_case_rejected(copied_repo: Path) -> None:
    path = copied_repo / "datasets/manifests/orderlab_v1.json"
    data = json.loads(path.read_bytes())
    data["cases"][1] = data["cases"][0]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate_case"):
        verify_dataset(copied_repo, verify_fixture(copied_repo))


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_version",
        "outside_source",
        "input_escape",
        "stale_input_hash",
        "unimplemented_grader_ref",
    ],
)
def test_dataset_consistency_rejects_invalid_references(
    copied_repo: Path, mutation: str
) -> None:
    manifest_path = copied_repo / "datasets/manifests/orderlab_v1.json"
    manifest = json.loads(manifest_path.read_bytes())
    entry = manifest["cases"][0]
    path = copied_repo / "datasets" / entry["input_ref"]
    data = json.loads(path.read_bytes())
    if mutation == "unknown_version":
        data["fixture_version"] = entry["fixture_version"] = "orderlab_missing"
    elif mutation == "outside_source":
        data["allowed_sources"] = ["datasets/private/answer.json"]
    elif mutation == "input_escape":
        entry["input_ref"] = "../answer.json"
    elif mutation == "unimplemented_grader_ref":
        entry["grader_ref"] = "private/missing"
    else:
        data["query"] += " 未发布变更"
    path.write_text(json.dumps(data, ensure_ascii=False))
    if mutation != "stale_input_hash":
        entry["input_sha256"] = sha256(path.read_bytes())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, ValidationError)):
        verify_dataset(copied_repo, verify_fixture(copied_repo))


def test_dependency_lock_drift_rejected(copied_repo: Path) -> None:
    path = copied_repo / "uv.lock"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="dependency_checksum_mismatch"):
        verify_fixture(copied_repo)


def test_invalid_public_test_reference_rejected(copied_repo: Path) -> None:
    path = copied_repo / "fixtures/orderlab/manifest.json"
    data = json.loads(path.read_bytes())
    data["public_tests"] = ["source/orderlab.py::test_not_present"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="missing_test_node"):
        verify_fixture(copied_repo)


def test_timeline_mismatch_rejected(copied_repo: Path) -> None:
    path = copied_repo / "fixtures/orderlab/logs/server_events.jsonl"
    lines = path.read_text().splitlines()
    row = json.loads(lines[0])
    row["request_id"] = "unmatched_request"
    lines[0] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="request_log_mismatch"):
        verify_materials(copied_repo)


def junit_result() -> ET.Element:
    # 分类器单测只构造输入格式；真实业务验证由独立 subprocess 执行，不能由本测试代替。
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    test = ET.SubElement(
        suite, "testcase", name=TARGET, classname="test_public_reproduction"
    )
    client = [
        json.loads(line)
        for line in (REPO / "fixtures/orderlab/requests/client_requests.jsonl")
        .read_text()
        .splitlines()
    ]
    server = [
        json.loads(line)
        for line in (REPO / "fixtures/orderlab/logs/server_events.jsonl")
        .read_text()
        .splitlines()
    ]
    observation = {
        "synthetic": True,
        "fixture_version": "orderlab_v1",
        "initial_orders": [],
        "client_events": client,
        "server_events": server,
        "orders": [
            {"order_id": "ord_0001", "operation_id": "checkout_001"},
            {"order_id": "ord_0002", "operation_id": "checkout_001"},
        ],
    }
    properties = ET.SubElement(test, "properties")
    ET.SubElement(
        properties,
        "property",
        name="orderlab_observation",
        value=json.dumps(observation),
    )
    failure = ET.SubElement(
        test,
        "failure",
        message=(
            "AssertionError: ORDERLAB_SINGLE_OPERATION: observed duplicates\n"
            "assert 2 == 1"
        ),
    )
    failure.text = "assert len(orders) == 1"
    return root


def test_classifier_recognizes_target_assertion() -> None:
    result = classify_reproduction(1, ET.tostring(junit_result()))
    assert result["status"] == "expected_reproduction_failure"


@pytest.mark.parametrize(
    "mutation",
    [
        "import_error",
        "non_target_assertion",
        "missing_observation",
        "wrong_test",
        "skip",
        "extra_test",
        "only_one_order",
    ],
)
def test_classifier_rejects_non_target_failures(mutation: str) -> None:
    root = junit_result()
    test = root.find(".//testcase")
    assert test is not None
    if mutation == "import_error":
        ET.SubElement(test, "error", message="ImportError")
    elif mutation == "non_target_assertion":
        failure = test.find("failure")
        assert failure is not None
        failure.set("message", "AssertionError: another requirement")
    elif mutation == "missing_observation":
        properties = test.find("properties")
        assert properties is not None
        test.remove(properties)
    elif mutation == "wrong_test":
        test.set("name", "another_test")
    elif mutation == "skip":
        ET.SubElement(test, "skipped")
    elif mutation == "extra_test":
        ET.SubElement(root, "testcase", name="extra_test")
    else:
        prop = test.find(".//property")
        assert prop is not None
        observation: dict[str, Any] = json.loads(prop.get("value", ""))
        observation["orders"] = observation["orders"][:1]
        prop.set("value", json.dumps(observation))
    with pytest.raises(ValueError):
        classify_reproduction(1, ET.tostring(root))


@pytest.mark.parametrize("exit_code", [0, 2, 3, 4, 5, -9])
def test_classifier_rejects_wrong_process_exit(exit_code: int) -> None:
    with pytest.raises(ValueError, match="unexpected_pytest_exit_code"):
        classify_reproduction(exit_code, ET.tostring(junit_result()))


def test_orderlab_not_registered_in_platform() -> None:
    from agenthub.main import create_app

    app = create_app()
    assert not any(
        getattr(route, "path", "").startswith("/orders") for route in app.routes
    )
