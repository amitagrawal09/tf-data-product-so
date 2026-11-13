"""In-process harness: Experience, Process and System APIs wired together with the in-proc bus.

`pump()` plays the role of the outbox dispatchers + Service Bus: it publishes pending outbox rows from
both services and delivers them to subscribers until the system is quiescent.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

import httpx
import pytest

from tfso.common.bus import InProcBroker
from tfso.common.config import get_settings
from tfso.common.web import register_transport

PROCESS_URL, SYSTEM_URL, EXP_URL = "http://process.test", "http://system.test", "http://exp.test"
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def delivery(days_ok=("MON", "TUE", "WED", "THU", "FRI"), lead: int = 3) -> str:
    d = date.today() + timedelta(days=lead)
    while DAYS[d.weekday()] not in days_ok:
        d += timedelta(days=1)
    return d.isoformat()


class Harness:
    def __init__(self, tmp_path):
        InProcBroker.reset()
        from tfso.experience.app import create_experience_app
        from tfso.process.app import create_process_app
        from tfso.system.app import create_system_app

        def s(service, **kw):
            base = get_settings(service)
            return dataclasses.replace(base, bus_mode="inproc", auth_mode="apikey", api_keys=("test-key",),
                                       process_url=PROCESS_URL, system_url=SYSTEM_URL, eventhub_fqdn=None,
                                       d365_mode="simulator", lakehouse_mode="file", env="test",
                                       lakehouse_local_path=str(tmp_path / "lake"),
                                       db_url=f"sqlite:///{tmp_path}/{service}.db", **kw)

        self.system_app = create_system_app(s("system"))
        register_transport(SYSTEM_URL, httpx.ASGITransport(app=self.system_app))
        self.process_app = create_process_app(s("process"))
        register_transport(PROCESS_URL, httpx.ASGITransport(app=self.process_app))
        self.exp_app = create_experience_app(s("experience"))
        h = {"x-api-key": "test-key"}
        self.exp = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.exp_app), base_url=EXP_URL, headers=h)
        self.process = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.process_app), base_url=PROCESS_URL, headers=h)
        self.system = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.system_app), base_url=SYSTEM_URL, headers=h)

    async def pump(self, rounds: int = 50) -> None:
        for _ in range(rounds):
            sent = await self.process_app.state.outbox.dispatch_once()
            sent += await self.system_app.state.outbox.dispatch_once()
            delivered = await InProcBroker.drain()
            if not sent and not delivered:
                return

    async def portal(self, **overrides) -> dict:
        body = {"channel": "PORTAL", "customerId": "C-1001", "shipToId": "ST-1001-01",
                "customerPo": overrides.pop("customerPo", f"PO-{id(overrides)}"),
                "requestedDeliveryDate": delivery(("MON", "TUE", "WED", "THU", "FRI", "SAT")),
                "contact": {"name": "Pat", "email": "pat@freshmart.example"},
                "lines": [{"itemNumber": "TF-10001", "quantity": 10, "uom": "CA"}]}
        body.update(overrides)
        r = await self.exp.post("/portal/v1/orders", json=body)
        assert r.status_code == 202, r.text
        await self.pump()
        return r.json()

    async def status(self, order_id: str) -> dict:
        r = await self.process.get(f"/v1/orders/{order_id}/status")
        assert r.status_code == 200, r.text
        return r.json()

    async def advance(self, order_id: str, to: str, **kw) -> dict:
        r = await self.system.post(f"/v1/sim/orders/{order_id}/advance", json={"to": to, **kw})
        assert r.status_code == 200, r.text
        await self.pump()
        return r.json()

    async def history(self, order_id: str) -> list[dict]:
        return (await self.process.get(f"/v1/orders/{order_id}/history")).json()["items"]

    async def close(self):
        for c in (self.exp, self.process, self.system):
            await c.aclose()


@pytest.fixture
async def h(tmp_path):
    harness = Harness(tmp_path)
    yield harness
    await harness.close()
