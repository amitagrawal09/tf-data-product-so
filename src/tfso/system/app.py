"""System API (Tier 2, API-led 'System' layer): unlocks D365, WMS, TMS, CRM and the Lakehouse."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import Body, FastAPI, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from ..common.bus import cloud_event, make_broker
from ..common.config import Settings, get_settings
from ..common.db import Database
from ..common.outbox import Outbox, make_models
from ..common.web import ApiError, correlation_id, create_app
from .d365_client import D365Client
from .erp import CrmActivity, LiveD365, SimulatedErp, SystemBase, translate_business_event
from .lakehouse import LakehouseWriter
from .masterdata import D365MasterData, SeedMasterData

log = logging.getLogger("tfso.system")
OutboxMessage, InboxMessage = make_models(SystemBase)


class AdvanceRequest(BaseModel):
    to: str
    lines: list[dict[str, Any]] | None = None
    carrier: dict[str, Any] | None = None


class SimConfig(BaseModel):
    autoProgress: bool | None = None
    stepSeconds: float | None = None
    creditCheck: bool | None = None
    inventoryCheck: bool | None = None


def create_system_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings("system")
    db = Database(settings.db_url, SystemBase.metadata)
    broker = make_broker(settings)
    outbox = Outbox(db, OutboxMessage, InboxMessage, broker)
    sim_config = {"autoProgress": settings.sim_autoprogress, "stepSeconds": settings.sim_step_seconds,
                  "creditCheck": True, "inventoryCheck": True}

    if settings.d365_mode == "live":
        client = D365Client(settings.d365_url, settings.d365_tenant_id, settings.d365_client_id,
                            settings.d365_client_secret, settings.d365_company)
        md: SeedMasterData = D365MasterData(client)
        erp: SimulatedErp = LiveD365(db, md, sim_config, client)
    else:
        md = SeedMasterData()
        erp = SimulatedErp(db, md, sim_config)
    lake = LakehouseWriter(settings)
    tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        outbox.start()
        await broker.start()
        yield
        for t in tasks:
            t.cancel()
        await outbox.stop()
        await broker.stop()

    app = create_app(settings, "Sales Order - System API",
                     "System APIs over D365 F&O, WMS, TMS, CRM and the Lakehouse (Tier 2, system layer). "
                     "Includes the fulfilment simulator used in non-production environments.")
    app.router.lifespan_context = lifespan
    app.state.db, app.state.outbox, app.state.erp, app.state.md, app.state.broker = db, outbox, erp, md, broker

    def emit(events: list[dict[str, Any]]) -> None:
        if not events:
            return
        with db.session() as s:
            for ev in events:
                outbox.add(s, "system-status-events",
                           cloud_event(f"tfso.system.{ev['eventType']}", f"/system/{ev['sourceSystem'].lower()}",
                                       ev.get("orderId"), ev, correlation_id(), ev["eventId"]))
        outbox.kick()

    async def autoprogress(order_id: str) -> None:
        try:
            for step in ("RELEASED", "PICKED", "PACKED", "SHIPPED", "DELIVERED", "INVOICED"):
                await asyncio.sleep(float(sim_config["stepSeconds"]))
                doc = erp.get(order_id=order_id)
                if not doc or doc["stage"] in ("CANCELLED", "CREDIT_HOLD"):
                    return
                try:
                    emit(await erp.advance(order_id, step))
                except ValueError:
                    continue
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------ bus consumers
    async def on_command(envelope: dict[str, Any]) -> None:
        if outbox.processed(envelope["id"]):
            return
        data, ctype = envelope["data"], envelope["type"]
        if ctype.endswith("CreateErpSalesOrder"):
            events = await erp.create_sales_order(data["order"])
            emit(events)
            if sim_config["autoProgress"] and erp.mode == "simulator" and events and events[0]["eventType"] == "ERP_ACCEPTED":
                t = asyncio.create_task(autoprogress(data["order"]["orderId"]))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
        elif ctype.endswith("CancelErpSalesOrder"):
            emit(await erp.cancel(data["orderId"], data.get("reason")))
        outbox.mark_processed(envelope["id"], "sales-order-commands")

    async def on_order_event_lakehouse(envelope: dict[str, Any]) -> None:
        lake.write("sales-order-events", envelope)

    async def on_order_event_crm(envelope: dict[str, Any]) -> None:
        d = envelope.get("data", {})
        if d.get("statusCategory") in ("ACTION_REQUIRED",) or d.get("eventType") in (
                "ERP_ACCEPTED", "SHIPPED", "DELIVERED", "INVOICED", "CANCELLED"):
            if outbox.processed(f"crm:{envelope['id']}"):
                return
            with db.session() as s:
                outbox.mark_processed(f"crm:{envelope['id']}", "sales-order-events", s)
                s.add(CrmActivity(order_id=d.get("orderId"), customer_id=d.get("customerId"),
                                  activity={"type": "ORDER_TIMELINE", "eventType": d.get("eventType"),
                                            "status": d.get("toStatus"), "category": d.get("statusCategory"),
                                            "at": d.get("occurredAt")}))

    broker.subscribe("sales-order-commands", "system-api", on_command)
    broker.subscribe("sales-order-events", "system-lakehouse", on_order_event_lakehouse)
    broker.subscribe("sales-order-events", "system-crm", on_order_event_crm)

    @app.post("/v1/bus/{topic}", tags=["bus"], summary="Local HTTP bus delivery (mirrors Service Bus)")
    async def bus_delivery(topic: str, envelope: dict[str, Any] = Body(...)):
        count = await broker.deliver_local(topic, envelope)
        return {"delivered": count}

    # ------------------------------------------------------------------ health
    @app.get("/health", tags=["ops"])
    async def health():
        return {"status": "ok", "service": "system-api", "env": settings.env, "d365Mode": erp.mode,
                "masterDataMode": md.mode, "lakehouseMode": lake.mode, "busMode": settings.bus_mode,
                "db": db.ping(), "outbox": outbox.stats(), "simulator": sim_config}

    # ------------------------------------------------------------------ master data
    @app.get("/v1/masterdata/customers", tags=["master data"])
    async def customers(active: bool | None = None):
        return {"items": await md.list_customers(active)}

    @app.get("/v1/masterdata/customers/{customer_id}", tags=["master data"])
    async def customer(customer_id: str):
        c = await md.get_customer(customer_id)
        if not c:
            raise ApiError(404, "Customer not found", customer_id)
        return c

    @app.get("/v1/masterdata/customers/{customer_id}/ship-tos", tags=["master data"])
    async def ship_tos(customer_id: str):
        return {"items": await md.ship_tos_for(customer_id)}

    @app.get("/v1/masterdata/ship-tos/{ship_to_id}", tags=["master data"])
    async def ship_to(ship_to_id: str):
        st = await md.get_ship_to(ship_to_id)
        if not st:
            raise ApiError(404, "Ship-to not found", ship_to_id)
        return st

    @app.get("/v1/masterdata/ship-tos", tags=["master data"])
    async def find_ship_to(customerId: str | None = None, ediLocationCode: str | None = None,
                           gln: str | None = None, name: str | None = None):
        st = await md.find_ship_to(customerId, edi_code=ediLocationCode, gln=gln, name=name)
        return {"items": [st] if st else []}

    @app.get("/v1/masterdata/items", tags=["master data"])
    async def items(active: bool | None = None):
        return {"items": await md.list_items(active)}

    @app.get("/v1/masterdata/items/{item_number}", tags=["master data"])
    async def item(item_number: str):
        i = await md.get_item(item_number)
        if not i:
            raise ApiError(404, "Item not found", item_number)
        return {**i, "available": md.inventory.get(item_number)}

    @app.get("/v1/masterdata/item-xref", tags=["master data"])
    async def item_xref(customerId: str | None = None, buyerItem: str | None = None, gtin: str | None = None,
                        upc: str | None = None, vendorItem: str | None = None):
        if any((buyerItem, gtin, upc, vendorItem)):
            i = await md.resolve_item(customerId, buyer_item=buyerItem, gtin=gtin, upc=upc, vendor_item=vendorItem)
            return {"items": [i] if i else []}
        return {"items": await md.item_xref(customerId)}

    @app.get("/v1/masterdata/price-agreements", tags=["master data"])
    async def price_agreements(customerId: str | None = None, itemNumber: str | None = None,
                               on: date | None = None):
        return {"items": await md.price_agreements(customerId, itemNumber, on)}

    @app.get("/v1/masterdata/trading-partners/{isa_id}", tags=["master data"])
    async def trading_partner(isa_id: str):
        p = await md.get_partner(isa_id)
        if not p:
            raise ApiError(404, "Trading partner not found", isa_id)
        return p

    # ------------------------------------------------------------------ D365
    @app.post("/v1/d365/sales-orders", tags=["d365"], status_code=201,
              summary="Create a D365 sales order synchronously (normally driven by the command topic)")
    async def create_so(order: dict[str, Any] = Body(...)):
        events = await erp.create_sales_order(order)
        emit(events)
        return {"events": events}

    @app.get("/v1/d365/sales-orders/{erp_id}", tags=["d365"])
    async def get_so(erp_id: str):
        doc = erp.get(erp_id=erp_id)
        if not doc:
            raise ApiError(404, "Sales order not found", erp_id)
        return doc

    @app.get("/v1/d365/sales-orders", tags=["d365"])
    async def find_so(orderId: str = Query(...)):
        doc = erp.get(order_id=orderId)
        if not doc:
            raise ApiError(404, "Sales order not found", orderId)
        return doc

    @app.post("/v1/d365/business-events", tags=["d365"], status_code=202,
              summary="D365 business events webhook (Business events → HTTPS endpoint / Event Grid)")
    async def business_events(payload: Any = Body(...)):
        evts = payload if isinstance(payload, list) else [payload]
        out = [e for e in (translate_business_event(x) for x in evts) if e]
        emit(out)
        return {"accepted": len(out), "ignored": len(evts) - len(out)}

    @app.post("/v1/wms/events", tags=["wms"], status_code=202, summary="WMS pick/pack/ship confirmations")
    async def wms_events(event: dict[str, Any] = Body(...)):
        return _external_event(event, "WMS", {"PICKED", "PACKED", "SHIPPED"})

    @app.post("/v1/tms/events", tags=["tms"], status_code=202, summary="TMS delivery / proof of delivery")
    async def tms_events(event: dict[str, Any] = Body(...)):
        return _external_event(event, "TMS", {"DELIVERED"})

    def _external_event(event: dict, source: str, allowed: set[str]) -> dict:
        if event.get("eventType") not in allowed:
            raise ApiError(422, "Unsupported event type", f"{source} supports {sorted(allowed)}")
        if not event.get("orderId") and not event.get("erpSalesOrderId"):
            raise ApiError(422, "Missing order reference", "orderId or erpSalesOrderId is required")
        from .erp import status_event
        ev = status_event(event["eventType"], source, event.get("orderId"), event.get("erpSalesOrderId"))
        ev.update({k: v for k, v in event.items() if k in ("eventId", "occurredAt", "shipment", "lines", "reason")})
        emit([ev])
        return {"accepted": 1, "eventId": ev["eventId"]}

    # ------------------------------------------------------------------ simulator (non-prod)
    def _sim_guard():
        if settings.is_prod:
            raise ApiError(403, "Simulator disabled", "Simulator endpoints are not available in production")

    @app.post("/v1/sim/orders/{order_id}/advance", tags=["simulator"])
    async def sim_advance(order_id: str, req: AdvanceRequest):
        _sim_guard()
        try:
            events = await erp.advance(order_id, req.to, req.lines, req.carrier)
        except LookupError:
            raise ApiError(404, "Order not in D365", f"{order_id} has not been created in D365 yet")
        except ValueError as e:
            raise ApiError(409, "Invalid simulator transition", str(e))
        emit(events)
        return {"orderId": order_id, "emitted": [e["eventType"] for e in events], "events": events}

    @app.post("/v1/sim/orders/{order_id}/release-credit-hold", tags=["simulator"])
    async def sim_release(order_id: str):
        _sim_guard()
        try:
            events = await erp.release_credit_hold(order_id)
        except LookupError:
            raise ApiError(404, "Order not in D365", order_id)
        except ValueError as e:
            raise ApiError(409, "Not on credit hold", str(e))
        emit(events)
        return {"orderId": order_id, "emitted": [e["eventType"] for e in events]}

    @app.get("/v1/sim/config", tags=["simulator"])
    async def get_sim_config():
        return sim_config

    @app.put("/v1/sim/config", tags=["simulator"])
    async def put_sim_config(cfg: SimConfig):
        _sim_guard()
        sim_config.update({k: v for k, v in cfg.model_dump().items() if v is not None})
        return sim_config

    # ------------------------------------------------------------------ lakehouse / crm
    @app.get("/v1/lakehouse/files", tags=["lakehouse"])
    async def lake_files():
        return {"mode": lake.mode, "items": lake.list_files()}

    @app.get("/v1/crm/orders/{order_id}/activities", tags=["crm"])
    async def crm_activities(order_id: str):
        with db.session() as s:
            rows = s.execute(select(CrmActivity).where(CrmActivity.order_id == order_id)
                             .order_by(CrmActivity.id)).scalars().all()
            return {"items": [{**r.activity, "createdAt": r.created_at.isoformat()} for r in rows]}

    return app


def __getattr__(name: str):  # lazy module attribute so importing the package has no side effects
    if name == "app":
        globals()["app"] = create_system_app()
        return globals()["app"]
    raise AttributeError(name)
