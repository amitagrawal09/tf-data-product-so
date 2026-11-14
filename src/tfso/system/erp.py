"""D365 / WMS / TMS adapters behind the System API.

`SimulatedErp` is a faithful functional stand-in for D365 F&O + WMS + TMS used locally and in non-prod:
credit check, inventory reservation with backorders, release, pick, pack, ship confirmation (with FSMA 204
lot / traceability lot codes), proof of delivery and invoice posting. `LiveD365` creates real sales orders
through OData data entities; fulfilment events then arrive through D365 business events and WMS/TMS webhooks.
"""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..common.db import Database, JsonDoc
from ..common.util import iso, new_id, qty_str, utcnow
from .d365_client import D365Client
from .masterdata import SeedMasterData


class SystemBase(DeclarativeBase):
    pass


class ErpOrder(SystemBase):
    __tablename__ = "erp_sales_orders"
    erp_sales_order_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    customer_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30))
    doc: Mapped[dict] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Counter(SystemBase):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(30), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class CrmActivity(SystemBase):
    __tablename__ = "crm_activities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    activity: Mapped[dict] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


FLOW = ["ACCEPTED", "RELEASED", "PICKED", "PACKED", "SHIPPED", "DELIVERED", "INVOICED", "CLOSED"]
STEP_EVENT = {"RELEASED": ("RELEASED_TO_WAREHOUSE", "D365"), "PICKED": ("PICKED", "WMS"),
              "PACKED": ("PACKED", "WMS"), "SHIPPED": ("SHIPPED", "WMS"), "DELIVERED": ("DELIVERED", "TMS"),
              "INVOICED": ("INVOICED", "D365"), "CLOSED": ("CLOSED", "D365")}


def status_event(event_type: str, source: str, order_id: str, erp_id: str | None, **kw) -> dict[str, Any]:
    ev = {"eventId": new_id("evt-"), "eventType": event_type, "orderId": order_id, "erpSalesOrderId": erp_id,
          "occurredAt": iso(utcnow()), "sourceSystem": source}
    ev.update({k: v for k, v in kw.items() if v is not None})
    return ev


class SimulatedErp:
    mode = "simulator"

    def __init__(self, db: Database, md: SeedMasterData, config: dict[str, Any]):
        self.db, self.md, self.config = db, md, config

    def _next(self, name: str, start: int) -> int:
        with self.db.session() as s:
            c = s.get(Counter, name)
            if c is None:
                c = Counter(name=name, value=start)
                s.add(c)
            c.value += 1
            return c.value

    # ------------------------------------------------------------------ create
    async def create_sales_order(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        order_id = order["orderId"]
        with self.db.session() as s:
            existing = s.execute(select(ErpOrder).where(ErpOrder.order_id == order_id)).scalar_one_or_none()
            if existing:  # idempotent re-delivery / resubmission after rejection
                if existing.status not in ("REJECTED", "CANCELLED"):
                    return [self._accepted_event(existing.doc)]
                s.delete(existing)
        customer = await self.md.get_customer(order["parties"]["soldTo"]["partyId"])
        if not customer or not customer.get("active"):
            return [status_event("ERP_REJECTED", "D365", order_id, None,
                                 reason="Customer account is blocked for sales in D365")]
        erp_id = f"SO-{self._next('erp_so', 100000):08d}"
        total = Decimal(str(order.get("totals", {}).get("grandTotal", 0)))
        lines = []
        warehouse = (order.get("shipping") or {}).get("shipFromWarehouse") or "WH-SAL"
        for ln in order["lines"]:
            item_no = ln["item"]["itemNumber"]
            qty = Decimal(str(ln["quantity"]["value"]))
            avail = Decimal(str(self.md.available(warehouse, item_no))) if self.config.get("inventoryCheck", True) else qty
            confirmed = min(qty, max(avail, Decimal(0)))
            if self.config.get("inventoryCheck", True):
                self.md.consume(warehouse, item_no, float(confirmed))
            lines.append({"lineNumber": ln["lineNumber"], "itemNumber": item_no, "uom": ln["quantity"]["uom"],
                          "ordered": qty_str(qty), "confirmed": qty_str(confirmed), "backordered": qty_str(qty - confirmed),
                          "shipped": "0", "delivered": "0", "invoiced": "0",
                          "unitPrice": str((ln.get("unitPrice") or {}).get("amount", 0)),
                          "erpLineId": f"{erp_id}-{ln['lineNumber']:03d}"})
        lead = 1
        for l in lines:
            lead = max(lead, int((await self.md.get_item(l["itemNumber"]) or {}).get("leadTimeDays", 1)))
        rsd = order.get("dates", {}).get("requestedShipDate")
        promised = max(date.fromisoformat(rsd) if rsd else date.today(), date.today() + timedelta(days=lead - 1))
        ship_to = order["parties"]["shipTo"]
        doc = {"erpSalesOrderId": erp_id, "orderId": order_id, "customerId": customer["customerId"],
               "customerAccount": customer["erpAccount"], "invoiceAccount": customer.get("invoiceAccount"),
               "warehouse": order.get("shipping", {}).get("shipFromWarehouse") or "WH-SAL",
               "site": order.get("shipping", {}).get("shipFromSite") or "SALINAS",
               "deliveryMode": order.get("shipping", {}).get("deliveryMode"),
               "shipToName": ship_to.get("name"), "promisedShipDate": promised.isoformat(),
               "total": str(total), "currency": order.get("totals", {}).get("currency", "USD"),
               "lines": lines, "shipments": [], "invoices": [], "stage": "ACCEPTED",
               "paymentTermsDays": ((order.get("commercial") or {}).get("paymentTerms") or {}).get("netDays") or 30}
        over_limit = (self.config.get("creditCheck", True) and
                      Decimal(str(customer.get("openAr", 0))) + total > Decimal(str(customer.get("creditLimit", 0))))
        doc["stage"] = "CREDIT_HOLD" if over_limit else "ACCEPTED"
        with self.db.session() as s:
            s.add(ErpOrder(erp_sales_order_id=erp_id, order_id=order_id, customer_id=customer["customerId"],
                           status=doc["stage"], doc=doc))
        if over_limit:
            return [status_event("CREDIT_HOLD_PLACED", "D365", order_id, erp_id, erp=self._erp_ref(doc, "Open order"),
                                 reason=f"Credit limit {customer.get('creditLimit')} exceeded "
                                        f"(open AR {customer.get('openAr')} + order {total})")]
        return [self._accepted_event(doc)]

    def _erp_ref(self, doc: dict, sales_status: str) -> dict:
        return {"system": "D365FO", "company": "usmf", "salesOrderId": doc["erpSalesOrderId"],
                "salesOrderStatus": sales_status, "customerAccount": doc["customerAccount"],
                "invoiceAccount": doc.get("invoiceAccount"), "siteId": doc["site"], "warehouseId": doc["warehouse"],
                "deliveryMode": doc.get("deliveryMode"), "createdAt": iso(utcnow()), "lastSyncedAt": iso(utcnow())}

    def _accepted_event(self, doc: dict) -> dict:
        return status_event("ERP_ACCEPTED", "D365", doc["orderId"], doc["erpSalesOrderId"],
                            erp=self._erp_ref(doc, "Open order"), promisedShipDate=doc["promisedShipDate"],
                            lines=[{"lineNumber": l["lineNumber"], "erpLineId": l["erpLineId"],
                                    "confirmedQuantity": l["confirmed"], "backorderedQuantity": l["backordered"],
                                    "status": "BACKORDERED" if Decimal(l["backordered"]) > 0 else "ALLOCATED"}
                                   for l in doc["lines"]])

    def get(self, *, order_id: str | None = None, erp_id: str | None = None) -> dict | None:
        with self.db.session() as s:
            q = select(ErpOrder)
            q = q.where(ErpOrder.order_id == order_id) if order_id else q.where(ErpOrder.erp_sales_order_id == erp_id)
            row = s.execute(q).scalar_one_or_none()
            return dict(row.doc) if row else None

    def _save(self, doc: dict) -> None:
        with self.db.session() as s:
            row = s.get(ErpOrder, doc["erpSalesOrderId"])
            row.doc, row.status = doc, doc["stage"]

    async def release_credit_hold(self, order_id: str) -> list[dict]:
        doc = self.get(order_id=order_id)
        if not doc:
            raise LookupError(order_id)
        if doc["stage"] != "CREDIT_HOLD":
            raise ValueError(f"Order is {doc['stage']}, not on credit hold")
        doc["stage"] = "ACCEPTED"
        self._save(doc)
        ev = self._accepted_event(doc)
        ev["eventType"] = "CREDIT_HOLD_RELEASED"
        return [ev]

    async def cancel(self, order_id: str, reason: str | None) -> list[dict]:
        doc = self.get(order_id=order_id)
        if not doc:
            return []
        if doc["stage"] in FLOW and FLOW.index(doc["stage"]) >= FLOW.index("SHIPPED"):
            return [status_event("ERP_REJECTED", "D365", order_id, doc["erpSalesOrderId"],
                                 reason="Cannot cancel: order already shipped")]
        doc["stage"] = "CANCELLED"
        self._save(doc)
        return []

    # ------------------------------------------------------------------ fulfilment simulation
    async def advance(self, order_id: str, to: str, lines: list[dict] | None = None,
                      carrier: dict | None = None, delivered_at: str | None = None) -> list[dict]:
        doc = self.get(order_id=order_id)
        if not doc:
            raise LookupError(order_id)
        to = to.upper()
        if to not in FLOW:
            raise ValueError(f"Unsupported target {to}")
        if doc["stage"] in ("CREDIT_HOLD", "CANCELLED", "REJECTED"):
            raise ValueError(f"Order is {doc['stage']} in D365")
        events: list[dict] = []
        cur = doc["stage"] if doc["stage"] in FLOW else "ACCEPTED"
        partial_ship = to == "SHIPPED" and bool(lines)
        if FLOW.index(to) <= FLOW.index(cur) and not (to == "SHIPPED" and self._open_qty(doc) > 0):
            raise ValueError(f"Order already at {cur}")
        steps = FLOW[FLOW.index(cur) + 1: FLOW.index(to) + 1] if FLOW.index(to) > FLOW.index(cur) else [to]
        for step in steps:
            ev_type, source = STEP_EVENT[step]
            if step == "SHIPPED":
                events.append(self._ship(doc, lines if partial_ship else None, carrier or {}))
                if self._open_qty(doc) > 0:
                    doc["stage"] = "PACKED"  # remaining quantity still to ship
                    self._save(doc)
                    return events
            elif step == "DELIVERED":
                events.append(self._deliver(doc, delivered_at))
            elif step == "INVOICED":
                events.append(self._invoice(doc))
            else:
                events.append(status_event(ev_type, source, order_id, doc["erpSalesOrderId"],
                                           lines=[{"lineNumber": l["lineNumber"], "status": step}
                                                  for l in doc["lines"] if Decimal(l["confirmed"]) > 0]))
            doc["stage"] = step
        self._save(doc)
        return events

    @staticmethod
    def _open_qty(doc: dict) -> Decimal:
        return sum((Decimal(l["confirmed"]) - Decimal(l["shipped"]) for l in doc["lines"]), Decimal(0))

    def _ship(self, doc: dict, lines: list[dict] | None, carrier: dict) -> dict:
        n = self._next("shipment", 500000)
        carriers = self.md.data.get("carriers", [{"scac": "CRSF", "name": "Cold Road Freight"}])
        car = carrier or carriers[n % len(carriers)]
        req = {int(l["lineNumber"]): Decimal(str(l["quantity"])) for l in (lines or [])}
        ship_lines = []
        today = date.today()
        for l in doc["lines"]:
            remaining = Decimal(l["confirmed"]) - Decimal(l["shipped"])
            qty = min(req.get(l["lineNumber"], Decimal(0)), remaining) if lines else remaining
            if qty <= 0:
                continue
            l["shipped"] = str(Decimal(l["shipped"]) + qty)
            lot = f"L{today:%y%j}{l['itemNumber'][-3:]}{secrets.token_hex(1).upper()}"
            ship_lines.append({"lineNumber": l["lineNumber"], "itemNumber": l["itemNumber"], "quantity": str(qty),
                               "uom": l["uom"], "lots": [{"lotNumber": lot, "quantity": str(qty),
                                                         "traceabilityLotCode": f"0614141{lot}",
                                                         "packDate": today.isoformat(),
                                                         "bestByDate": (today + timedelta(days=12)).isoformat(),
                                                         "countryOfOrigin": "US"}]})
        shipment = {"shipmentId": f"SHP-{n}", "asnNumber": f"ASN{n}", "erpPackingSlipId": f"PS-{n}",
                    "carrierScac": car.get("scac"), "carrierName": car.get("name"),
                    "trackingNumber": f"{car.get('scac', 'TRK')}{n}{secrets.randbelow(999):03d}",
                    "billOfLading": f"BOL{n}", "proNumber": f"PRO{n}", "trailerNumber": f"TR{secrets.randbelow(9999):04d}",
                    "sealNumber": f"SL{secrets.randbelow(999999):06d}", "shipFromWarehouse": doc["warehouse"],
                    "shippedAt": iso(utcnow()), "estimatedDeliveryAt": iso(utcnow() + timedelta(hours=20)),
                    "status": "IN_TRANSIT", "sscc": [f"00{secrets.randbelow(10**17):017d}"], "lines": ship_lines}
        doc["shipments"].append(shipment)
        return status_event("SHIPPED", "WMS", doc["orderId"], doc["erpSalesOrderId"], shipment=shipment,
                            lines=[{"lineNumber": sl["lineNumber"], "shippedQuantity": sl["quantity"],
                                    "lots": sl["lots"]} for sl in ship_lines])

    def _deliver(self, doc: dict, delivered_at: str | None = None) -> dict:
        when = delivered_at or iso(utcnow())
        for sh in doc["shipments"]:
            if sh["status"] != "DELIVERED":
                sh["status"], sh["deliveredAt"] = "DELIVERED", when
                sh["proofOfDelivery"] = {"receivedBy": "Receiving clerk", "receivedAt": when, "exceptions": []}
        for l in doc["lines"]:
            l["delivered"] = l["shipped"]
        last = doc["shipments"][-1] if doc["shipments"] else {}
        ev = status_event("DELIVERED", "TMS", doc["orderId"], doc["erpSalesOrderId"], shipment=last,
                          lines=[{"lineNumber": l["lineNumber"], "deliveredQuantity": l["delivered"]}
                                 for l in doc["lines"]])
        ev["occurredAt"] = when
        return ev

    def _invoice(self, doc: dict) -> dict:
        n = self._next("invoice", 700000)
        inv_lines, total = [], Decimal(0)
        for l in doc["lines"]:
            qty = Decimal(l["shipped"]) - Decimal(l["invoiced"])
            if qty <= 0:
                continue
            amt = (qty * Decimal(l["unitPrice"])).quantize(Decimal("0.01"))
            l["invoiced"] = str(Decimal(l["invoiced"]) + qty)
            inv_lines.append({"lineNumber": l["lineNumber"], "quantity": str(qty), "unitPrice": l["unitPrice"],
                              "amount": str(amt)})
            total += amt
        invoice = {"invoiceId": f"INV-{n}", "invoiceDate": date.today().isoformat(),
                   "dueDate": (date.today() + timedelta(days=int(doc.get("paymentTermsDays") or 30))).isoformat(),
                   "amount": str(total), "taxAmount": "0.00", "currency": doc.get("currency", "USD"),
                   "status": "POSTED", "lines": inv_lines}
        doc["invoices"].append(invoice)
        return status_event("INVOICED", "D365", doc["orderId"], doc["erpSalesOrderId"], invoice=invoice,
                            lines=[{"lineNumber": il["lineNumber"], "invoicedQuantity": il["quantity"]}
                                   for il in inv_lines])


class LiveD365(SimulatedErp):
    """Creates sales orders in D365 F&O via OData data entities; status then arrives via business events."""
    mode = "live"

    def __init__(self, db: Database, md: SeedMasterData, config: dict, client: D365Client):
        super().__init__(db, md, config)
        self.client = client

    async def create_sales_order(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        sold, ship = order["parties"]["soldTo"], order["parties"]["shipTo"]
        header = {
            "dataAreaId": self.client.company,
            "OrderingCustomerAccountNumber": sold.get("erpAccount") or sold["partyId"],
            "InvoiceCustomerAccountNumber": (order["parties"].get("billTo") or sold).get("erpAccount"),
            "CustomerRequisitionNumber": order["purchaseOrder"]["poNumber"],
            "CustomersOrderReference": order["orderId"],
            "CurrencyCode": order.get("commercial", {}).get("currency", "USD"),
            "RequestedReceiptDate": order["dates"].get("requestedDeliveryDate"),
            "RequestedShippingDate": order["dates"].get("requestedShipDate"),
            "DeliveryAddressLocationId": ship.get("erpAccount"),
            "DeliveryModeCode": order.get("shipping", {}).get("deliveryMode"),
            "DefaultShippingSiteId": order.get("shipping", {}).get("shipFromSite"),
            "DefaultShippingWarehouseId": order.get("shipping", {}).get("shipFromWarehouse"),
            "SalesOrderOriginCode": order["source"]["channel"],
        }
        created = await self.client.create("SalesOrderHeadersV2", {k: v for k, v in header.items() if v})
        so = created["SalesOrderNumber"]
        for ln in order["lines"]:
            await self.client.create("SalesOrderLines", {
                "dataAreaId": self.client.company, "SalesOrderNumber": so, "ItemNumber": ln["item"]["itemNumber"],
                "OrderedSalesQuantity": float(ln["quantity"]["value"]), "SalesUnitSymbol": ln["quantity"]["uom"],
                "SalesPrice": float((ln.get("unitPrice") or {}).get("amount", 0)),
                "RequestedReceiptDate": ln.get("requestedDeliveryDate") or order["dates"].get("requestedDeliveryDate"),
                "CustomersLineNumber": int(ln.get("buyerLineNumber") or ln["lineNumber"]),
            })
        return [
            status_event("ERP_ACCEPTED", "D365", order["orderId"], so,
                         erp={"system": "D365FO", "company": self.client.company, "salesOrderId": so,
                              "salesOrderStatus": "Open order", "createdAt": iso(utcnow())})]

    async def advance(self, *a, **kw):  # noqa: D401
        raise ValueError("Simulator actions are disabled when TFSO_D365_MODE=live")


def translate_business_event(evt: dict[str, Any]) -> dict[str, Any] | None:
    """Map a D365 F&O business event payload to a StatusEvent. Unknown events return None."""
    bid = evt.get("BusinessEventId", "")
    mapping = {"SalesOrderConfirmedBusinessEvent": "ERP_ACCEPTED",
               "SalesOrderReleasedToWarehouseBusinessEvent": "RELEASED_TO_WAREHOUSE",
               "SalesOrderPackingSlipPostedBusinessEvent": "SHIPPED",
               "SalesOrderInvoicePostedBusinessEvent": "INVOICED",
               "SalesOrderCreditLimitCheckFailedBusinessEvent": "CREDIT_HOLD_PLACED",
               "SalesOrderCanceledBusinessEvent": "CANCELLED"}
    et = mapping.get(bid)
    if not et:
        return None
    ev = status_event(et, "D365", evt.get("CustomersOrderReference"), evt.get("SalesOrderNumber") or evt.get("SalesId"))
    ev["eventId"] = evt.get("EventId") or ev["eventId"]
    ev["occurredAt"] = evt.get("EventTime") or ev["occurredAt"]
    if et == "INVOICED" and evt.get("InvoiceId"):
        ev["invoice"] = {"invoiceId": evt["InvoiceId"], "invoiceDate": (evt.get("InvoiceDate") or iso(utcnow()))[:10],
                         "amount": str(evt.get("InvoiceAmount", 0)), "currency": evt.get("CurrencyCode", "USD"),
                         "status": "POSTED", "lines": []}
    if et == "SHIPPED" and evt.get("PackingSlipId"):
        ev["shipment"] = {"shipmentId": evt["PackingSlipId"], "erpPackingSlipId": evt["PackingSlipId"],
                          "shippedAt": evt.get("EventTime") or iso(utcnow()), "status": "IN_TRANSIT", "lines": []}
    ev["payload"] = evt
    return ev
