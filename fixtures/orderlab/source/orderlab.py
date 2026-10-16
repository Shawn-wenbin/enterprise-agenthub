"""synthetic OrderLab 0.3.0 快照；仅供显式本地实验，不连接平台数据库。"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

FIXTURE_VERSION = "orderlab_v1"
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]


class CreateOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: Identifier
    sku: Identifier
    quantity: int = Field(ge=1, le=20)
    currency: Literal["CNY"]


class Order(CreateOrder):
    order_id: str
    created_at: datetime


class Journal:
    def __init__(self) -> None:
        self.sequence = 0
        self.server_events: list[dict[str, object]] = []

    def event(self, event: str, **fields: object) -> dict[str, object]:
        # 使用逻辑时钟生成固定素材，时间不表示真实生产采集时间。
        timestamp = datetime(2026, 10, 1, 8, tzinfo=UTC) + timedelta(
            seconds=self.sequence
        )
        self.sequence += 1
        return {
            "synthetic": True,
            "fixture_version": FIXTURE_VERSION,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "event": event,
            **fields,
        }


class OrderService:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.orders: dict[str, Order] = {}
        self.request_orders: dict[str, str] = {}

    def create(self, body: CreateOrder, request_id: str) -> Order:
        if request_id in self.request_orders:
            order = self.orders[self.request_orders[request_id]]
            if (
                body.model_dump()
                != CreateOrder.model_validate(
                    order.model_dump(include=set(CreateOrder.model_fields))
                ).model_dump()
            ):
                raise HTTPException(409, detail="request_payload_conflict")
            return order
        order_id = f"ord_{len(self.orders) + 1:04d}"
        order = Order(
            **body.model_dump(),
            order_id=order_id,
            created_at=datetime(2026, 10, 1, 8, tzinfo=UTC)
            + timedelta(seconds=self.journal.sequence),
        )
        self.orders[order_id] = order
        self.request_orders[request_id] = order_id
        return order


def create_app(journal: Journal | None = None) -> FastAPI:
    journal = journal if journal is not None else Journal()
    service = OrderService(journal)
    app = FastAPI(
        title="OrderLab (synthetic)",
        version="0.3.0",
        description="Synthetic order API snapshot; explicit local experiments only.",
    )
    app.state.order_service = service
    app.state.journal = journal

    @app.post(
        "/orders",
        response_model=Order,
        status_code=201,
        responses={409: {"description": "Request payload conflict"}},
    )
    async def create_order(
        body: CreateOrder,
        x_request_id: Annotated[Identifier, Header()],
        x_mobile_version: Annotated[Literal["2.2", "2.3"], Header()],
    ) -> Order:
        context = {
            "service": "order-api",
            "api_version": "0.3.0",
            "request_id": x_request_id,
            "operation_id": body.operation_id,
            "mobile_version": x_mobile_version,
            "method": "POST",
            "path": "/orders",
        }
        journal.server_events.append(
            journal.event("request_received", **context, body=body.model_dump())
        )
        order = service.create(body, x_request_id)
        journal.server_events.append(
            journal.event(
                "order_persisted", **context, order_id=order.order_id, status_code=201
            )
        )
        return order

    @app.get("/orders", response_model=list[Order])
    async def list_orders(operation_id: Annotated[Identifier, Query()]) -> list[Order]:
        return [
            order
            for order in service.orders.values()
            if order.operation_id == operation_id
        ]

    return app
