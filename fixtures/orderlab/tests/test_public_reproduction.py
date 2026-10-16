"""synthetic 公开业务要求；故障快照应因实际订单数量断言失败。"""

import json
from collections.abc import Callable

from scenario import replay


async def test_timeout_retry_creates_one_order(
    record_property: Callable[..., None],
) -> None:
    observation = await replay()
    events = observation["client_events"]
    assert observation["initial_orders"] == []
    assert [event["event"] for event in events] == [
        "request_sent",
        "response_timeout",
        "request_sent",
        "response_received",
    ]
    record_property("orderlab_observation", json.dumps(observation, sort_keys=True))
    orders = observation["orders"]
    assert len(orders) == 1, (
        "ORDERLAB_SINGLE_OPERATION: timeout retry must preserve one order; "
        f"observed={json.dumps(orders, sort_keys=True)}"
    )
