"""Process API (Tier 2, API-led 'Process' layer): Order Intake, Fulfilment orchestration and the Order Status API."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from typing import Any, Literal

import httpx
from fastapi import Body, FastAPI, Header, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..common.bus import EventStream, make_broker
from ..common.config import Settings, get_settings
from ..common.db import Database
from ..common.outbox import Outbox
from ..common.statuses import reference_catalogue
from ..common.util import iso, parse_dt, utcnow
from ..common.web import ApiError, create_app
from . import views
from .masterdata import MasterDataClient
from .models import (DataAccessRequestRow, DataExportRow, InboxMessage, OutboxMessage, ProcessBase,
                     SubscriptionRow)
from .service import OrderService

log = logging.getLogger("tfso.process")

TAGS = [
    {"name": "order status", "description": "Order Status API - one status across every system and channel"},
    {"name": "order intake", "description": "Channel-neutral intake: validate, enrich, canonicalize, publish, submit"},
    {"name": "customer service", "description": "Exception handling, corrections, holds and cancellation"},
    {"name": "events", "description": "Status events from D365, WMS and TMS"},
    {"name": "webhooks", "description": "Push status notifications to subscribers"},
    {"name": "governance", "description": "Data marketplace backing store: access requests, export audit, usage"},
]


class Actor(BaseModel):
    type: Literal["SYSTEM", "USER", "CUSTOMER", "PARTNER"] = "SYSTEM"
    id: str
    name: str | None = None


class IntakeRequest(BaseModel):
    sourceChannel: Literal["EDI", "EMAIL", "PORTAL", "MANUAL", "PARTNER_API"]
    channelDetail: str | None = None
    receivedAt: datetime | None = None
    orderGroupId: str | None = None
    sourceDocument: dict[str, Any] | None = Field(None, description="documentType, reference, senderId, ISA/GS/ST control numbers, rawContent")
    extraction: dict[str, Any] | None = None
    submittedBy: Actor | None = None
    acknowledgments: list[dict[str, Any]] | None = Field(None, description="Already-sent channel acks, e.g. the 997")
    draft: dict[str, Any] = Field(description="Order draft in canonical (camelCase) shape; partial is fine")


class CorrectionRequest(BaseModel):
    changes: dict[str, Any]
    reason: str | None = None
    reasonCode: str | None = None
    actor: str | None = None


class ActionRequest(BaseModel):
    actor: str | None = None
    reason: str | None = None
    reasonCode: str | None = None
    comment: str | None = None


class AccessRequest(BaseModel):
    datasetId: str
    requester: str
    team: str | None = None
    purpose: str = Field(min_length=3)
    consumerSystem: str | None = None
    accessLevel: Literal["READ", "EXPORT", "SUBSCRIBE"] = "READ"
    autoApprove: bool = False


class AccessDecision(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "REVOKED"]
    decidedBy: str
    note: str | None = None


class ExportAudit(BaseModel):
    datasetId: str
    requester: str
    format: str
    filters: dict[str, Any] = Field(default_factory=dict)
    fields: list[str] = Field(default_factory=list)
    rowCount: int = 0
    bytes: int = 0
    fulfilledBy: list[str] = Field(default_factory=list)


class SubscriptionRequest(BaseModel):
    callbackUrl: str
    eventTypes: list[str] = Field(default_factory=lambda: ["*"])
    customerId: str | None = None


def create_process_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings("process")
    db = Database(settings.db_url, ProcessBase.metadata)
    broker = make_broker(settings)
    stream = EventStream(settings)
    outbox = Outbox(db, OutboxMessage, InboxMessage, broker, stream, ("sales-order-events",))
    md = MasterDataClient(settings)
    svc = OrderService(settings, db, outbox, md)
    http = httpx.AsyncClient(timeout=10)
    bg: list[asyncio.Task] = []

    async def sweeper():
        while True:
            await asyncio.sleep(30)
            try:
                await svc.sweep()
            except Exception:  # noqa: BLE001
                log.exception("sweeper failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        outbox.start()
        await broker.start()
        bg.append(asyncio.create_task(sweeper()))
        yield
        for t in bg:
            t.cancel()
        await outbox.stop()
        await broker.stop()
        await stream.close()
        await http.aclose()

    app = create_app(settings, "Sales Order - Process API (Order Status API)",
                     "Process layer of the Sales Order data product: order intake (validate, enrich, canonicalize, "
                     "publish), fulfilment orchestration with D365/WMS/TMS, exception management and the **Order "
                     "Status API** - one status and one lifecycle history for every order, whatever the channel.")
    app.openapi_tags = TAGS
    app.router.lifespan_context = lifespan
    app.state.svc, app.state.db, app.state.outbox, app.state.broker = svc, db, outbox, broker

    def actor_of(request: Request, explicit: str | None) -> str:
        return explicit or request.headers.get("x-user-id") or request.state.principal.get("sub", "user")

    # ------------------------------------------------------------------ bus consumers
    async def on_status_event(envelope: dict[str, Any]) -> None:
        if outbox.processed(envelope["id"]):
            return
        try:
            await svc.apply_status_event(envelope["data"], causation_id=envelope["id"])
        except ApiError as e:
            if e.status not in (404, 422):
                raise
            log.warning("status event dropped: %s %s", e.title, e.detail)  # → DLQ in Service Bus terms
        outbox.mark_processed(envelope["id"], "system-status-events")

    async def on_order_event_webhooks(envelope: dict[str, Any]) -> None:
        data = envelope.get("data", {})
        with db.session() as s:
            subs = s.execute(select(SubscriptionRow).where(SubscriptionRow.active.is_(True))).scalars().all()
            targets = [(x.subscription_id, x.callback_url, x.secret) for x in subs
                       if ("*" in x.event_types or data.get("eventType") in x.event_types
                           or data.get("toStatus") in x.event_types)
                       and (not x.customer_id or x.customer_id == data.get("customerId"))]
        if not targets:
            return
        payload = {**envelope, "data": {k: v for k, v in data.items() if k != "snapshot"}}
        body = json.dumps(payload, separators=(",", ":")).encode()
        for sub_id, url, secret in targets:
            sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            status = None
            try:
                resp = await http.post(url, content=body, headers={"content-type": "application/cloudevents+json",
                                                                    "x-tfso-signature": f"sha256={sig}",
                                                                    "x-tfso-event-id": envelope["id"]})
                status = f"{resp.status_code}"
                ok = resp.status_code < 300
            except Exception as exc:  # noqa: BLE001
                status, ok = f"error: {exc}"[:200], False
            with db.session() as s:
                sub = s.get(SubscriptionRow, sub_id)
                if sub:
                    sub.deliveries += 1
                    sub.failures += 0 if ok else 1
                    sub.last_status, sub.last_delivery_at = status, utcnow()

    broker.subscribe("system-status-events", "process-api", on_status_event)
    broker.subscribe("sales-order-events", "process-webhooks", on_order_event_webhooks)

    @app.post("/v1/bus/{topic}", tags=["events"], include_in_schema=False)
    async def bus_delivery(topic: str, envelope: dict[str, Any] = Body(...)):
        return {"delivered": await broker.deliver_local(topic, envelope)}

    # ------------------------------------------------------------------ ops
    @app.get("/health", tags=["order status"])
    async def health():
        return {"status": "ok", "service": "process-api", "env": settings.env, "busMode": settings.bus_mode,
                "db": db.ping(), "outbox": outbox.stats(), "eventStream": stream.enabled}

    @app.get("/v1/reference/statuses", tags=["order status"], summary="State machine: statuses, categories, transitions")
    async def reference():
        return reference_catalogue()

    # ------------------------------------------------------------------ intake
    @app.post("/v1/orders/intake", tags=["order intake"], status_code=202,
              summary="Channel-neutral order intake (used by the Experience APIs)")
    async def intake(req: IntakeRequest, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        body = req.model_dump(mode="json", exclude_none=True)
        return await svc.intake(body, idempotency_key)

    # ------------------------------------------------------------------ order status API
    @app.get("/v1/orders", tags=["order status"], summary="Search orders")
    async def search(status: str | None = None, statusCategory: str | None = None, customerId: str | None = None,
                     channel: str | None = None, customerPo: str | None = None, erpSalesOrderId: str | None = None,
                     fromDate: date | None = None, toDate: date | None = None, hasExceptions: bool | None = None,
                     q: str | None = None, page: int = Query(1, ge=1), pageSize: int = Query(25, ge=1, le=200),
                     sort: str = "-receivedAt",
                     full: bool = Query(False, description="Include the canonical order document in each item")):
        return views.search_orders(db, status=status, status_category=statusCategory, customer_id=customerId,
                                   channel=channel, customer_po=customerPo, erp_sales_order_id=erpSalesOrderId,
                                   from_date=fromDate, to_date=toDate, has_exceptions=hasExceptions, q=q, page=page,
                                   page_size=pageSize, sort=sort, full=full)

    @app.get("/v1/order-events", tags=["order status"],
             summary="Lifecycle event feed across all orders (oldest first, paged)")
    async def order_events(since: datetime | None = None, until: datetime | None = None, eventType: str | None = None,
                           orderId: str | None = None, sourceSystem: str | None = None,
                           page: int = Query(1, ge=1), pageSize: int = Query(200, ge=1, le=1000)):
        return views.event_feed(db, since=since, until=until, event_type=eventType, order_id=orderId,
                                source_system=sourceSystem, page=page, page_size=pageSize)

    @app.get("/v1/datasets/stats", tags=["governance"], summary="Live volume/freshness facts for the data catalog")
    async def ds_stats():
        return views.dataset_stats(db)

    # ------------------------------------------------------------------ data governance (marketplace backing store)
    def access_view(x: DataAccessRequestRow) -> dict:
        return {"requestId": x.request_id, "datasetId": x.dataset_id, "requester": x.requester, "team": x.team,
                "purpose": x.purpose, "consumerSystem": x.consumer_system, "accessLevel": x.access_level,
                "status": x.status, "requestedAt": iso(x.requested_at), "decidedAt": iso(x.decided_at),
                "decidedBy": x.decided_by, "decisionNote": x.decision_note, "expiresAt": iso(x.expires_at)}

    @app.post("/v1/governance/access-requests", tags=["governance"], status_code=201)
    async def create_access(req: AccessRequest):
        now = utcnow()
        auto = req.autoApprove
        x = DataAccessRequestRow(request_id=f"AR-{secrets.token_hex(4).upper()}", dataset_id=req.datasetId,
                                 requester=req.requester, team=req.team, purpose=req.purpose,
                                 consumer_system=req.consumerSystem, access_level=req.accessLevel,
                                 status="APPROVED" if auto else "PENDING", requested_at=now,
                                 decided_at=now if auto else None, decided_by="policy:auto-approve" if auto else None,
                                 decision_note="Internal dataset - approved by policy" if auto else None,
                                 expires_at=now + timedelta(days=365) if auto else None)
        with db.session() as s:
            s.add(x)
            s.flush()
            return access_view(x)

    @app.get("/v1/governance/access-requests", tags=["governance"])
    async def list_access(datasetId: str | None = None, requester: str | None = None, status: str | None = None):
        with db.session() as s:
            q = select(DataAccessRequestRow)
            if datasetId:
                q = q.where(DataAccessRequestRow.dataset_id == datasetId)
            if requester:
                q = q.where(DataAccessRequestRow.requester == requester)
            if status:
                q = q.where(DataAccessRequestRow.status == status.upper())
            rows = s.execute(q.order_by(DataAccessRequestRow.requested_at.desc())).scalars().all()
            return {"items": [access_view(x) for x in rows]}

    @app.post("/v1/governance/access-requests/{request_id}/decision", tags=["governance"])
    async def decide_access(request_id: str, req: AccessDecision):
        with db.session() as s:
            x = s.get(DataAccessRequestRow, request_id)
            if not x:
                raise ApiError(404, "Access request not found", request_id)
            if x.status != "PENDING" and req.decision != "REVOKED":
                raise ApiError(409, "Already decided", f"Request is {x.status}")
            x.status, x.decided_by, x.decision_note, x.decided_at = req.decision, req.decidedBy, req.note, utcnow()
            x.expires_at = utcnow() + timedelta(days=365) if req.decision == "APPROVED" else None
            s.flush()
            return access_view(x)

    @app.post("/v1/governance/exports", tags=["governance"], status_code=201)
    async def record_export(req: ExportAudit):
        with db.session() as s:
            x = DataExportRow(export_id=f"EX-{secrets.token_hex(5).upper()}", dataset_id=req.datasetId,
                              requester=req.requester, format=req.format, filters=req.filters, fields=req.fields,
                              row_count=req.rowCount, bytes=req.bytes, fulfilled_by=req.fulfilledBy)
            s.add(x)
            s.flush()
            return {"exportId": x.export_id, "createdAt": iso(x.created_at)}

    @app.get("/v1/governance/exports", tags=["governance"])
    async def list_exports(datasetId: str | None = None, requester: str | None = None, limit: int = Query(50, le=500)):
        with db.session() as s:
            q = select(DataExportRow)
            if datasetId:
                q = q.where(DataExportRow.dataset_id == datasetId)
            if requester:
                q = q.where(DataExportRow.requester == requester)
            rows = s.execute(q.order_by(DataExportRow.created_at.desc()).limit(limit)).scalars().all()
            return {"items": [{"exportId": x.export_id, "datasetId": x.dataset_id, "requester": x.requester,
                               "format": x.format, "filters": x.filters, "fields": x.fields, "rowCount": x.row_count,
                               "bytes": x.bytes, "fulfilledBy": x.fulfilled_by, "createdAt": iso(x.created_at)}
                              for x in rows]}

    @app.get("/v1/governance/usage", tags=["governance"], summary="Per-dataset usage: exports, consumers, pending requests")
    async def usage():
        with db.session() as s:
            out: dict[str, dict] = {}
            for ds, n, last in s.execute(select(DataExportRow.dataset_id, func.count(), func.max(DataExportRow.created_at))
                                         .group_by(DataExportRow.dataset_id)).all():
                out.setdefault(ds, {})["exports"] = n
                out[ds]["lastExportAt"] = iso(last)
            for ds, status, n in s.execute(select(DataAccessRequestRow.dataset_id, DataAccessRequestRow.status, func.count())
                                           .group_by(DataAccessRequestRow.dataset_id, DataAccessRequestRow.status)).all():
                out.setdefault(ds, {})[{"APPROVED": "approvedConsumers", "PENDING": "pendingRequests"}.get(status, status.lower())] = n
            return {"items": out}

    @app.get("/v1/orders/by-po/{customer_po}", tags=["order status"], summary="Find orders by customer PO")
    async def by_po(customer_po: str, customerId: str | None = None):
        res = views.search_orders(db, customer_po=customer_po, customer_id=customerId, page_size=50)
        if not res["items"]:
            raise ApiError(404, "Order not found", f"No order for PO {customer_po}")
        return res

    @app.get("/v1/orders/{order_id}", tags=["order status"], summary="Canonical sales order (CDM v1) with lifecycle history")
    async def get_order(order_id: str, response: Response, includeHistory: bool = True,
                        if_none_match: str | None = Header(None, alias="If-None-Match")):
        doc = views.get_order(db, order_id, includeHistory)
        etag = f'"{doc["version"]}"'
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return doc

    @app.get("/v1/orders/{order_id}/status", tags=["order status"], summary="Status view with milestones")
    async def get_status(order_id: str, response: Response, view: Literal["internal", "customer"] = "internal"):
        v = views.status_view(db, order_id, customer_facing=view == "customer")
        response.headers["ETag"] = f'"{v["version"]}"'
        return v

    @app.get("/v1/orders/{order_id}/history", tags=["order status"], summary="Ordered lifecycle history")
    async def get_history(order_id: str, eventType: str | None = None, since: datetime | None = None):
        with db.session() as s:
            views._row(s, order_id)
            items = views.history_entries(s, order_id, event_types=eventType.split(",") if eventType else None,
                                          since=since)
        return {"orderId": order_id, "items": items, "count": len(items)}

    @app.get("/v1/orders/{order_id}/documents", tags=["order status"], summary="Inbound and outbound documents")
    async def get_documents(order_id: str):
        return views.documents(db, order_id)

    @app.get("/v1/orders/{order_id}/documents/{document_id}", tags=["order status"])
    async def get_document(order_id: str, document_id: str):
        return views.document(db, order_id, document_id)

    @app.get("/v1/orders/{order_id}/related", tags=["order status"], summary="Orders in the same group and links")
    async def get_related(order_id: str):
        return views.related(db, order_id)

    @app.get("/v1/order-groups/{group_id}/history", tags=["order status"], summary="Merged lifecycle of a group of orders")
    async def get_group_history(group_id: str):
        return views.group_history(db, group_id)

    @app.get("/v1/exceptions", tags=["customer service"], summary="Exception queue")
    async def exceptions(customerId: str | None = None, code: str | None = None):
        return views.exceptions_queue(db, customerId, code)

    @app.get("/v1/status/summary", tags=["order status"])
    async def status_summary():
        return views.summary(db)

    @app.get("/v1/metrics/kpis", tags=["order status"], summary="Touchless rate, first-pass, cycle time, freshness, ROI")
    async def metrics(laborRatePerHour: float = 32.0):
        return views.kpis(db, laborRatePerHour)

    # ------------------------------------------------------------------ customer service
    @app.patch("/v1/orders/{order_id}", tags=["customer service"], summary="Correct an order in the exception queue")
    async def correct(order_id: str, req: CorrectionRequest, request: Request, response: Response,
                      if_match: str | None = Header(None, alias="If-Match")):
        doc = await svc.correct(order_id, req.model_dump(exclude_none=True), if_match, actor_of(request, req.actor))
        response.headers["ETag"] = f'"{doc["version"]}"'
        return doc

    @app.post("/v1/orders/{order_id}/resubmit", tags=["customer service"])
    async def resubmit(order_id: str, request: Request, req: ActionRequest | None = None):
        req = req or ActionRequest()
        return await svc.resubmit(order_id, actor_of(request, req.actor), req.comment or req.reason)

    @app.post("/v1/orders/{order_id}/hold", tags=["customer service"])
    async def hold(order_id: str, request: Request, req: ActionRequest | None = None):
        req = req or ActionRequest()
        return await svc.hold(order_id, actor_of(request, req.actor), req.reason)

    @app.post("/v1/orders/{order_id}/release-hold", tags=["customer service"])
    async def release_hold(order_id: str, request: Request, req: ActionRequest | None = None):
        req = req or ActionRequest()
        return await svc.release_hold(order_id, actor_of(request, req.actor), req.reason)

    @app.post("/v1/orders/{order_id}/cancel", tags=["customer service"])
    async def cancel(order_id: str, request: Request, req: ActionRequest | None = None):
        req = req or ActionRequest()
        return await svc.cancel(order_id, actor_of(request, req.actor), req.reasonCode, req.reason)

    # ------------------------------------------------------------------ events from systems
    @app.post("/v1/events", tags=["events"], status_code=202, summary="Apply a status event from D365/WMS/TMS")
    async def events(event: dict[str, Any] = Body(...)):
        if not event.get("eventId") or not event.get("eventType"):
            raise ApiError(422, "Invalid event", "eventId and eventType are required")
        event.setdefault("occurredAt", iso(utcnow()))
        event.setdefault("sourceSystem", "EXTERNAL")
        return await svc.apply_status_event(event)

    # ------------------------------------------------------------------ webhooks
    def sub_view(x: SubscriptionRow, show_secret: bool = False) -> dict:
        v = {"subscriptionId": x.subscription_id, "callbackUrl": x.callback_url, "eventTypes": x.event_types,
             "customerId": x.customer_id, "active": x.active, "createdAt": iso(x.created_at),
             "deliveries": x.deliveries, "failures": x.failures, "lastStatus": x.last_status,
             "lastDeliveryAt": iso(x.last_delivery_at)}
        if show_secret:
            v["secret"] = x.secret
        return v

    @app.get("/v1/subscriptions", tags=["webhooks"])
    async def list_subs():
        with db.session() as s:
            return {"items": [sub_view(x) for x in s.execute(select(SubscriptionRow)).scalars()]}

    @app.post("/v1/subscriptions", tags=["webhooks"], status_code=201)
    async def create_sub(req: SubscriptionRequest):
        if not req.callbackUrl.startswith(("https://", "http://")):
            raise ApiError(422, "Invalid callback URL", "callbackUrl must be http(s)")
        if settings.is_prod and not req.callbackUrl.startswith("https://"):
            raise ApiError(422, "Invalid callback URL", "HTTPS is required in production")
        x = SubscriptionRow(subscription_id=f"sub-{secrets.token_hex(6)}", callback_url=req.callbackUrl,
                            event_types=req.eventTypes or ["*"], customer_id=req.customerId,
                            secret=secrets.token_urlsafe(32))
        with db.session() as s:
            s.add(x)
            s.flush()
            return sub_view(x, show_secret=True)

    @app.delete("/v1/subscriptions/{sub_id}", tags=["webhooks"], status_code=204)
    async def delete_sub(sub_id: str):
        with db.session() as s:
            x = s.get(SubscriptionRow, sub_id)
            if not x:
                raise ApiError(404, "Subscription not found", sub_id)
            s.delete(x)
        return Response(status_code=204)

    return app


def __getattr__(name: str):  # lazy module attribute so importing the package has no side effects
    if name == "app":
        globals()["app"] = create_process_app()
        return globals()["app"]
    raise AttributeError(name)
