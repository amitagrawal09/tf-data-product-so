"""Order intake & fulfilment orchestration (Process API).

Every state change goes through `Recorder.record()`, which
  * enforces the state machine,
  * appends an immutable `orderHistory` entry (sequence, timings, actor, tier, diff),
  * stamps the ROI stage timestamps,
  * queues an `OrderStatusChanged` CloudEvent in the outbox (same DB transaction as the order).
"""
from __future__ import annotations

import asyncio
import copy
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..common import x12
from ..common.bus import cloud_event
from ..common.cdm import CDM_VERSION, SalesOrder
from ..common.config import Settings
from ..common.db import Database
from ..common.outbox import Outbox
from ..common.statuses import (CANCELLABLE_BEFORE_RANK, CORRECTABLE, SYSTEM_EVENT_TO_STATUS, TERMINAL,
                               HistoryEventType as H, OrderStatus as S, can_transition, category_of, is_stale,
                               rank_of)
from ..common.util import qty_str, group_id as new_group_id, iso, new_id, order_id as new_order_id, parse_dt, utcnow
from ..common.web import ApiError, correlation_id
from . import rules
from .masterdata import MasterDataClient
from .models import CounterRow, DocumentRow, HistoryRow, IdempotencyRow, OrderRow

log = logging.getLogger("tfso.process")

STAGE_KEY = {S.RECEIVED: "received", S.INGESTED: "ingested", S.VALIDATED: "validated", S.ENRICHED: "enriched",
             S.PUBLISHED: "published", S.SUBMITTED_TO_ERP: "submittedToErp", S.ACCEPTED: "erpCreated",
             S.RELEASED: "released", S.PICKED: "picked", S.PACKED: "packed", S.PARTIALLY_SHIPPED: "shipped",
             S.SHIPPED: "shipped", S.DELIVERED: "delivered", S.INVOICED: "invoiced", S.CLOSED: "closed",
             S.CANCELLED: "cancelled", S.REJECTED: "cancelled"}
TIER3_SOURCES = {"D365", "WMS", "TMS", "CRM"}
OPEN_STATUSES = [s.value for s in S if s not in TERMINAL]
MANUAL_TOUCH_MINUTES = {"MANUAL_ENTRY": 8.0, "CORRECTION": 6.0, "RESUBMIT": 1.0, "HOLD": 2.0, "RELEASE_HOLD": 2.0,
                        "CANCEL": 3.0}
CHANNEL_SOURCE = {"EDI": "EDI-GATEWAY", "EMAIL": "EMAIL-INTAKE", "PORTAL": "CUSTOMER-PORTAL",
                  "MANUAL": "CS-PORTAL", "PARTNER_API": "PARTNER-API"}


def json_diff(a: Any, b: Any, path: str = "") -> list[dict[str, Any]]:
    """Minimal JSON-Patch style diff (add/remove/replace) used for history `changes`."""
    if isinstance(a, dict) and isinstance(b, dict):
        ops = []
        for k in sorted(set(a) | set(b)):
            p = f"{path}/{k}"
            if k not in a:
                ops.append({"op": "add", "path": p, "value": b[k]})
            elif k not in b:
                ops.append({"op": "remove", "path": p, "fromValue": a[k]})
            else:
                ops.extend(json_diff(a[k], b[k], p))
        return ops
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        ops = []
        for i, (x, y) in enumerate(zip(a, b)):
            ops.extend(json_diff(x, y, f"{path}/{i}"))
        return ops
    if a != b:
        return [{"op": "replace", "path": path or "/", "value": b, "fromValue": a}]
    return []


class Recorder:
    """Collects the effects of one unit of work on one order."""

    def __init__(self, svc: "OrderService", doc: dict[str, Any], seq: int, last_at: datetime | None):
        self.svc, self.doc, self.seq, self.last_at = svc, doc, seq, last_at
        self.entries: list[dict[str, Any]] = []
        self.messages: list[tuple[str, dict]] = []
        self.documents: list[DocumentRow] = []

    def record(self, event_type: H, *, to_status: S | None = None, source_system: str = "PROCESS-API",
               tier: str | None = None, actor: dict | None = None, occurred_at: datetime | None = None,
               reason_code: str | None = None, message: str | None = None, changes: list | None = None,
               line_changes: list | None = None, document_ref: str | None = None,
               external_event_id: str | None = None, causation_id: str | None = None, force: bool = False) -> dict:
        doc = self.doc
        now = utcnow()
        occurred_at = occurred_at or now
        from_status = S(doc["status"]) if doc.get("status") else None
        if to_status is not None and from_status is not None and to_status != from_status or (
                to_status is not None and to_status == S.PARTIALLY_SHIPPED):
            if from_status and not force and not can_transition(from_status, to_status):
                raise ApiError(409, "Invalid status transition", f"{from_status.value} → {to_status.value} is not allowed")
        if to_status is not None:
            doc["status"] = to_status.value
            doc["statusCategory"] = category_of(to_status).value
            doc["statusUpdatedAt"] = iso(now)
            doc["statusReason"] = message if category_of(to_status).value in ("ACTION_REQUIRED", "CANCELLED") else None
            key = STAGE_KEY.get(to_status)
            stamps = doc["roi"].setdefault("stageTimestamps", {})
            if key and not stamps.get(key):
                stamps[key] = iso(occurred_at)
            self._cycle_times()
        self.seq += 1
        elapsed = (now - self.last_at).total_seconds() if self.last_at else None
        self.last_at = now
        entry = {
            "eventId": new_id("hist-"), "orderId": doc["orderId"], "orderGroupId": doc.get("orderGroupId"),
            "sequence": self.seq, "eventType": event_type.value, "occurredAt": iso(occurred_at), "recordedAt": iso(now),
            "fromStatus": from_status.value if from_status else None,
            "toStatus": to_status.value if to_status else (from_status.value if from_status else None),
            "statusCategory": doc.get("statusCategory"),
            "tier": tier or ("TIER3" if source_system in TIER3_SOURCES else "TIER2"),
            "sourceSystem": source_system, "actor": actor or {"type": "SYSTEM", "id": "process-api"},
            "correlationId": correlation_id(), "causationId": causation_id, "externalEventId": external_event_id,
            "reasonCode": reason_code, "message": message, "lineChanges": line_changes or [],
            "changes": changes or [], "documentRef": document_ref,
            "elapsedSincePreviousSeconds": round(elapsed, 3) if elapsed is not None else None,
        }
        entry = {k: v for k, v in entry.items() if v not in (None, [])} | {"sequence": self.seq}
        self.entries.append(entry)
        self.messages.append(("sales-order-events", self._event(entry, status_changed=to_status is not None)))
        return entry

    def _cycle_times(self) -> None:
        roi = self.doc["roi"]
        st = roi.get("stageTimestamps", {})
        ct = roi.setdefault("cycleTimes", {})
        rec = parse_dt(st.get("received"))
        if not rec:
            return
        if st.get("erpCreated"):
            ct["receiptToErpMinutes"] = round((parse_dt(st["erpCreated"]) - rec).total_seconds() / 60, 2)
        if st.get("shipped"):
            ct["receiptToShipHours"] = round((parse_dt(st["shipped"]) - rec).total_seconds() / 3600, 3)
        if st.get("invoiced"):
            ct["receiptToInvoiceHours"] = round((parse_dt(st["invoiced"]) - rec).total_seconds() / 3600, 3)

    def _event(self, entry: dict, status_changed: bool) -> dict:
        d = self.doc
        data = {"orderId": d["orderId"], "orderGroupId": d.get("orderGroupId"), "sequence": entry["sequence"],
                "eventType": entry["eventType"], "fromStatus": entry.get("fromStatus"), "toStatus": entry.get("toStatus"),
                "statusCategory": d.get("statusCategory"), "occurredAt": entry["occurredAt"],
                "recordedAt": entry["recordedAt"], "sourceSystem": entry["sourceSystem"],
                "sourceChannel": d["source"]["channel"], "customerId": d["parties"]["soldTo"].get("partyId"),
                "customerPo": (d.get("purchaseOrder") or {}).get("poNumber"),
                "erpSalesOrderId": (d.get("erp") or {}).get("salesOrderId"),
                "totalAmount": (d.get("totals") or {}).get("grandTotal"), "currency": (d.get("totals") or {}).get("currency"),
                "roi": {k: d["roi"].get(k) for k in ("touchless", "touchCount", "exceptionReasonCodes", "stageTimestamps",
                                                     "acknowledgmentTimestamps", "cycleTimes", "chargebackRisk")},
                "historyEntry": entry}
        if status_changed:
            data["snapshot"] = {k: v for k, v in d.items() if k != "orderHistory"}
        return cloud_event("tfso.salesorder.OrderStatusChanged", "/process/sales-orders", d["orderId"], data,
                           correlation_id())

    def command(self, command_type: str, data: dict) -> None:
        self.messages.append(("sales-order-commands",
                              cloud_event(f"tfso.salesorder.{command_type}", "/process/sales-orders",
                                          self.doc["orderId"], data, correlation_id())))

    def touch(self, actor: str, action: str) -> None:
        roi = self.doc["roi"]
        roi["touchless"] = False
        roi["touchCount"] = int(roi.get("touchCount", 0)) + 1
        roi.setdefault("manualTouches", []).append({"at": iso(utcnow()), "actor": actor, "action": action,
                                                    "minutes": MANUAL_TOUCH_MINUTES.get(action, 5.0)})

    def add_document(self, document_type: str, direction: str, content: str, *, control_number: str | None = None,
                     content_type: str = "text/plain", partner: str | None = None) -> str:
        doc_id = new_id("doc-")
        self.documents.append(DocumentRow(document_id=doc_id, order_id=self.doc["orderId"],
                                          order_group_id=self.doc.get("orderGroupId"), document_type=document_type,
                                          direction=direction, control_number=control_number,
                                          content_type=content_type, partner=partner, content=content))
        return doc_id


class OrderService:
    def __init__(self, settings: Settings, db: Database, outbox: Outbox, md: MasterDataClient):
        self.settings, self.db, self.outbox, self.md = settings, db, outbox, md
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, order_id: str) -> asyncio.Lock:
        return self._locks.setdefault(order_id, asyncio.Lock())

    # ------------------------------------------------------------------ persistence helpers
    def _load(self, s: Session, order_id: str) -> OrderRow:
        row = s.get(OrderRow, order_id)
        if row is None:
            raise ApiError(404, "Order not found", f"No order {order_id}")
        return row

    def _last_at(self, s: Session, order_id: str) -> datetime | None:
        v = s.scalar(select(func.max(HistoryRow.recorded_at)).where(HistoryRow.order_id == order_id))
        return v if v is None or v.tzinfo else v.replace(tzinfo=timezone.utc)

    def _next_control(self, s: Session, name: str = "x12") -> int:
        c = s.get(CounterRow, name)
        if c is None:
            c = CounterRow(name=name, value=1000)
            s.add(c)
            s.flush()
        c.value += 1
        return c.value

    def _persist(self, s: Session, row: OrderRow | None, rec: Recorder, actor: str = "process-api") -> OrderRow:
        doc = rec.doc
        now = utcnow()
        audit = doc.setdefault("audit", {})
        audit.update(updatedAt=iso(now), updatedBy=actor)
        if row is None:
            row = OrderRow(order_id=doc["orderId"], received_at=parse_dt(doc["source"]["receivedAt"]), version=0)
            s.add(row)
        doc["version"] = int(row.version or 0) + 1
        row.version = doc["version"]
        row.history_seq = rec.seq
        row.order_group_id = doc["orderGroupId"]
        row.status, row.status_category = doc["status"], doc["statusCategory"]
        row.source_channel = doc["source"]["channel"]
        sold = doc["parties"]["soldTo"]
        row.customer_id, row.customer_name = sold.get("partyId"), sold.get("name")
        row.customer_po = (doc.get("purchaseOrder") or {}).get("poNumber")
        row.ship_to_id = doc["parties"]["shipTo"].get("partyId")
        row.erp_sales_order_id = (doc.get("erp") or {}).get("salesOrderId")
        rdd = (doc.get("dates") or {}).get("requestedDeliveryDate")
        try:
            row.requested_delivery_date = date.fromisoformat(rdd[:10]) if rdd else None
        except ValueError:
            row.requested_delivery_date = None
        row.updated_at = now
        totals = doc.get("totals") or {}
        row.total_amount = float(totals["grandTotal"]) if totals.get("grandTotal") else None
        row.currency = totals.get("currency") or (doc.get("commercial") or {}).get("currency")
        row.open_exception_count = sum(1 for e in doc.get("exceptions", []) if e.get("state") == "OPEN")
        roi = doc["roi"]
        row.touchless = bool(roi.get("touchless", True))
        row.first_pass = (doc.get("dataQuality") or {}).get("firstPass")
        row.erp_created = bool(roi.get("stageTimestamps", {}).get("erpCreated"))
        row.receipt_to_erp_minutes = roi.get("cycleTimes", {}).get("receiptToErpMinutes")
        row.receipt_to_ship_hours = roi.get("cycleTimes", {}).get("receiptToShipHours")
        stored = {k: v for k, v in doc.items() if k != "orderHistory"}
        row.doc = copy.deepcopy(stored)
        s.flush()
        for e in rec.entries:
            s.add(HistoryRow(event_id=e["eventId"], order_id=doc["orderId"], order_group_id=doc.get("orderGroupId"),
                             sequence=e["sequence"], event_type=e["eventType"], occurred_at=parse_dt(e["occurredAt"]),
                             recorded_at=parse_dt(e["recordedAt"]), from_status=e.get("fromStatus"),
                             to_status=e.get("toStatus"), source_system=e["sourceSystem"],
                             external_event_id=e.get("externalEventId"), entry=e))
        for d in rec.documents:
            s.add(d)
        for topic, env in rec.messages:
            self.outbox.add(s, topic, env)
        return row

    def _recorder(self, s: Session, row: OrderRow) -> Recorder:
        return Recorder(self, copy.deepcopy(row.doc), row.history_seq, self._last_at(s, row.order_id))

    # ------------------------------------------------------------------ intake
    def find_duplicates(self, customer_id: str, po: str, exclude: str) -> list[str]:
        with self.db.session() as s:
            return list(s.scalars(select(OrderRow.order_id).where(
                OrderRow.customer_id == customer_id, OrderRow.customer_po == po, OrderRow.order_id != exclude,
                OrderRow.status.in_([x for x in OPEN_STATUSES if x != S.EXCEPTION.value]))))

    def _build_doc(self, req: dict[str, Any], oid: str, gid: str, now: datetime) -> dict[str, Any]:
        draft = req.get("draft") or {}
        channel = req["sourceChannel"]
        received_at = iso(parse_dt(req.get("receivedAt")) or now)
        sold_in = draft.get("soldTo") or (draft.get("parties") or {}).get("soldTo") or {}
        ship_in = draft.get("shipTo") or (draft.get("parties") or {}).get("shipTo") or {}
        sold = {"role": "SOLD_TO", "partyId": draft.get("customerId") or sold_in.get("partyId") or sold_in.get("customerId")}
        for k in ("name", "gln", "duns", "contacts"):
            if sold_in.get(k):
                sold[k] = sold_in[k]
        ship = {"role": "SHIP_TO", "partyId": draft.get("shipToId") or ship_in.get("partyId") or ship_in.get("shipToId")}
        for k in ("ediLocationCode", "gln", "name", "address", "contacts"):
            if ship_in.get(k):
                ship[k] = ship_in[k]
        parties = {"soldTo": sold, "shipTo": ship}
        for role in ("billTo", "buyer"):
            p = draft.get(role) or (draft.get("parties") or {}).get(role)
            if p:
                parties[role] = {"role": role.upper().replace("TO", "_TO") if role == "billTo" else "BUYER", **p}
        po = dict(draft.get("purchaseOrder") or {})
        if draft.get("customerPo") and not po.get("poNumber"):
            po["poNumber"] = draft["customerPo"]
        if po.get("poDate"):
            po["poDate"] = str(po["poDate"])
            if len(po["poDate"]) == 8 and po["poDate"].isdigit():
                po["poDate"] = f"{po['poDate'][:4]}-{po['poDate'][4:6]}-{po['poDate'][6:]}"
        dates = {k: _isodate(v) for k, v in (draft.get("dates") or {}).items() if v}
        if draft.get("requestedDeliveryDate"):
            dates["requestedDeliveryDate"] = _isodate(draft["requestedDeliveryDate"])
        lines = []
        for i, ln in enumerate(draft.get("lines") or [], start=1):
            q = ln.get("quantity") or {}
            if not isinstance(q, dict):
                q = {"value": q, "uom": ln.get("uom")}
            line = {"lineNumber": int(ln.get("lineNumber") or i), "item": dict(ln.get("item") or {}),
                    "quantity": {"value": qty_str(q.get("value")),
                                 "uom": (q.get("uom") or ln.get("uom") or "CA").upper()},
                    "quantities": {}, "status": "OPEN"}
            if ln.get("itemNumber"):
                line["item"]["itemNumber"] = ln["itemNumber"]
            price = ln.get("unitPrice")
            if price not in (None, "", {}):
                line["unitPrice"] = price if isinstance(price, dict) else {"amount": str(price), "source": "PURCHASE_ORDER"}
                line["unitPrice"].setdefault("source", "PURCHASE_ORDER")
                if line["unitPrice"].get("amount") in (None, ""):
                    line.pop("unitPrice")
                else:
                    line["unitPrice"]["amount"] = str(line["unitPrice"]["amount"])
            for k in ("buyerLineNumber", "notes", "minRemainingShelfLifeDays", "substitutionAllowed",
                      "allowancesCharges"):
                if ln.get(k) not in (None, "", []):
                    line[k] = ln[k]
            for k in ("requestedDeliveryDate", "requestedShipDate"):
                if ln.get(k):
                    line[k] = _isodate(ln[k])
            if isinstance(line.get("notes"), str):
                line["notes"] = [{"type": "CUSTOMER", "text": line["notes"]}]
            lines.append(line)
        actor = req.get("submittedBy") or {"type": "SYSTEM", "id": CHANNEL_SOURCE.get(channel, channel)}
        notes = draft.get("notes") or []
        if isinstance(notes, str):
            notes = [{"type": "CUSTOMER", "text": notes}]
        roi = {"sourceChannel": channel, "touchless": True, "touchCount": 0, "manualTouches": [],
               "exceptionReasonCodes": [], "stageTimestamps": {}, "acknowledgmentTimestamps": {}, "cycleTimes": {},
               "chargebackRisk": [], "registeredConsumers": ["d365-erp", "lakehouse", "crm"]}
        doc = {
            "schemaVersion": CDM_VERSION, "orderId": oid, "orderGroupId": gid, "version": 0,
            "orderType": draft.get("orderType") or "STANDARD", "purpose": draft.get("purpose") or "ORIGINAL",
            "status": None, "statusCategory": None, "statusUpdatedAt": received_at,
            "source": {"channel": channel, "channelDetail": req.get("channelDetail"), "receivedAt": received_at,
                       "sourceDocument": {**(req.get("sourceDocument") or {"documentType": "PORTAL_FORM"}),
                                          "receivedAt": received_at},
                       "submittedBy": actor},
            "purchaseOrder": po, "parties": parties, "dates": dates,
            "commercial": draft.get("commercial") or {}, "shipping": draft.get("shipping") or {},
            "lines": lines, "allowancesCharges": draft.get("allowancesCharges") or [],
            "totals": {}, "references": draft.get("references") or [], "notes": notes,
            "shipments": [], "invoices": [], "acknowledgments": [], "exceptions": [],
            "dataQuality": {"ruleSetVersion": "1.0", "attempt": 0, "passed": False, "score": 0, "results": []},
            "roi": roi, "relatedOrders": [], "orderHistory": [],
            "audit": {"createdAt": iso(now), "createdBy": actor.get("id"), "updatedAt": iso(now),
                      "updatedBy": actor.get("id"), "contractVersion": CDM_VERSION, "lineage": []},
            "extensions": draft.get("extensions") or {},
        }
        if req.get("extraction"):
            doc["source"]["extraction"] = req["extraction"]
        doc["source"]["sourceDocument"].pop("rawContent", None)
        doc["source"]["sourceDocument"].pop("contentType", None)
        if not doc["source"].get("channelDetail"):
            doc["source"].pop("channelDetail")
        if channel == "MANUAL":
            roi.update(touchless=False, touchCount=1, manualTouches=[{"at": received_at, "actor": actor.get("id"),
                                                                     "action": "MANUAL_ENTRY", "minutes": 8.0}])
        return doc

    async def intake(self, req: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key:
            with self.db.session() as s:
                hit = s.get(IdempotencyRow, idempotency_key)
                if hit:
                    return {**hit.response, "idempotentReplay": True}
        now = utcnow()
        oid = new_order_id(now)
        gid = req.get("orderGroupId") or new_group_id(now)
        doc = self._build_doc(req, oid, gid, now)
        rec = Recorder(self, doc, 0, None)
        channel = req["sourceChannel"]
        src = CHANNEL_SOURCE.get(channel, channel)
        sd = req.get("sourceDocument") or {}
        raw_ref = None
        if sd.get("rawContent"):
            raw_ref = rec.add_document(sd.get("documentType", "RAW"), "INBOUND", sd["rawContent"],
                                       control_number=sd.get("transactionSetControlNumber"),
                                       content_type=sd.get("contentType", "text/plain"), partner=sd.get("senderId"))
            doc["source"]["sourceDocument"]["documentId"] = raw_ref
        lineage = doc["audit"]["lineage"]
        lineage.append({"step": "CAPTURE", "service": src, "at": doc["source"]["receivedAt"], "outputRef": raw_ref})
        rec.record(H.ORDER_RECEIVED, to_status=S.RECEIVED, source_system=src, tier="TIER1",
                   actor=doc["source"]["submittedBy"], occurred_at=parse_dt(doc["source"]["receivedAt"]),
                   document_ref=raw_ref,
                   message=f"Received via {channel}" + (f" ({sd.get('reference')})" if sd.get("reference") else ""))
        for ack in req.get("acknowledgments") or []:
            ack_doc = rec.add_document(ack["documentType"], "OUTBOUND", ack["content"],
                                       control_number=ack.get("controlNumber"), partner=sd.get("senderId"))
            doc["acknowledgments"].append({"type": ack["type"], "documentId": ack_doc,
                                           "controlNumber": ack.get("controlNumber"), "status": ack.get("status", "ACCEPTED"),
                                           "channel": channel, "recipient": sd.get("senderId"), "sentAt": iso(now)})
            if ack["type"] == "FUNCTIONAL_ACK_997":
                doc["roi"]["acknowledgmentTimestamps"]["functionalAck997"] = iso(now)
            rec.record(H.DOCUMENT_SENT, source_system=src, tier="TIER1", document_ref=ack_doc,
                       message=f"{ack['type']} sent to {sd.get('senderId')}")
        with self.db.session() as s:
            siblings = list(s.scalars(select(OrderRow.order_id).where(OrderRow.order_group_id == gid)))
        for sib in siblings:
            doc["relatedOrders"].append({"orderId": sib, "relationship": "SAME_INTERCHANGE", "createdAt": iso(now)})
        if siblings:
            rec.record(H.ORDER_LINKED, message=f"Linked to {len(siblings)} order(s) in group {gid}")
        lineage.append({"step": "INGEST", "service": "process-api", "at": iso(utcnow()), "inputRef": raw_ref,
                        "outputRef": oid})
        rec.record(H.ORDER_INGESTED, to_status=S.INGESTED,
                   message=f"Parsed {len(doc['lines'])} line(s) into the working format; order id {oid} assigned")
        with self.db.session() as s:
            self._persist(s, None, rec, doc["source"]["submittedBy"].get("id", "process-api"))
        self.outbox.kick()
        await self.run_pipeline(oid)
        response = self.accepted_view(oid)
        if idempotency_key:
            with self.db.session() as s:
                if not s.get(IdempotencyRow, idempotency_key):
                    s.add(IdempotencyRow(key=idempotency_key, response=response))
        return response

    async def run_pipeline(self, order_id: str, actor: str = "process-api") -> None:
        """INGESTED → validate → enrich → canonicalize/publish → submit to D365."""
        async with self.lock(order_id):
            with self.db.session() as s:
                row = self._load(s, order_id)
                if row.status != S.INGESTED.value:
                    return
                rec = self._recorder(s, row)
                loaded_version = row.version
            doc = rec.doc
            try:
                checks = await rules.validate(doc, self.md, self.find_duplicates)
            except ApiError as exc:  # master data unavailable → stay INGESTED; sweeper retries
                log.warning("validation deferred for %s: %s", order_id, exc.detail)
                return
            new_ex = rules.apply_results(doc, checks, actor)
            doc["audit"]["lineage"].append({"step": "VALIDATE", "service": "process-api", "at": iso(utcnow())})
            warnings = [r for r in checks.results if not r["passed"] and r["severity"] == "WARNING"]
            if checks.errors:
                codes = sorted({e["code"] for e in new_ex})
                rec.record(H.VALIDATION_FAILED, to_status=S.EXCEPTION, reason_code=codes[0],
                           message=f"{len(new_ex)} quality rule failure(s): {', '.join(codes)} - held in exception queue")
            else:
                rec.record(H.VALIDATION_PASSED, to_status=S.VALIDATED,
                           message=f"{len(checks.results)} checks passed"
                                   + (f" with {len(warnings)} warning(s)" if warnings else ""))
                before = copy.deepcopy(doc)
                try:
                    await rules.enrich(doc, self.md)
                except ApiError as exc:
                    log.warning("enrichment deferred for %s: %s", order_id, exc.detail)
                    return
                doc["audit"]["lineage"].append({"step": "ENRICH", "service": "process-api", "at": iso(utcnow())})
                changes = [c for c in json_diff(before, doc) if not c["path"].startswith(("/audit", "/roi", "/status"))]
                rec.record(H.ORDER_ENRICHED, to_status=S.ENRICHED, changes=changes[:200],
                           message=f"Applied customer, ship-to, item and pricing master data ({len(changes)} element(s))")
                try:
                    SalesOrder.model_validate({**doc, "orderHistory": []})
                except ValidationError as exc:
                    doc["exceptions"].append({"exceptionId": new_id("ex-"), "code": "CONTRACT_VIOLATION",
                                              "severity": "ERROR", "message": str(exc)[:1000],
                                              "detectedAt": iso(utcnow()), "detectedBy": "process-api", "state": "OPEN"})
                    rec.record(H.VALIDATION_FAILED, to_status=S.EXCEPTION, reason_code="CONTRACT_VIOLATION",
                               message="Order does not conform to the canonical contract", force=True)
                else:
                    doc["audit"]["lineage"].append({"step": "CANONICALIZE", "service": "process-api", "at": iso(utcnow())})
                    rec.record(H.ORDER_PUBLISHED, to_status=S.PUBLISHED,
                               message=f"Canonical order published under contract v{CDM_VERSION}")
                    self._submit_to_erp(rec)
            with self.db.session() as s:
                row = self._load(s, order_id)
                if row.version != loaded_version:
                    raise ApiError(409, "Concurrent modification", f"{order_id} changed during processing")
                self._persist(s, row, rec, actor)
            self.outbox.kick()

    def _submit_to_erp(self, rec: Recorder) -> None:
        doc = rec.doc
        snapshot = {k: v for k, v in doc.items() if k != "orderHistory"}
        rec.command("CreateErpSalesOrder", {"order": snapshot})
        doc["audit"]["lineage"].append({"step": "ORCHESTRATE", "service": "process-api", "at": iso(utcnow()),
                                        "outputRef": "sales-order-commands"})
        rec.record(H.SUBMITTED_TO_ERP, to_status=S.SUBMITTED_TO_ERP,
                   message="CreateErpSalesOrder command sent to D365 system API")

    async def sweep(self) -> int:
        """Retry orders stuck in INGESTED (e.g. master data was unavailable)."""
        cutoff = utcnow() - timedelta(seconds=20)
        with self.db.session() as s:
            ids = list(s.scalars(select(OrderRow.order_id).where(OrderRow.status == S.INGESTED.value,
                                                                 OrderRow.updated_at < cutoff).limit(50)))
        for oid in ids:
            try:
                await self.run_pipeline(oid)
            except Exception:  # noqa: BLE001
                log.exception("sweep failed for %s", oid)
        return len(ids)

    # ------------------------------------------------------------------ status events from Tier 3
    async def apply_status_event(self, ev: dict[str, Any], causation_id: str | None = None) -> dict[str, Any]:
        et = ev.get("eventType")
        target = SYSTEM_EVENT_TO_STATUS.get(et)
        if target is None:
            raise ApiError(422, "Unknown event type", f"{et} is not a supported status event")
        with self.db.session() as s:
            row = None
            if ev.get("orderId"):
                row = s.get(OrderRow, ev["orderId"])
            if row is None and ev.get("erpSalesOrderId"):
                row = s.execute(select(OrderRow).where(OrderRow.erp_sales_order_id == ev["erpSalesOrderId"])).scalar_one_or_none()
            if row is None:
                raise ApiError(404, "Order not found", f"No order for event {ev.get('eventId')}")
            order_id = row.order_id
        async with self.lock(order_id):
            with self.db.session() as s:
                if ev.get("eventId") and s.scalar(select(HistoryRow.event_id).where(
                        HistoryRow.external_event_id == ev["eventId"])):
                    return {"orderId": order_id, "duplicate": True}
                row = self._load(s, order_id)
                rec = self._recorder(s, row)
                partner = await self._partner_for(rec.doc)
                self._apply(rec, ev, target, causation_id, partner, s)
                self._persist(s, row, rec, ev.get("sourceSystem", "system"))
            self.outbox.kick()
            return {"orderId": order_id, "status": rec.doc["status"], "applied": [e["eventType"] for e in rec.entries]}

    async def _partner_for(self, doc: dict) -> dict | None:
        if doc["source"]["channel"] != "EDI":
            return None
        sender = doc["source"]["sourceDocument"].get("senderId")
        try:
            return await self.md.partner(sender) if sender else None
        except ApiError:
            return None

    def _apply(self, rec: Recorder, ev: dict, target: S, causation_id: str | None, partner: dict | None,
               s: Session) -> None:
        doc = rec.doc
        et = ev["eventType"]
        src = ev.get("sourceSystem", "D365")
        occurred = parse_dt(ev.get("occurredAt")) or utcnow()
        actor = {"type": "SYSTEM", "id": src}
        current = S(doc["status"])
        lines_by_no = {ln["lineNumber"]: ln for ln in doc["lines"]}
        line_changes: list[dict] = []
        common = dict(source_system=src, actor=actor, occurred_at=occurred, external_event_id=ev.get("eventId"),
                      causation_id=causation_id)

        def set_line(n: int, field: str, value: Any, qty_field: bool = True) -> None:
            ln = lines_by_no.get(int(n))
            if not ln:
                return
            container = ln.setdefault("quantities", {}) if qty_field else ln
            old = container.get(field)
            if str(old) != str(value):
                container[field] = value
                line_changes.append({"lineNumber": int(n), "field": field if not qty_field else f"quantities.{field}",
                                     "fromValue": old, "toValue": value})

        # ---- stale / out-of-order events (record, merge facts, never regress status)
        if is_stale(current, target) and et not in ("SHIPPED", "INVOICED", "DELIVERED"):
            rec.record(H.LATE_EVENT_IGNORED, reason_code="OUT_OF_ORDER", message=f"{et} arrived after order reached {current.value}",
                       **common)
            return

        if et == "ERP_ACCEPTED" or et == "CREDIT_HOLD_RELEASED":
            doc["erp"] = {**(doc.get("erp") or {}), **(ev.get("erp") or {}), "lastSyncedAt": iso(utcnow())}
            if ev.get("promisedShipDate"):
                doc["dates"]["promisedShipDate"] = ev["promisedShipDate"]
            backorders = []
            for l in ev.get("lines", []):
                n = l["lineNumber"]
                if l.get("erpLineId") and lines_by_no.get(n):
                    lines_by_no[n]["erpLineId"] = l["erpLineId"]
                set_line(n, "confirmed", str(l.get("confirmedQuantity", "0")))
                set_line(n, "backordered", str(l.get("backorderedQuantity", "0")))
                set_line(n, "allocated", str(l.get("confirmedQuantity", "0")))
                set_line(n, "status", l.get("status", "ALLOCATED"), qty_field=False)
                if lines_by_no.get(n):
                    lines_by_no[n]["promisedShipDate"] = ev.get("promisedShipDate")
                if Decimal(str(l.get("backorderedQuantity", "0"))) > 0:
                    backorders.append(n)
            if et == "CREDIT_HOLD_RELEASED":
                self._resolve_exceptions(doc, "CREDIT_HOLD", src)
                rec.record(H.HOLD_RELEASED, to_status=S.ACCEPTED, message="Credit hold released in D365", **common)
            else:
                rec.record(H.ERP_ACCEPTED, to_status=S.ACCEPTED, line_changes=line_changes,
                           message=f"Sales order {doc['erp'].get('salesOrderId')} created in D365", **common)
            rec.record(H.INVENTORY_RESERVED, source_system=src, actor=actor,
                       message=f"Inventory reserved at {doc['erp'].get('warehouseId') or 'warehouse'}")
            for n in backorders:
                rec.record(H.LINE_BACKORDERED, source_system=src, actor=actor, reason_code="INSUFFICIENT_INVENTORY",
                           message=f"Line {n}: {lines_by_no[n]['quantities'].get('backordered')} "
                                   f"{lines_by_no[n]['quantity']['uom']} backordered")
            self._acknowledge(rec, s, partner, "855")
            return

        if et == "CREDIT_HOLD_PLACED":
            doc["erp"] = {**(doc.get("erp") or {}), **(ev.get("erp") or {})}
            doc["exceptions"].append({"exceptionId": new_id("ex-"), "code": "CREDIT_HOLD", "severity": "ERROR",
                                      "message": ev.get("reason") or "Credit hold", "detectedAt": iso(occurred),
                                      "detectedBy": src, "state": "OPEN"})
            doc["roi"].setdefault("exceptionReasonCodes", []).append("CREDIT_HOLD")
            rec.record(H.CREDIT_HOLD_PLACED, to_status=S.CREDIT_HOLD, reason_code="CREDIT_HOLD",
                       message=ev.get("reason"), **common)
            return

        if et == "ERP_REJECTED":
            doc["erp"] = {**(doc.get("erp") or {"system": "D365FO", "company": self.settings.d365_company}),
                          "rejectionReason": ev.get("reason")}
            doc["exceptions"].append({"exceptionId": new_id("ex-"), "code": "ERP_REJECTED", "severity": "ERROR",
                                      "message": ev.get("reason") or "Rejected by D365", "detectedAt": iso(occurred),
                                      "detectedBy": src, "state": "OPEN"})
            doc["roi"].setdefault("exceptionReasonCodes", []).append("ERP_REJECTED")
            if current in (S.SUBMITTED_TO_ERP,):
                rec.record(H.ERP_REJECTED, to_status=S.ERP_REJECTED, reason_code="ERP_REJECTED",
                           message=ev.get("reason"), **common)
            else:
                rec.record(H.ERP_REJECTED, reason_code="ERP_REJECTED", message=ev.get("reason"), **common)
            return

        if et == "CANCELLED":
            if current not in TERMINAL:
                rec.record(H.CANCELLED, to_status=S.CANCELLED, message=ev.get("reason") or "Cancelled in D365",
                           force=True, **common)
            return

        # ---- fulfilment
        force = rank_of(target) > rank_of(current) and current in (S.ON_HOLD, S.CREDIT_HOLD)
        if et in ("RELEASED_TO_WAREHOUSE", "PICKED", "PACKED"):
            line_status = {"RELEASED_TO_WAREHOUSE": "RELEASED", "PICKED": "PICKED", "PACKED": "PACKED"}[et]
            for ln in doc["lines"]:
                if Decimal(str(ln["quantities"].get("confirmed", "0"))) > 0 and ln.get("status") not in ("SHIPPED", "DELIVERED", "INVOICED"):
                    set_line(ln["lineNumber"], "status", line_status, qty_field=False)
                    if et == "PICKED":
                        set_line(ln["lineNumber"], "picked", ln["quantities"].get("confirmed"))
            hist = {"RELEASED_TO_WAREHOUSE": H.RELEASED_TO_WAREHOUSE, "PICKED": H.PICKED, "PACKED": H.PACKED}[et]
            if not is_stale(current, target):
                rec.record(hist, to_status=target, line_changes=line_changes, force=force, **common)
            return

        if et == "SHIPPED":
            sh = ev.get("shipment") or {}
            if sh and not any(x.get("shipmentId") == sh.get("shipmentId") for x in doc["shipments"]):
                doc["shipments"].append(sh)
            for l in ev.get("lines", []) or [{"lineNumber": x["lineNumber"], "shippedQuantity": x["quantity"],
                                              "lots": x.get("lots", [])} for x in sh.get("lines", [])]:
                n = l["lineNumber"]
                ln = lines_by_no.get(n)
                if not ln:
                    continue
                shipped = Decimal(str(ln["quantities"].get("shipped", "0"))) + Decimal(str(l.get("shippedQuantity", "0")))
                set_line(n, "shipped", str(shipped))
                ln.setdefault("lots", []).extend(l.get("lots", []))
                conf = Decimal(str(ln["quantities"].get("confirmed", "0")))
                set_line(n, "status", "SHIPPED" if shipped >= conf else "PACKED", qty_field=False)
            open_qty = sum(Decimal(str(ln["quantities"].get("confirmed", "0"))) - Decimal(str(ln["quantities"].get("shipped", "0")))
                           for ln in doc["lines"])
            new_status = S.SHIPPED if open_qty <= 0 else S.PARTIALLY_SHIPPED
            if is_stale(current, new_status) and new_status != S.PARTIALLY_SHIPPED:
                rec.record(H.SHIPPED, line_changes=line_changes, message="Late ship confirmation merged", **common)
            else:
                rec.record(H.SHIPPED, to_status=new_status, line_changes=line_changes, force=force or current == S.ON_HOLD,
                           message=f"Shipment {sh.get('shipmentId')} via {sh.get('carrierName') or sh.get('carrierScac')}, "
                                   f"tracking {sh.get('trackingNumber')}" + ("" if open_qty <= 0 else f"; {open_qty} still open"),
                           **common)
            self._acknowledge(rec, s, partner, "856", shipment=sh)
            return

        if et == "DELIVERED":
            sh = ev.get("shipment") or {}
            for x in doc["shipments"]:
                if x.get("status") != "DELIVERED":
                    x["status"], x["deliveredAt"] = "DELIVERED", sh.get("deliveredAt") or iso(occurred)
                    if sh.get("proofOfDelivery"):
                        x["proofOfDelivery"] = sh["proofOfDelivery"]
            for l in ev.get("lines", []):
                set_line(l["lineNumber"], "delivered", str(l.get("deliveredQuantity")))
                set_line(l["lineNumber"], "status", "DELIVERED", qty_field=False)
            if not is_stale(current, target):
                rec.record(H.DELIVERED, to_status=target, line_changes=line_changes, force=True,
                           message="Proof of delivery received", **common)
            return

        if et == "INVOICED":
            inv = ev.get("invoice") or {}
            if inv and not any(x.get("invoiceId") == inv.get("invoiceId") for x in doc["invoices"]):
                doc["invoices"].append(inv)
            for l in ev.get("lines", []):
                ln = lines_by_no.get(l["lineNumber"])
                if not ln:
                    continue
                total = Decimal(str(ln["quantities"].get("invoiced", "0"))) + Decimal(str(l.get("invoicedQuantity", "0")))
                set_line(l["lineNumber"], "invoiced", str(total))
                set_line(l["lineNumber"], "status", "INVOICED", qty_field=False)
            if not is_stale(current, target):
                rec.record(H.INVOICED, to_status=target, line_changes=line_changes, force=True,
                           message=f"Invoice {inv.get('invoiceId')} posted for {inv.get('amount')} {inv.get('currency', '')}",
                           **common)
            self._acknowledge(rec, s, partner, "810", invoice=inv)
            return

        if et == "CLOSED":
            rec.record(H.CLOSED, to_status=S.CLOSED, force=True, message="Order closed", **common)

    def _resolve_exceptions(self, doc: dict, code: str | None, by: str, note: str = "Resolved") -> None:
        for ex in doc.get("exceptions", []):
            if ex.get("state") == "OPEN" and (code is None or ex.get("code") == code):
                ex.update(state="RESOLVED", resolvedAt=iso(utcnow()), resolvedBy=by, resolutionNote=note)

    def _acknowledge(self, rec: Recorder, s: Session, partner: dict | None, kind: str, **kw) -> None:
        """Outbound customer communication: EDI 855/856/810 for EDI partners, e-mail confirmation otherwise."""
        doc = rec.doc
        now = utcnow()
        ack_ts = doc["roi"].setdefault("acknowledgmentTimestamps", {})
        if doc["source"]["channel"] == "EDI" and partner:
            if not partner.get(f"send{kind}", False):
                return
            control = self._next_control(s)
            ours, theirs = self.settings.isa_sender_id, partner["isaId"]
            if kind == "855":
                content, ack_type, key = x12.build_855(doc, ours, theirs, control), "PO_ACK_855", "poAck855"
            elif kind == "856":
                content, ack_type, key = x12.build_856(doc, kw["shipment"], ours, theirs, control), "SHIP_NOTICE_856", "shipNotice856"
            else:
                content, ack_type, key = x12.build_810(doc, kw["invoice"], ours, theirs, control), "INVOICE_810", "invoice810"
            doc_id = rec.add_document(f"X12_{kind}", "OUTBOUND", content, control_number=str(control), partner=theirs)
            backordered = any(Decimal(str(l["quantities"].get("backordered", "0"))) > 0 for l in doc["lines"])
            doc["acknowledgments"].append({"type": ack_type, "documentId": doc_id, "controlNumber": str(control),
                                           "status": "ACCEPTED_WITH_CHANGES" if kind == "855" and backordered else "SENT",
                                           "channel": "EDI", "recipient": theirs, "sentAt": iso(now)})
            if not ack_ts.get(key):
                ack_ts[key] = iso(now)
            sla = (partner.get("ackSlaMinutes") or {}).get(kind)
            ref = {"855": doc["roi"]["stageTimestamps"].get("received"),
                   "856": doc["roi"]["stageTimestamps"].get("shipped")}.get(kind)
            if sla and ref:
                minutes = (now - parse_dt(ref)).total_seconds() / 60
                if kind == "856":
                    doc["roi"]["cycleTimes"]["shipToAsnMinutes"] = round(minutes, 2)
                if minutes > sla:
                    doc["roi"].setdefault("chargebackRisk", []).append(f"LATE_{kind}")
            rec.record(H.DOCUMENT_SENT, source_system="EDI-GATEWAY", tier="TIER1", document_ref=doc_id,
                       message=f"X12 {kind} sent to {theirs} (control {control})")
            return
        contact = next(iter(doc["parties"]["soldTo"].get("contacts") or []), {}) or {}
        recipient = contact.get("email") or "customer"
        subject = {"855": "Order confirmation", "856": "Shipment notice", "810": "Invoice"}[kind]
        body = [f"Subject: {subject} - PO {doc['purchaseOrder'].get('poNumber')} / order {doc['orderId']}",
                f"To: {recipient}", "",
                f"Status: {doc['status']} ({doc['statusCategory']})"]
        if kind == "855":
            body += [f"D365 sales order: {(doc.get('erp') or {}).get('salesOrderId')}",
                     f"Promised ship date: {doc['dates'].get('promisedShipDate')}"]
            body += [f"  Line {l['lineNumber']}: {l['item'].get('itemNumber')} {l['item'].get('description')} - "
                     f"confirmed {l['quantities'].get('confirmed')} / backordered {l['quantities'].get('backordered')} "
                     f"{l['quantity']['uom']}" for l in doc["lines"]]
        elif kind == "856":
            sh = kw.get("shipment") or {}
            body += [f"Carrier: {sh.get('carrierName')} ({sh.get('carrierScac')})", f"Tracking: {sh.get('trackingNumber')}",
                     f"Estimated delivery: {sh.get('estimatedDeliveryAt')}"]
        else:
            inv = kw.get("invoice") or {}
            body += [f"Invoice {inv.get('invoiceId')} dated {inv.get('invoiceDate')}: {inv.get('amount')} {inv.get('currency')}",
                     f"Due: {inv.get('dueDate')}"]
        doc_id = rec.add_document("EMAIL_NOTIFICATION", "OUTBOUND", "\n".join(body), partner=recipient)
        doc["acknowledgments"].append({"type": "EMAIL_CONFIRMATION" if doc["source"]["channel"] in ("EMAIL", "MANUAL")
                                       else "PORTAL_NOTIFICATION", "documentId": doc_id, "status": "SENT",
                                       "channel": doc["source"]["channel"], "recipient": recipient, "sentAt": iso(now)})
        rec.record(H.NOTIFICATION_SENT, source_system="NOTIFICATION", tier="TIER1", document_ref=doc_id,
                   message=f"{subject} sent to {recipient}")

    # ------------------------------------------------------------------ customer service actions
    async def correct(self, order_id: str, body: dict[str, Any], if_match: str | None, actor: str) -> dict:
        changes = body.get("changes") or {}
        async with self.lock(order_id):
            with self.db.session() as s:
                row = self._load(s, order_id)
                _check_version(row, if_match)
                if S(row.status) not in CORRECTABLE:
                    raise ApiError(409, "Order not correctable",
                                   f"Corrections are allowed in {sorted(x.value for x in CORRECTABLE)}; order is {row.status}")
                rec = self._recorder(s, row)
                doc = rec.doc
                before = copy.deepcopy(doc)
                _apply_changes(doc, changes)
                patch = [c for c in json_diff(before, doc) if not c["path"].startswith("/audit")]
                if not patch:
                    raise ApiError(422, "No changes", "The correction does not change the order")
                rec.touch(actor, "CORRECTION")
                rec.record(H.ORDER_CORRECTED, actor={"type": "USER", "id": actor}, changes=patch,
                           reason_code=body.get("reasonCode"), message=body.get("reason") or "Corrected by customer service")
                self._persist(s, row, rec, actor)
            self.outbox.kick()
        return self.get_order(order_id)

    async def resubmit(self, order_id: str, actor: str, comment: str | None) -> dict:
        async with self.lock(order_id):
            with self.db.session() as s:
                row = self._load(s, order_id)
                if row.status not in (S.EXCEPTION.value, S.ERP_REJECTED.value):
                    raise ApiError(409, "Order cannot be resubmitted", f"Order is {row.status}")
                rec = self._recorder(s, row)
                if row.status == S.ERP_REJECTED.value:
                    self._resolve_exceptions(rec.doc, "ERP_REJECTED", actor, "Resubmitted")
                rec.touch(actor, "RESUBMIT")
                rec.record(H.ORDER_RESUBMITTED, to_status=S.INGESTED, actor={"type": "USER", "id": actor},
                           message=comment or "Re-entered at validation")
                self._persist(s, row, rec, actor)
        await self.run_pipeline(order_id, actor)
        self.outbox.kick()
        return self.accepted_view(order_id)

    async def hold(self, order_id: str, actor: str, reason: str | None) -> dict:
        async with self.lock(order_id):
            with self.db.session() as s:
                row = self._load(s, order_id)
                if not can_transition(row.status, S.ON_HOLD):
                    raise ApiError(409, "Cannot hold order", f"Order is {row.status}")
                rec = self._recorder(s, row)
                rec.doc.setdefault("extensions", {})["heldFromStatus"] = row.status
                rec.touch(actor, "HOLD")
                rec.record(H.HOLD_PLACED, to_status=S.ON_HOLD, actor={"type": "USER", "id": actor},
                           reason_code="MANUAL_HOLD", message=reason or "Placed on hold")
                self._persist(s, row, rec, actor)
            self.outbox.kick()
        return self.status_view(order_id)

    async def release_hold(self, order_id: str, actor: str, reason: str | None) -> dict:
        async with self.lock(order_id):
            with self.db.session() as s:
                row = self._load(s, order_id)
                if row.status != S.ON_HOLD.value:
                    raise ApiError(409, "Order is not on hold", f"Order is {row.status}")
                rec = self._recorder(s, row)
                back = S(rec.doc.get("extensions", {}).pop("heldFromStatus", S.PUBLISHED.value))
                rec.touch(actor, "RELEASE_HOLD")
                rec.record(H.HOLD_RELEASED, to_status=back, actor={"type": "USER", "id": actor},
                           message=reason or "Hold released")
                if back == S.PUBLISHED:
                    self._submit_to_erp(rec)
                self._persist(s, row, rec, actor)
            self.outbox.kick()
        return self.status_view(order_id)

    async def cancel(self, order_id: str, actor: str, reason_code: str | None, reason: str | None) -> dict:
        async with self.lock(order_id):
            with self.db.session() as s:
                row = self._load(s, order_id)
                if S(row.status) in TERMINAL or rank_of(row.status) >= CANCELLABLE_BEFORE_RANK:
                    raise ApiError(409, "Order cannot be cancelled", f"Order is {row.status}")
                rec = self._recorder(s, row)
                self._resolve_exceptions(rec.doc, None, actor, "Order cancelled")
                for ln in rec.doc["lines"]:
                    ln["quantities"]["cancelled"] = ln["quantities"].get("ordered") or ln["quantity"]["value"]
                    ln["status"] = "CANCELLED"
                rec.touch(actor, "CANCEL")
                rec.record(H.CANCELLED, to_status=S.CANCELLED, actor={"type": "USER", "id": actor},
                           reason_code=reason_code or "CUSTOMER_REQUEST", message=reason or "Cancelled", force=True)
                if (rec.doc.get("erp") or {}).get("salesOrderId"):
                    rec.command("CancelErpSalesOrder", {"orderId": order_id, "reason": reason,
                                                        "erpSalesOrderId": rec.doc["erp"]["salesOrderId"]})
                self._persist(s, row, rec, actor)
            self.outbox.kick()
        return self.status_view(order_id)

    # ------------------------------------------------------------------ read models (delegated)
    def get_order(self, order_id: str, include_history: bool = True) -> dict:
        from .views import get_order
        return get_order(self.db, order_id, include_history)

    def status_view(self, order_id: str) -> dict:
        from .views import status_view
        return status_view(self.db, order_id)

    def accepted_view(self, order_id: str) -> dict:
        from .views import accepted_view
        return accepted_view(self.db, order_id)


def _isodate(v: Any) -> str:
    s = str(v)
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _check_version(row: OrderRow, if_match: str | None) -> None:
    if if_match is None:
        raise ApiError(428, "Precondition required", "Send If-Match with the order version (ETag)")
    v = if_match.strip().strip('W/').strip('"')
    if v != str(row.version):
        raise ApiError(412, "Precondition failed", f"Order is at version {row.version}, you sent {v}. Reload and retry.",
                       headers={"ETag": f'"{row.version}"'})


def _apply_changes(doc: dict, ch: dict[str, Any]) -> None:
    """Apply a correction payload. Supported: customerPo, purchaseOrder, requestedDeliveryDate, dates, shipTo,
    soldTo/customerId, shipping, notes, lines (by lineNumber; `remove: true` deletes; new numbers add)."""
    if ch.get("customerPo"):
        doc["purchaseOrder"]["poNumber"] = ch["customerPo"]
    if isinstance(ch.get("purchaseOrder"), dict):
        doc["purchaseOrder"].update({k: v for k, v in ch["purchaseOrder"].items() if v is not None})
    if ch.get("requestedDeliveryDate"):
        doc["dates"]["requestedDeliveryDate"] = _isodate(ch["requestedDeliveryDate"])
        doc["dates"].pop("requestedShipDate", None)
    if isinstance(ch.get("dates"), dict):
        doc["dates"].update({k: _isodate(v) for k, v in ch["dates"].items() if v})
    ship = ch.get("shipTo") or ({"shipToId": ch["shipToId"]} if ch.get("shipToId") else None)
    if ship:
        sid = ship.get("shipToId") or ship.get("partyId")
        doc["parties"]["shipTo"] = {"role": "SHIP_TO", "partyId": sid} if sid else {"role": "SHIP_TO", **ship}
        doc["dates"].pop("requestedShipDate", None)
    sold = ch.get("customerId") or (ch.get("soldTo") or {}).get("partyId") or (ch.get("soldTo") or {}).get("customerId")
    if sold:
        doc["parties"]["soldTo"] = {"role": "SOLD_TO", "partyId": sold}
    if isinstance(ch.get("shipping"), dict):
        doc.setdefault("shipping", {}).update(ch["shipping"])
    if ch.get("notes"):
        notes = ch["notes"] if isinstance(ch["notes"], list) else [{"type": "INTERNAL", "text": ch["notes"]}]
        doc.setdefault("notes", []).extend(notes)
    by_no = {ln["lineNumber"]: ln for ln in doc["lines"]}
    for lc in ch.get("lines") or []:
        n = int(lc.get("lineNumber") or 0)
        if lc.get("remove"):
            doc["lines"] = [l for l in doc["lines"] if l["lineNumber"] != n]
            continue
        ln = by_no.get(n)
        if ln is None:
            n = n or (max(by_no) + 1 if by_no else 1)
            ln = {"lineNumber": n, "item": {}, "quantity": {"value": None, "uom": "CA"}, "quantities": {}, "status": "OPEN"}
            doc["lines"].append(ln)
            by_no[n] = ln
        item = lc.get("item") or ({"itemNumber": lc["itemNumber"]} if lc.get("itemNumber") else None)
        if item:
            ln["item"] = {k: v for k, v in item.items() if v}  # re-resolve on validation
        q = lc.get("quantity")
        if q is not None:
            q = q if isinstance(q, dict) else {"value": q}
            if q.get("value") not in (None, ""):
                ln["quantity"]["value"] = qty_str(q["value"])
            if q.get("uom"):
                ln["quantity"]["uom"] = q["uom"].upper()
        if lc.get("uom"):
            ln["quantity"]["uom"] = lc["uom"].upper()
        if "unitPrice" in lc:  # explicit null → drop the customer price and use the agreement price
            p = lc["unitPrice"]
            amount = p.get("amount") if isinstance(p, dict) else p
            if amount in (None, ""):
                ln.pop("unitPrice", None)
            else:
                ln["unitPrice"] = {"amount": str(amount), "source": "MANUAL_OVERRIDE"}
        for k in ("requestedDeliveryDate", "minRemainingShelfLifeDays", "substitutionAllowed"):
            if lc.get(k) is not None:
                ln[k] = lc[k]
    doc["lines"].sort(key=lambda l: l["lineNumber"])
