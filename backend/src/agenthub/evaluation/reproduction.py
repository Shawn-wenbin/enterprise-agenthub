"""检查公开实验进程的实际业务断言；不执行代码，也不把任意非零退出视作成功。"""

import json
from typing import Any
from xml.etree import ElementTree

from agenthub.evaluation.validation import require

TARGET = "test_timeout_retry_creates_one_order"
ASSERTION = "ORDERLAB_SINGLE_OPERATION"


def classify_reproduction(exit_code: int, junit: bytes) -> dict[str, Any]:
    require(exit_code == 1, "unexpected_pytest_exit_code")
    root = ElementTree.fromstring(junit)
    tests = root.findall(".//testcase")
    require(len(tests) == 1 and tests[0].get("name") == TARGET, "wrong_test_collected")
    test = tests[0]
    require(
        test.get("classname", "").endswith("test_public_reproduction"),
        "wrong_test_module",
    )
    require(
        not root.findall(".//error") and not root.findall(".//skipped"),
        "environment_error_or_skip",
    )
    failures = root.findall(".//failure")
    require(len(failures) == 1, "missing_single_failure")
    failure = failures[0]
    # 锁定版本的 pytest JUnit 将异常类别写在 message 中，不提供 type 属性。
    require(
        failure.get("message", "").startswith(f"AssertionError: {ASSERTION}:"),
        "non_target_assertion",
    )
    require(
        "assert len(orders) == 1" in (failure.text or "")
        and "assert 2 == 1" in failure.get("message", ""),
        "non_target_assertion",
    )
    properties = test.findall("./properties/property")
    values = [
        p.get("value", "")
        for p in properties
        if p.get("name") == "orderlab_observation"
    ]
    require(len(values) == 1, "missing_observation")
    observation = json.loads(values[0])
    require(
        observation["synthetic"] is True
        and observation["fixture_version"] == "orderlab_v1",
        "wrong_observation_version",
    )
    require(observation["initial_orders"] == [], "non_empty_initial_state")
    client = observation["client_events"]
    server = observation["server_events"]
    require(
        [row["event"] for row in client]
        == ["request_sent", "response_timeout", "request_sent", "response_received"],
        "wrong_timeout_retry_sequence",
    )
    require(
        [row["event"] for row in server]
        == [
            "request_received",
            "order_persisted",
            "request_received",
            "order_persisted",
        ],
        "missing_actual_server_state",
    )
    orders = observation["orders"]
    require(
        len(orders) == 2 and len({r["order_id"] for r in orders}) == 2,
        "no_duplicate_orders_observed",
    )
    require(
        all(r["operation_id"] == "checkout_001" for r in orders),
        "different_business_operations",
    )
    require(
        [r["order_id"] for r in orders]
        == [r["order_id"] for r in server if r["event"] == "order_persisted"],
        "persisted_order_mismatch",
    )
    require(client[-1]["status_code"] == 201, "retry_did_not_succeed")
    return {"status": "expected_reproduction_failure", "observation": observation}
