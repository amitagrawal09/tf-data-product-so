"""Order Status API read models: canonical order, status view, lifecycle history, search, KPIs."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, asc, desc, func, or_, select

from ..common.db import Database
from ..common.statuses import (MILESTONES, STATUS_META, TERMINAL, OrderStatus as S, StatusCategory, category_of)
from ..common.util import iso, parse_dt, utcnow
from ..common.web import ApiError, correlation_id_var
from .models import DocumentRow, HistoryRow, OrderRow

MILESTONE_STAGE = {"RECEIVED": "received", "VALIDATED": "validated", "PUBLISHED": "published",
                   "ACCEPTED": "erpCreated", "RELEASED": "released", "PICKED": "picked", "SHIPPED": "shipped",
                   "DELIVERED": "delivered", "INVOICED": "invoiced"}


def _row(s, order_id: str) -> OrderRow:
    row = s.get(OrderRow, order_id)
    if row is None:
        raise ApiError(404, "Order not found", f"No order {order_id}")
    return row


def history_entries(s, order_id: str | None = None, group_id: str | None = None, event_types: list[str] | None = None,
                    since: datetime | None = None) -> list[dict]:
    q = select(HistoryRow)
    if order_id:
        q = q.where(HistoryRow.order_id == order_id).order_by(HistoryRow.sequence)
    else:
        q = q.where(HistoryRow.order_group_id == group_id).order_by(HistoryRow.recorded_at, HistoryRow.sequence)
    if event_types:
        q = q.where(HistoryRow.event_type.in_(event_types))
    if since:
        q = q.where(HistoryRow.recorded_at >= since)
    return [r.entry for r in s.execute(q).scalars()]


def get_order(db: Database, order_id: str, include_history: bool = True) -> dict:
    with db.session() as s:
        row = _row(s, order_id)
        doc = dict(row.doc)
        doc["orderHistory"] = history_entries(s, order_id) if include_history else []
        return doc


def list_item(row: OrderRow) -> dict:
    return {"orderId": row.order_id, "orderGroupId": row.order_group_id, "status": row.status,
            "statusCategory": row.status_category, "sourceChannel": row.source_channel, "customerId": row.customer_id,
            "customerName": row.customer_name, "customerPo": row.customer_po, "shipToId": row.ship_to_id,
            "erpSalesOrderId": row.erp_sales_order_id,
            "requestedDeliveryDate": row.requested_delivery_date.isoformat() if row.requested_delivery_date else None,
            "receivedAt": iso(_aware(row.received_at)), "updatedAt": iso(_aware(row.updated_at)),
            "totalAmount": float(row.total_amount) if row.total_amount is not None else None, "currency": row.currency,
            "openExceptionCount": row.open_exception_count, "touchless": row.touchless, "version": row.version}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def milestones(doc: dict) -> tuple[list[dict], dict | None]:
    stamps = doc.get("roi", {}).get("stageTimestamps", {})
    status = S(doc["status"])
    out, current_set = [], False
    terminal_cancel = status in (S.CANCELLED, S.REJECTED)
    blocked = category_of(status) == StatusCategory.ACTION_REQUIRED
    next_expected = None
    for key, label, _st in MILESTONES:
        at = stamps.get(MILESTONE_STAGE[key]) or (stamps.get("packed") if key == "PICKED" else None)
        if at:
            out.append({"milestone": key, "label": label, "status": "DONE", "at": at})
        elif terminal_cancel or status == S.CLOSED:
            out.append({"milestone": key, "label": label, "status": "SKIPPED"})
        elif not current_set:
            m = {"milestone": key, "label": label, "status": "CURRENT"}
            if blocked:
                m["blocked"] = True
                m["blockedReason"] = doc.get("statusReason") or status.value
            out.append(m)
            current_set = True
            next_expected = {"milestone": key, "label": label}
        else:
            out.append({"milestone": key, "label": label, "status": "PENDING"})
    if next_expected:
        if next_expected["milestone"] in ("RELEASED", "PICKED", "SHIPPED") and doc.get("dates", {}).get("promisedShipDate"):
            next_expected["by"] = doc["dates"]["promisedShipDate"]
        elif next_expected["milestone"] == "DELIVERED":
            eta = next((x.get("estimatedDeliveryAt") for x in reversed(doc.get("shipments", [])) if x.get("estimatedDeliveryAt")), None)
            next_expected["by"] = eta or doc.get("dates", {}).get("requestedDeliveryDate")
    return out, next_expected


def status_view(db: Database, order_id: str, customer_facing: bool = False) -> dict:
    with db.session() as s:
        row = _row(s, order_id)
        doc = row.doc
        last = s.execute(select(HistoryRow).where(HistoryRow.order_id == order_id)
                         .order_by(desc(HistoryRow.sequence)).limit(1)).scalar_one_or_none()
    ms, nxt = milestones(doc)
    status = S(doc["status"])
    view = {
        "orderId": doc["orderId"], "orderGroupId": doc.get("orderGroupId"), "version": doc.get("version"),
        "customerId": doc["parties"]["soldTo"].get("partyId"), "customerName": doc["parties"]["soldTo"].get("name"),
        "customerPo": doc["purchaseOrder"].get("poNumber"), "shipToId": doc["parties"]["shipTo"].get("partyId"),
        "shipToName": doc["parties"]["shipTo"].get("name"), "sourceChannel": doc["source"]["channel"],
        "status": status.value, "statusCategory": doc["statusCategory"], "statusDescription": STATUS_META[status][3],
        "statusReason": doc.get("statusReason"), "statusUpdatedAt": doc.get("statusUpdatedAt"),
        "terminal": status in TERMINAL,
        "erpSalesOrderId": (doc.get("erp") or {}).get("salesOrderId"),
        "requestedDeliveryDate": doc.get("dates", {}).get("requestedDeliveryDate"),
        "promisedShipDate": doc.get("dates", {}).get("promisedShipDate"),
        "milestones": ms, "nextExpected": nxt,
        "lines": [{"lineNumber": l["lineNumber"], "itemNumber": l["item"].get("itemNumber"),
                   "buyerItemNumber": l["item"].get("buyerItemNumber"), "description": l["item"].get("description"),
                   "uom": l["quantity"].get("uom"), "status": l.get("status"),
                   **{k: l.get("quantities", {}).get(k, "0") for k in
                      ("ordered", "confirmed", "backordered", "shipped", "delivered", "invoiced", "cancelled")},
                   } for l in doc["lines"]],
        "shipments": [{k: sh.get(k) for k in ("shipmentId", "asnNumber", "carrierScac", "carrierName", "trackingNumber",
                                              "billOfLading", "shippedAt", "estimatedDeliveryAt", "deliveredAt", "status")}
                      for sh in doc.get("shipments", [])],
        "invoices": [{k: inv.get(k) for k in ("invoiceId", "invoiceDate", "dueDate", "amount", "currency", "status")}
                     for inv in doc.get("invoices", [])],
        "acknowledgments": [{k: a.get(k) for k in ("type", "status", "controlNumber", "sentAt")}
                            for a in doc.get("acknowledgments", [])],
        "totals": doc.get("totals"),
        "lastEvent": {"eventType": last.event_type, "occurredAt": iso(_aware(last.occurred_at)),
                      "sourceSystem": last.source_system} if last else None,
        "asOf": iso(utcnow()),
    }
    if customer_facing:
        view["openIssues"] = [{"code": e["code"], "message": e["message"]} for e in doc.get("exceptions", [])
                              if e.get("state") == "OPEN"]
    else:
        view["openExceptions"] = [e for e in doc.get("exceptions", []) if e.get("state") == "OPEN"]
        view["roi"] = {k: doc["roi"].get(k) for k in ("touchless", "touchCount", "cycleTimes", "chargebackRisk")}
    return view


def accepted_view(db: Database, order_id: str) -> dict:
    with db.session() as s:
        row = _row(s, order_id)
        doc = row.doc
    return {"orderId": order_id, "orderGroupId": doc.get("orderGroupId"), "version": doc.get("version"),
            "status": doc["status"], "statusCategory": doc["statusCategory"],
            "customerPo": doc["purchaseOrder"].get("poNumber"), "customerId": doc["parties"]["soldTo"].get("partyId"),
            "exceptions": [{k: e.get(k) for k in ("code", "ruleId", "severity", "message", "lineNumber", "field")
                            if e.get(k) is not None} for e in doc.get("exceptions", []) if e.get("state") == "OPEN"],
            "warnings": [{k: r.get(k) for k in ("ruleId", "code", "message", "lineNumber") if r.get(k) is not None}
                         for r in (doc.get("dataQuality") or {}).get("results", [])
                         if not r.get("passed") and r.get("severity") == "WARNING"],
            "totals": doc.get("totals"),
            "statusUrl": f"/v1/orders/{order_id}/status", "historyUrl": f"/v1/orders/{order_id}/history",
            "receivedAt": doc["source"]["receivedAt"], "correlationId": correlation_id_var.get() or None}


SORTABLE = {"receivedAt": OrderRow.received_at, "updatedAt": OrderRow.updated_at, "status": OrderRow.status,
            "customerId": OrderRow.customer_id, "customerPo": OrderRow.customer_po, "totalAmount": OrderRow.total_amount,
            "requestedDeliveryDate": OrderRow.requested_delivery_date, "orderId": OrderRow.order_id}


def search_orders(db: Database, *, status: str | None = None, status_category: str | None = None,
                  customer_id: str | None = None, channel: str | None = None, customer_po: str | None = None,
                  erp_sales_order_id: str | None = None, from_date: date | None = None, to_date: date | None = None,
                  has_exceptions: bool | None = None, q: str | None = None, page: int = 1, page_size: int = 25,
                  sort: str = "-receivedAt") -> dict:
    conds = []
    if status:
        conds.append(OrderRow.status.in_([x.strip().upper() for x in status.split(",")]))
    if status_category:
        conds.append(OrderRow.status_category.in_([x.strip().upper() for x in status_category.split(",")]))
    if customer_id:
        conds.append(OrderRow.customer_id == customer_id)
    if channel:
        conds.append(OrderRow.source_channel.in_([x.strip().upper() for x in channel.split(",")]))
    if customer_po:
        conds.append(OrderRow.customer_po == customer_po)
    if erp_sales_order_id:
        conds.append(OrderRow.erp_sales_order_id == erp_sales_order_id)
    if from_date:
        conds.append(OrderRow.received_at >= datetime.combine(from_date, datetime.min.time(), tzinfo=timezone.utc))
    if to_date:
        conds.append(OrderRow.received_at <= datetime.combine(to_date, datetime.max.time(), tzinfo=timezone.utc))
    if has_exceptions is not None:
        conds.append(OrderRow.open_exception_count > 0 if has_exceptions else OrderRow.open_exception_count == 0)
    if q:
        like = f"%{q}%"
        conds.append(or_(OrderRow.order_id.ilike(like), OrderRow.customer_po.ilike(like),
                         OrderRow.customer_name.ilike(like), OrderRow.erp_sales_order_id.ilike(like),
                         OrderRow.customer_id.ilike(like)))
    key = sort.lstrip("-+")
    col = SORTABLE.get(key)
    if col is None:
        raise ApiError(400, "Invalid sort", f"Sort by one of {sorted(SORTABLE)} (prefix '-' for descending)")
    order = desc(col) if sort.startswith("-") else asc(col)
    page, page_size = max(page, 1), max(1, min(page_size, 200))
    with db.session() as s:
        where = and_(*conds) if conds else True
        total = s.scalar(select(func.count()).select_from(OrderRow).where(where))
        rows = s.execute(select(OrderRow).where(where).order_by(order, OrderRow.order_id)
                         .offset((page - 1) * page_size).limit(page_size)).scalars().all()
        return {"items": [list_item(r) for r in rows], "page": page, "pageSize": page_size, "total": total,
                "totalPages": (total + page_size - 1) // page_size}


def documents(db: Database, order_id: str) -> dict:
    with db.session() as s:
        _row(s, order_id)
        rows = s.execute(select(DocumentRow).where(DocumentRow.order_id == order_id)
                         .order_by(DocumentRow.created_at)).scalars().all()
        return {"orderId": order_id, "items": [
            {"documentId": r.document_id, "documentType": r.document_type, "direction": r.direction,
             "controlNumber": r.control_number, "partner": r.partner, "contentType": r.content_type,
             "createdAt": iso(_aware(r.created_at)), "sizeBytes": len(r.content or "")} for r in rows]}


def document(db: Database, order_id: str, document_id: str) -> dict:
    with db.session() as s:
        r = s.get(DocumentRow, document_id)
        if r is None or r.order_id != order_id:
            raise ApiError(404, "Document not found", document_id)
        return {"documentId": r.document_id, "orderId": r.order_id, "documentType": r.document_type,
                "direction": r.direction, "controlNumber": r.control_number, "partner": r.partner,
                "contentType": r.content_type, "createdAt": iso(_aware(r.created_at)), "content": r.content}


def related(db: Database, order_id: str) -> dict:
    with db.session() as s:
        row = _row(s, order_id)
        group = s.execute(select(OrderRow).where(OrderRow.order_group_id == row.order_group_id)
                          .order_by(OrderRow.received_at)).scalars().all()
        rel = {r["orderId"]: r for r in row.doc.get("relatedOrders", [])}
        orders = []
        for g in group:
            item = list_item(g)
            item["relationType"] = "SELF" if g.order_id == order_id else rel.get(g.order_id, {}).get("relationship", "SAME_GROUP")
            orders.append(item)
        return {"orderId": order_id, "orderGroupId": row.order_group_id, "orders": orders,
                "relatedOrders": row.doc.get("relatedOrders", [])}


def group_history(db: Database, group_id: str) -> dict:
    with db.session() as s:
        rows = s.execute(select(OrderRow).where(OrderRow.order_group_id == group_id)
                         .order_by(OrderRow.received_at)).scalars().all()
        if not rows:
            raise ApiError(404, "Order group not found", group_id)
        cats = {r.status_category for r in rows}
        agg = (StatusCategory.ACTION_REQUIRED.value if StatusCategory.ACTION_REQUIRED.value in cats
               else min(cats, key=lambda c: min(STATUS_META[S(r.status)][1] for r in rows if r.status_category == c)))
        src = rows[0].doc["source"]["sourceDocument"]
        return {"orderGroupId": group_id, "origin": src.get("interchangeControlNumber") or src.get("reference"),
                "orderIds": [r.order_id for r in rows], "aggregateStatusCategory": agg,
                "orders": [list_item(r) for r in rows], "createdAt": iso(_aware(rows[0].received_at)),
                "history": history_entries(s, group_id=group_id)}


def exceptions_queue(db: Database, customer_id: str | None = None, code: str | None = None) -> dict:
    with db.session() as s:
        q = select(OrderRow).where(OrderRow.open_exception_count > 0)
        if customer_id:
            q = q.where(OrderRow.customer_id == customer_id)
        rows = s.execute(q.order_by(OrderRow.received_at)).scalars().all()
        items = []
        for r in rows:
            exs = [e for e in r.doc.get("exceptions", []) if e.get("state") == "OPEN" and (not code or e.get("code") == code)]
            if not exs:
                continue
            items.append({**list_item(r), "exceptions": exs,
                          "ageMinutes": round((utcnow() - _aware(r.received_at)).total_seconds() / 60, 1)})
        by_code: dict[str, int] = {}
        for it in items:
            for e in it["exceptions"]:
                by_code[e["code"]] = by_code.get(e["code"], 0) + 1
        return {"items": items, "total": len(items), "byCode": by_code}


def summary(db: Database) -> dict:
    with db.session() as s:
        def grouped(col):
            return {k: v for k, v in s.execute(select(col, func.count()).group_by(col)).all()}
        total = s.scalar(select(func.count()).select_from(OrderRow)) or 0
        return {"total": total, "byStatus": grouped(OrderRow.status), "byStatusCategory": grouped(OrderRow.status_category),
                "byChannel": grouped(OrderRow.source_channel),
                "openExceptions": s.scalar(select(func.count()).select_from(OrderRow).where(OrderRow.open_exception_count > 0)) or 0,
                "asOf": iso(utcnow())}


def kpis(db: Database, labor_rate_per_hour: float = 32.0) -> dict:
    """Deck slide 13 measures + slide 4 ROI drivers, computed from the ROI tags carried on each order."""
    with db.session() as s:
        rows = s.execute(select(OrderRow.source_channel, OrderRow.touchless, OrderRow.first_pass, OrderRow.erp_created,
                                OrderRow.receipt_to_erp_minutes, OrderRow.receipt_to_ship_hours,
                                OrderRow.open_exception_count, OrderRow.status, OrderRow.doc)).all()
        fresh = s.execute(select(HistoryRow.occurred_at, HistoryRow.recorded_at)
                          .where(HistoryRow.source_system.in_(["D365", "WMS", "TMS"]))
                          .order_by(desc(HistoryRow.recorded_at)).limit(200)).all()

    def agg(rs: list) -> dict:
        reached = [r for r in rs if r.erp_created]
        validated = [r for r in rs if r.first_pass is not None]
        erp_minutes = [r.receipt_to_erp_minutes for r in reached if r.receipt_to_erp_minutes is not None]
        ship_hours = [r.receipt_to_ship_hours for r in rs if r.receipt_to_ship_hours is not None]
        return {"orders": len(rs),
                "touchlessRate": round(sum(1 for r in reached if r.touchless) / len(reached), 4) if reached else None,
                "firstPassValidationRate": round(sum(1 for r in validated if r.first_pass) / len(validated), 4) if validated else None,
                "avgReceiptToErpMinutes": round(sum(erp_minutes) / len(erp_minutes), 2) if erp_minutes else None,
                "avgReceiptToShipHours": round(sum(ship_hours) / len(ship_hours), 2) if ship_hours else None,
                "openExceptions": sum(1 for r in rs if r.open_exception_count > 0)}

    overall = agg(rows)
    by_channel = []
    for ch in sorted({r.source_channel for r in rows}):
        by_channel.append({"channel": ch, **agg([r for r in rows if r.source_channel == ch])})
    lags = [(_aware(r.recorded_at) - _aware(r.occurred_at)).total_seconds() for r in fresh]
    manual_minutes = sum(t.get("minutes", 0) for r in rows for t in r.doc.get("roi", {}).get("manualTouches", []))
    touchless_orders = sum(1 for r in rows if r.touchless and r.erp_created)
    chargeback_flags = sum(len(r.doc.get("roi", {}).get("chargebackRisk", [])) for r in rows)
    reasons: dict[str, int] = {}
    for r in rows:
        for c in r.doc.get("roi", {}).get("exceptionReasonCodes", []):
            reasons[c] = reasons.get(c, 0) + 1
    baseline_minutes = 8.0  # assumed manual handling minutes per non-automated order (baseline for labor ROI)
    return {
        "ordersTotal": overall["orders"], "touchlessRate": overall["touchlessRate"],
        "firstPassValidationRate": overall["firstPassValidationRate"],
        "avgReceiptToErpMinutes": overall["avgReceiptToErpMinutes"], "avgReceiptToShipHours": overall["avgReceiptToShipHours"],
        "openExceptions": overall["openExceptions"],
        "dataFreshnessSeconds": round(sum(lags) / len(lags), 3) if lags else None,
        "manualTouchMinutes": round(manual_minutes, 1),
        "estimatedLaborSavedHours": round(touchless_orders * baseline_minutes / 60, 2),
        "estimatedLaborSavedUsd": round(touchless_orders * baseline_minutes / 60 * labor_rate_per_hour, 2),
        "chargebackRiskFlags": chargeback_flags,
        "exceptionReasons": reasons,
        "byChannel": by_channel,
        "assumptions": {"baselineManualMinutesPerOrder": baseline_minutes, "laborRatePerHourUsd": labor_rate_per_hour},
        "asOf": iso(utcnow()),
    }
