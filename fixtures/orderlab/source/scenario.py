"""synthetic 固定请求配方；只有显式测试/生成命令才导入执行。"""

import httpx2 as httpx
from orderlab import Journal, create_app

BODY = {
    "operation_id": "checkout_001",
    "sku": "sku_demo_book",
    "quantity": 1,
    "currency": "CNY",
}


class LoseFirstResponse(httpx.AsyncBaseTransport):
    def __init__(self, journal: Journal) -> None:
        self.inner = httpx.ASGITransport(app=create_app(journal))
        self.armed = True

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if self.armed and request.method == "POST" and request.url.path == "/orders":
            # 等真实应用完成写入，再丢弃第一次响应；不模拟数据库竞态或真实网络延迟。
            if response.status_code != 201:
                raise RuntimeError("First request did not reach committed order state")
            self.armed = False
            await response.aclose()
            raise httpx.ReadTimeout(
                "synthetic response loss after completion", request=request
            )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


async def replay() -> dict[str, object]:
    journal = Journal()
    client_events: list[dict[str, object]] = []
    async with httpx.AsyncClient(
        transport=LoseFirstResponse(journal), base_url="http://orderlab.test"
    ) as client:
        initial = await client.get("/orders", params={"operation_id": "checkout_001"})
        if initial.status_code != 200 or initial.json() != []:
            raise RuntimeError("Initial order state is not empty")
        for attempt in (1, 2):
            context = {
                "request_id": f"mobile23_req_{attempt}",
                "operation_id": BODY["operation_id"],
                "mobile_version": "2.3",
                "method": "POST",
                "path": "/orders",
                "attempt": attempt,
            }
            client_events.append(journal.event("request_sent", **context, body=BODY))
            try:
                response = await client.post(
                    "/orders",
                    json=BODY,
                    headers={
                        "X-Request-ID": str(context["request_id"]),
                        "X-Mobile-Version": "2.3",
                    },
                )
            except httpx.ReadTimeout:
                client_events.append(journal.event("response_timeout", **context))
            else:
                if response.status_code != 201:
                    raise RuntimeError("Unexpected response to retry")
                client_events.append(
                    journal.event(
                        "response_received",
                        **context,
                        status_code=response.status_code,
                        order_id=response.json()["order_id"],
                    )
                )
        response = await client.get("/orders", params={"operation_id": "checkout_001"})
        if response.status_code != 200:
            raise RuntimeError("Order state cannot be queried")
        orders = response.json()
    return {
        "synthetic": True,
        "fixture_version": "orderlab_v1",
        "initial_orders": [],
        "client_events": client_events,
        "server_events": journal.server_events,
        "orders": orders,
    }


async def schema_probe() -> list[dict[str, object]]:
    journal = Journal()
    rows = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(journal)),
        base_url="http://orderlab.test",
    ) as client:
        for version, extra in (
            ("2.2", {}),
            ("2.3", {"delivery_note": "synthetic demo"}),
        ):
            body = {**BODY, "operation_id": "checkout_schema", **extra}
            response = await client.post(
                "/orders",
                json=body,
                headers={
                    "X-Request-ID": f"schema_{version.replace('.', '')}",
                    "X-Mobile-Version": version,
                },
            )
            rows.append(
                journal.event(
                    "schema_probe",
                    mobile_version=version,
                    request_id=f"schema_{version.replace('.', '')}",
                    operation_id=body["operation_id"],
                    method="POST",
                    path="/orders",
                    body=body,
                    status_code=response.status_code,
                    response=response.json(),
                )
            )
    return rows


def uncertain_request() -> dict[str, object]:
    return Journal().event(
        "response_timeout",
        request_id="uncertain_req_1",
        operation_id="checkout_unobserved",
        mobile_version="2.3",
        method="POST",
        path="/orders",
        body={**BODY, "operation_id": "checkout_unobserved"},
        capture_scope="client_only",
        server_observation="not_available",
    )
