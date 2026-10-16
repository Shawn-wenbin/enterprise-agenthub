"""synthetic 最小正向对照；不修复故障版本。"""

import httpx2 as httpx
from orderlab import create_app
from scenario import BODY


async def test_single_create_and_independent_operations() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://orderlab.test"
    ) as client:
        for index in (1, 2):
            body = {**BODY, "operation_id": f"independent_{index}"}
            response = await client.post(
                "/orders",
                json=body,
                headers={"X-Request-ID": f"req_{index}", "X-Mobile-Version": "2.3"},
            )
            assert response.status_code == 201
            assert response.json()["operation_id"] == body["operation_id"]
            result = await client.get(
                "/orders", params={"operation_id": body["operation_id"]}
            )
            assert result.status_code == 200
            assert len(result.json()) == 1
        assert response.json()["order_id"] == "ord_0002"


async def test_invalid_payload_is_rejected_without_order() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://orderlab.test"
    ) as client:
        response = await client.post(
            "/orders",
            json={**BODY, "quantity": "1"},
            headers={"X-Request-ID": "invalid_req", "X-Mobile-Version": "2.3"},
        )
        assert response.status_code == 422
        result = await client.get(
            "/orders", params={"operation_id": BODY["operation_id"]}
        )
        assert result.json() == []
