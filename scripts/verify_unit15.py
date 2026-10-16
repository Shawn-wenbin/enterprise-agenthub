"""Unit 1.5 显式验证入口；固定可信快照，本地实验不代表安全沙箱或模型质量。"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from orderlab_assets import REPO, check_generated

from agenthub.evaluation.reproduction import TARGET, classify_reproduction
from agenthub.evaluation.validation import (
    require,
    verify_dataset,
    verify_fixture,
    verify_materials,
)


def static_validation(repo: Path) -> dict[str, object]:
    fixture = verify_fixture(repo)
    cases = verify_dataset(repo, fixture)
    return {
        "status": "verified",
        "fixture_version": fixture.fixture_version,
        "dataset_version": "v1",
        "public_cases": [case.case_id for case in cases],
        "source_checksum": fixture.source_checksum,
        "materials": verify_materials(repo),
        "execution": "not_executed",
        "grader": "not_implemented",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--static-only", action="store_true", help="只读扫描，不导入/执行 fixture"
    )
    args = parser.parse_args()
    static = static_validation(REPO)
    if args.static_only:
        print(json.dumps(static, ensure_ascii=False, indent=2))
        return
    directory = REPO / ".data/unit15-verification" / uuid4().hex
    directory.mkdir(parents=True)
    report: dict[str, object] = {"status": "in_progress", "static": static}
    commands = []
    report["commands"] = commands

    def run(name: str, target: str) -> tuple[int, bytes]:
        junit = directory / f"{name}.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            target,
            "-q",
            "-o",
            "junit_family=xunit1",
            f"--junitxml={junit}",
        ]
        # 禁止外部 pytest 参数改变收集范围/断言行为；仍使用根项目锁定的 pytest 配置。
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTEST_ADDOPTS", "PYTHONPATH", "PYTHONOPTIMIZE"}
        }
        try:
            result = subprocess.run(
                command, cwd=REPO, env=env, capture_output=True, timeout=60
            )
        except subprocess.TimeoutExpired as exc:
            (directory / f"{name}.stdout").write_bytes(exc.stdout or b"")
            (directory / f"{name}.stderr").write_bytes(exc.stderr or b"")
            commands.append(
                {"argv": command, "exit_code": None, "status": "environment_timeout"}
            )
            raise RuntimeError(
                "Public experiment exceeded local execution timeout"
            ) from exc
        (directory / f"{name}.stdout").write_bytes(result.stdout)
        (directory / f"{name}.stderr").write_bytes(result.stderr)
        commands.append({"argv": command, "exit_code": result.returncode})
        require(junit.is_file(), "pytest_did_not_produce_junit")
        return result.returncode, junit.read_bytes()

    try:
        code, _ = run("boundary-tests", "backend/tests/test_orderlab.py")
        require(code == 0, "boundary_tests_failed")
        report["boundary_tests"] = "passed"
        report["generated_materials"] = check_generated()
        code, _ = run("controls", "fixtures/orderlab/tests/test_controls.py")
        require(code == 0, "positive_controls_failed")
        report["positive_controls"] = "passed"
        reproductions = []
        report["reproductions"] = reproductions
        for index in range(3):
            code, junit = run(
                f"reproduction-{index + 1}",
                f"fixtures/orderlab/tests/test_public_reproduction.py::{TARGET}",
            )
            reproductions.append(classify_reproduction(code, junit))
        require(
            all(item == reproductions[0] for item in reproductions),
            "non_deterministic_observation",
        )
        report["deterministic_repetitions"] = 3
        report["status"] = "verified"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (directory / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print("Unit 1.5 result:", directory.relative_to(REPO) / "result.json")
    print(
        "verified: static contracts, generated materials, controls; "
        "3 expected_reproduction_failure; grader not implemented"
    )


if __name__ == "__main__":
    main()
