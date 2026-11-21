"""Data marketplace Experience API (/marketplace/v1).

A self-service catalog of the datasets published by the Sales Order data product (and the master data it
depends on). Consumers browse definitions, schema, lineage and refresh SLAs, preview data, request access
and export data. Every retrieval is *fulfilled in the backend by the Process and System APIs* - the response
lists exactly which calls were made (`fulfilledBy`) - and every export is audited by the Process API
governance store.

Access control (docs/07-data-mart-access-contract.md)
  * Domain       - internal users need the Entra app role `Domain.<X>.Read` (or DataMart.Admin) to open a
                   domain's datasets; others see them as locked. External users never see datasets outside
                   their audience.
  * Row          - external customers only get rows for their customer account(s), external vendors only
                   for their vendor account; internal users bound to regions only get rows for those regions.
  * Column       - PII fields are masked unless the user holds PII.Read; sensitive (commercial) fields are
                   masked until access to a Confidential dataset is approved.
  * Policy       - `auto` datasets are open to everyone who can read the domain; `approval` (Confidential)
                   datasets need an APPROVED access request unless the user is a data steward / admin.
  * Export       - needs DataMart.Export (external users may always export their own rows).
"""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field

from ..common.util import iso, parse_dt, utcnow
from ..common.identity import DOMAINS as MART_DOMAINS, Principal
from ..common.web import ApiError, ServiceClient, current_user, forbid
from .catalog import BY_ID, DATASETS, DOMAINS

MAX_EXPORT_ROWS = 50_000
MASK = "•••• (restricted)"


class Fulfilment:
    """Collects the backend API calls made to fulfil one marketplace request."""

    def __init__(self, process: ServiceClient, system: ServiceClient):
        self.process, self.system = process, system
        self.calls: list[str] = []

    async def get(self, api: Literal["process", "system"], path: str, params: dict | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        self.calls.append(f"GET /{api}{path}" + (f"?{qs}" if qs else ""))
        client = self.process if api == "process" else self.system
        return await client.get_json(path, params)


# ------------------------------------------------------------------------------------------- row shaping
def _flat(v: Any) -> Any:
    if isinstance(v, list):
        return "|".join(str(_flat(x)) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, separators=(",", ":"))
    return v


def _project(rows: list[dict], schema: list[dict], fields: list[str] | None) -> tuple[list[str], list[dict]]:
    cols = [c["name"] for c in schema]
    if fields:
        unknown = [x for x in fields if x not in cols]
        if unknown:
            raise ApiError(400, "Unknown fields", f"{', '.join(unknown)} not in the dataset schema")
        cols = [c for c in cols if c in fields]
    return cols, [{c: r.get(c) for c in cols} for r in rows]


async def _orders(ff: Fulfilment, filters: dict, limit: int, full: bool = False) -> list[dict]:
    out, page = [], 1
    params = {k: filters.get(k) for k in ("status", "statusCategory", "channel", "customerId", "fromDate", "toDate",
                                          "region", "accountScope")}
    while len(out) < limit:
        res = await ff.get("process", "/v1/orders", {**params, "page": page, "pageSize": 200,
                                                     "full": "true" if full else None})
        out.extend(res["items"])
        if page >= res.get("totalPages", 1):
            break
        page += 1
    return out


async def fetch_rows(ds: dict, ff: Fulfilment, filters: dict, limit: int, newest_first: bool = False) -> list[dict]:
    i = ds["id"]
    if i == "sales-orders":
        return (await _orders(ff, filters, limit))[:limit]

    if i in ("sales-order-lines", "shipments", "invoices"):
        rows: list[dict] = []
        for it in await _orders(ff, filters, limit if i == "sales-order-lines" else MAX_EXPORT_ROWS, full=True):
            o = it["order"]
            base = {"orderId": o["orderId"], "customerId": it.get("customerId"), "customerPo": it.get("customerPo"),
                    "region": it.get("region"), "shipFromWarehouse": it.get("shipFromWarehouse")}
            lines = {ln["lineNumber"]: ln for ln in o.get("lines", [])}
            if i == "sales-order-lines":
                for ln in o.get("lines", []):
                    q, pr, item = ln.get("quantities", {}), ln.get("unitPrice") or {}, ln.get("item", {})
                    rows.append({**base, "lineNumber": ln["lineNumber"], "orderStatus": o["status"],
                                 "itemNumber": item.get("itemNumber"), "buyerItemNumber": item.get("buyerItemNumber"),
                                 "gtin": item.get("gtin"), "description": item.get("description"),
                                 "uom": ln.get("quantity", {}).get("uom"),
                                 **{k: q.get(k, "0") for k in ("ordered", "confirmed", "backordered", "shipped", "delivered", "invoiced")},
                                 "unitPrice": pr.get("amount"), "priceSource": pr.get("source"),
                                 "agreementId": pr.get("agreementId"), "lineAmount": ln.get("lineAmount"),
                                 "lineStatus": ln.get("status"), "requestedDeliveryDate": ln.get("requestedDeliveryDate")})
            elif i == "shipments":
                for sh in o.get("shipments", []):
                    for sl in sh.get("lines", []):
                        for lot in sl.get("lots") or [{}]:
                            rows.append({**base, "shipmentId": sh.get("shipmentId"), "lineNumber": sl.get("lineNumber"),
                                         "lotNumber": lot.get("lotNumber"), "asnNumber": sh.get("asnNumber"),
                                         "carrierScac": sh.get("carrierScac"), "carrierName": sh.get("carrierName"),
                                         "trackingNumber": sh.get("trackingNumber"), "billOfLading": sh.get("billOfLading"),
                                         "shippedAt": sh.get("shippedAt"), "deliveredAt": sh.get("deliveredAt"),
                                         "shipmentStatus": sh.get("status"), "itemNumber": sl.get("itemNumber"),
                                         "quantity": lot.get("quantity") or sl.get("quantity"), "uom": sl.get("uom"),
                                         "traceabilityLotCode": lot.get("traceabilityLotCode"), "packDate": lot.get("packDate"),
                                         "bestByDate": lot.get("bestByDate"), "countryOfOrigin": lot.get("countryOfOrigin")})
            else:
                for inv in o.get("invoices", []):
                    for il in inv.get("lines") or [{}]:
                        rows.append({**base, "invoiceId": inv.get("invoiceId"), "lineNumber": il.get("lineNumber"),
                                     "erpSalesOrderId": (o.get("erp") or {}).get("salesOrderId"),
                                     "invoiceDate": inv.get("invoiceDate"), "dueDate": inv.get("dueDate"),
                                     "quantity": il.get("quantity"), "unitPrice": il.get("unitPrice"),
                                     "lineAmount": il.get("amount"), "amount": inv.get("amount"),
                                     "currency": inv.get("currency"), "invoiceStatus": inv.get("status"),
                                     "itemNumber": (lines.get(il.get("lineNumber")) or {}).get("item", {}).get("itemNumber")})
            if len(rows) >= limit:
                break
        return rows[:limit]

    if i == "order-lifecycle-events":
        params = {"eventType": filters.get("eventType"), "sourceSystem": filters.get("sourceSystem"),
                  "orderId": filters.get("orderId"), "since": filters.get("since"), "region": filters.get("region"),
                  "accountScope": filters.get("accountScope")}
        page_size = min(1000, max(limit, 1))
        res = await ff.get("process", "/v1/order-events", {**params, "page": 1, "pageSize": page_size})
        entries = list(res["items"])
        if newest_first and res.get("totalPages", 1) > 1:  # previews show the latest activity
            res = await ff.get("process", "/v1/order-events", {**params, "page": res["totalPages"], "pageSize": page_size})
            entries = list(res["items"])
        else:
            page = 2
            while len(entries) < limit and page <= res.get("totalPages", 1):
                entries.extend((await ff.get("process", "/v1/order-events", {**params, "page": page, "pageSize": page_size}))["items"])
                page += 1
        if newest_first:
            entries.reverse()
        return [{**{k: e.get(k) for k in ("eventId", "orderId", "orderGroupId", "sequence", "eventType", "fromStatus",
                                           "toStatus", "statusCategory", "tier", "sourceSystem", "occurredAt", "recordedAt",
                                           "elapsedSincePreviousSeconds", "reasonCode", "message")},
                 "actorType": (e.get("actor") or {}).get("type"), "actorId": (e.get("actor") or {}).get("id"),
                 "changeCount": len(e.get("changes") or [])} for e in entries[:limit]]

    if i == "order-exceptions":
        res = await ff.get("process", "/v1/exceptions", {"customerId": filters.get("customerId"), "code": filters.get("code"),
                                                          "region": filters.get("region"), "accountScope": filters.get("accountScope")})
        return [{"exceptionId": ex.get("exceptionId"), "orderId": it["orderId"], "customerId": it.get("customerId"),
                 "customerName": it.get("customerName"), "customerPo": it.get("customerPo"),
                 "sourceChannel": it.get("sourceChannel"), **{k: ex.get(k) for k in ("code", "ruleId", "severity", "message",
                                                                                    "lineNumber", "field", "detectedAt")},
                 "ageMinutes": it.get("ageMinutes"), "region": it.get("region")}
                for it in res["items"] for ex in it["exceptions"]][:limit]

    if i == "regional-performance":
        k = await ff.get("process", "/v1/metrics/kpis", {"region": filters.get("region"), "accountScope": filters.get("accountScope")})
        level = (filters.get("level") or ("SITE" if filters.get("region") else "REGION")).upper()
        src = k["bySite"] if level == "SITE" else k["byRegion"]
        return [{**r, "level": level, "key": r.get("site") or r.get("region"),
                 "name": r.get("siteName") or r.get("regionName")} for r in src][:limit]

    if i == "orders-at-risk":
        res = await ff.get("process", "/v1/orders/at-risk", {"region": filters.get("region"), "accountScope": filters.get("accountScope")})
        return [{**{k: it.get(k) for k in ("orderId", "severity", "customerId", "customerName", "customerPo", "region",
                                           "shipFromWarehouse", "accountScope", "status", "requestedDeliveryDate",
                                           "promisedShipDate", "totalAmount")},
                 "reasonCode": r["code"], "reason": r["message"]} for it in res["items"] for r in it["reasons"]][:limit]

    if i == "sites-inventory":
        res = await ff.get("system", "/v1/masterdata/inventory", {"region": filters.get("region"), "itemNumber": filters.get("itemNumber")})
        return res["items"][:limit]

    if i == "order-kpis":
        return (await ff.get("process", "/v1/metrics/kpis"))["byChannel"][:limit]

    if i == "customers":
        active = filters.get("active")
        res = await ff.get("system", "/v1/masterdata/customers", {"active": active})
        out = []
        for c in res["items"]:
            ct = (c.get("contacts") or [{}])[0]
            out.append({**{k: c.get(k) for k in ("customerId", "name", "active", "erpAccount", "invoiceAccount", "segment",
                                                 "channels", "currency", "priceGroup", "creditLimit", "openAr", "gln", "duns")},
                        "paymentTermsCode": (c.get("paymentTerms") or {}).get("code"),
                        "contactName": ct.get("name"), "contactEmail": ct.get("email"), "contactPhone": ct.get("phone")})
        return out[:limit]

    if i == "ship-tos":
        res = await ff.get("system", "/v1/masterdata/ship-tos", {"customerId": filters.get("customerId")})
        return [{**{k: s.get(k) for k in ("shipToId", "customerId", "name", "active", "erpLocationId", "ediLocationCode",
                                          "gln", "transitDays", "deliveryDays", "receivingHours", "site", "warehouse",
                                          "temperatureZone")}, **(s.get("address") or {})} for s in res["items"]][:limit]

    if i == "items":
        return (await ff.get("system", "/v1/masterdata/items", {"active": filters.get("active")}))["items"][:limit]

    if i == "price-agreements":
        return (await ff.get("system", "/v1/masterdata/price-agreements", {"customerId": filters.get("customerId")}))["items"][:limit]

    if i == "trading-partners":
        res = await ff.get("system", "/v1/masterdata/trading-partners")
        return [{**p, "ackSla855Minutes": (p.get("ackSlaMinutes") or {}).get("855"),
                 "ackSla856Minutes": (p.get("ackSlaMinutes") or {}).get("856")} for p in res["items"]][:limit]

    if ds.get("martDataset"):
        res = await ff.get("system", f"/v1/mart/{ds['martDataset']}", {**filters, "limit": limit})
        return res["items"][:limit]

    raise ApiError(404, "Dataset not found", i)


# ------------------------------------------------------------------------------------------- access control
REGION_FIELDS = ("region", "originRegion", "homeRegion")


def visible(ds: dict, user: Principal) -> bool:
    """External users only ever see the datasets published to their audience."""
    if user.is_external_customer:
        return "external-customer" in ds["audiences"]
    if user.is_external_vendor:
        return "external-vendor" in ds["audiences"]
    return not user.is_external


def locked_reason(ds: dict, user: Principal) -> str | None:
    if not visible(ds, user):
        return "Not published to your organisation"
    if user.is_external or user.is_service or user.can_read_domain(ds["domainId"]):
        return None
    role = next(d["role"] for d in MART_DOMAINS if d["domainId"] == ds["domainId"])
    return f"Requires the {role} role ({ds['domain']} domain). Ask a data steward to add you to the Entra ID group."


def scope_filters(ds: dict, user: Principal, filters: dict) -> dict:
    """Force row-level filters onto the backend query (defence in depth: rows are re-checked by `row_allowed`)."""
    out = dict(filters)
    rs = ds.get("rowScope") or {}
    if user.is_external_customer and "customer" in rs:
        asked = out.get(rs["customer"])
        ids = [c for c in user.customer_ids if not asked or c == asked] or ["__NO_ACCESS__"]
        out[rs["customer"]] = ids[0] if ds["id"] in ("sales-orders", "sales-order-lines", "shipments", "invoices") else ",".join(ids)
    if user.is_external_vendor and "vendor" in rs:
        asked = out.get(rs["vendor"])
        ids = [v for v in user.vendor_ids if not asked or v == asked] or ["__NO_ACCESS__"]
        out[rs["vendor"]] = ",".join(ids)
    if user.regions and not user.is_external and any(fl["name"] == "region" for fl in ds.get("filters", [])):
        asked = [r.strip().upper() for r in (out.get("region") or "").split(",") if r.strip()]
        allowed = [r for r in (asked or user.regions) if r in user.regions] or ["__NO_ACCESS__"]
        out["region"] = ",".join(allowed)
    return out


def row_allowed(ds: dict, user: Principal, row: dict) -> bool:
    rs = ds.get("rowScope") or {}
    if user.is_external_customer and "customer" in rs and row.get(rs["customer"]) not in user.customer_ids:
        return False
    if user.is_external_vendor and "vendor" in rs and row.get(rs["vendor"]) not in user.vendor_ids:
        return False
    if user.regions and not user.is_external:
        for fld in REGION_FIELDS:
            if fld in row and row[fld] and row[fld] not in user.regions:
                return False
    return True


# ------------------------------------------------------------------------------------------- serialisers
def to_csv(cols: list[str], rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: _flat(v) for k, v in r.items()})
    return buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly


def to_xlsx(cols: list[str], rows: list[dict], ds: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = ds["id"][:31]
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([_flat(r.get(c)) for c in cols])
    ws.freeze_panes = "A2"
    meta = wb.create_sheet("About")
    for k, v in (("Dataset", ds["name"]), ("Id", ds["id"]), ("Version", ds["version"]), ("Owner", ds["owner"]["name"]),
                 ("Classification", ds["classification"]), ("Refresh", ds["refresh"]["frequency"]),
                 ("Exported at", iso(utcnow())), ("Rows", len(rows))):
        meta.append([k, v])
    meta.append([])
    meta.append(["Field", "Type", "Description"])
    for fdef in ds["schema"]:
        if fdef["name"] in cols:
            meta.append([fdef["name"], fdef["type"], fdef["description"]])
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


FORMATS = {"csv": ("text/csv; charset=utf-8", "csv"), "json": ("application/json", "json"),
           "ndjson": ("application/x-ndjson", "ndjson"),
           "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")}


# ------------------------------------------------------------------------------------------- router
class AccessRequestIn(BaseModel):
    purpose: str = Field(min_length=3, max_length=2000)
    team: str | None = None
    consumerSystem: str | None = None
    accessLevel: Literal["READ", "EXPORT", "SUBSCRIBE"] = "EXPORT"


class DecisionIn(BaseModel):
    decision: Literal["APPROVED", "REJECTED", "REVOKED"]
    note: str | None = None


def build_router(process: ServiceClient, system: ServiceClient) -> APIRouter:
    r = APIRouter(prefix="/marketplace/v1", tags=["data marketplace"])
    cache: dict[str, tuple[float, Any]] = {}

    def who(request: Request) -> str:
        user = current_user(request)
        if user.is_service:  # integration callers may act on behalf of a named user
            return (request.headers.get("x-user-id") or "service").strip()
        return (user.email or user.id).strip()

    def guard(ds: dict, user: Principal) -> None:
        if not visible(ds, user):
            raise ApiError(404, "Dataset not found", f"No dataset '{ds['id']}' in the catalog")
        reason = locked_reason(ds, user)
        if reason:
            raise forbid(reason)

    def dataset(ds_id: str) -> dict:
        ds = BY_ID.get(ds_id)
        if not ds:
            raise ApiError(404, "Dataset not found", f"No dataset '{ds_id}' in the catalog")
        return ds

    async def cached(key: str, ttl: float, fn):
        hit = cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        cache[key] = (time.time(), val)
        return val

    async def live_facts() -> dict[str, dict]:
        """Record counts, last refresh and pipeline health for every dataset (cached 15 s)."""
        async def compute():
            ff = Fulfilment(process, system)
            facts: dict[str, dict] = {}
            stats = await ff.get("process", "/v1/datasets/stats")
            health = await process.get_json("/health")
            pipeline_ok = health.get("outbox", {}).get("retrying", 0) == 0
            orders = await _orders(ff, {}, MAX_EXPORT_ROWS, full=True)
            n_lines = sum(len(it["order"].get("lines", [])) for it in orders)
            n_ship = sum(len(it["order"].get("shipments", [])) for it in orders)
            n_inv = sum(len(it["order"].get("invoices", [])) for it in orders)
            last_ship = max((sh.get("shippedAt") or "" for it in orders for sh in it["order"].get("shipments", [])), default=None)
            last_inv = max((inv.get("invoiceDate") or "" for it in orders for inv in it["order"].get("invoices", [])), default=None)
            exc = await ff.get("process", "/v1/exceptions")
            usage = (await ff.get("process", "/v1/governance/usage"))["items"]
            now = iso(utcnow())
            order_ts = stats.get("ordersLastUpdatedAt")
            counts = {"sales-orders": (stats["orders"], order_ts), "sales-order-lines": (n_lines, order_ts),
                      "order-lifecycle-events": (stats["historyEvents"], stats.get("historyLastRecordedAt")),
                      "order-exceptions": (sum(len(i["exceptions"]) for i in exc["items"]), order_ts),
                      "shipments": (n_ship, last_ship or None), "invoices": (n_inv, last_inv or None),
                      "order-kpis": (len({it.get("sourceChannel") for it in orders}), now),
                      "regional-performance": (len({it.get("region") for it in orders if it.get("region")}), now)}
            risk = await ff.get("process", "/v1/orders/at-risk")
            counts["orders-at-risk"] = (sum(len(x["reasons"]) for x in risk["items"]), now)
            for name, path in (("customers", "/v1/masterdata/customers"), ("ship-tos", "/v1/masterdata/ship-tos"),
                               ("items", "/v1/masterdata/items"), ("price-agreements", "/v1/masterdata/price-agreements"),
                               ("trading-partners", "/v1/masterdata/trading-partners"),
                               ("sites-inventory", "/v1/masterdata/inventory")):
                counts[name] = (len((await ff.get("system", path))["items"]), now)
            for m in (await ff.get("system", "/v1/mart"))["items"]:
                counts[m["dataset"]] = (m["rows"], m.get("asOf") or now)
            for ds in DATASETS:
                n, last = counts.get(ds["id"], (None, None))
                event_driven = ds["refresh"]["mode"].startswith("Event")
                status = "ON_TIME" if (pipeline_ok or not event_driven) else "DELAYED"
                facts[ds["id"]] = {"recordCount": n, "lastDataChangeAt": last, "lastRefreshedAt": now if not event_driven else (last or now),
                                   "freshnessStatus": status,
                                   "freshnessDetail": ("Outbox has messages retrying - downstream copies may lag"
                                                       if status == "DELAYED" else "Pipeline healthy; within SLA"),
                                   "usage": usage.get(ds["id"], {}), "factsAsOf": now}
            return {"facts": facts, "fulfilledBy": ff.calls}
        return await cached("facts", 15, compute)

    def card(ds: dict, facts: dict, user: Principal) -> dict:
        reason = locked_reason(ds, user)
        live = dict(facts.get(ds["id"], {}))
        if reason or user.is_external or (user.regions and not user.is_service):
            live["recordCount"] = None  # totals across other accounts / regions are not disclosed
            live.pop("usage", None)
        return {k: ds[k] for k in ("id", "name", "object", "domain", "domainId", "type", "version", "classification",
                                   "accessPolicy", "tags", "summary", "owner", "refresh", "grain", "primaryKey",
                                   "audiences", "rowScope")} | {
            "certified": ds.get("certified", False), "fieldCount": len(ds["schema"]),
            "piiFieldCount": sum(1 for fd in ds["schema"] if fd["pii"]), "accessible": reason is None,
            "lockedReason": reason, "rowScoped": bool(scope_note(ds, user)), "rowScopeNote": scope_note(ds, user), **live}

    def scope_note(ds: dict, user: Principal) -> str | None:
        rs = ds.get("rowScope") or {}
        if user.is_external_customer and "customer" in rs:
            return f"Rows limited to your account ({', '.join(user.customer_ids)})"
        if user.is_external_vendor and "vendor" in rs:
            return f"Rows limited to your vendor account ({', '.join(user.vendor_ids)})"
        if user.regions and not user.is_external and not user.is_service and \
                any(fl["name"] == "region" for fl in ds.get("filters", [])):
            return f"Rows limited to your regions ({', '.join(user.regions)})"
        return None

    async def has_approval(ds: dict, requester: str, user: Principal | None = None) -> bool:
        if ds["accessPolicy"] == "auto":
            return True
        if user is not None and user.is_service and requester == "service":
            return True  # integration identity acting as itself
        if user is not None and not user.is_service and (user.is_steward or user.is_external):
            return True  # stewards/admins by role; external users only ever receive their own rows
        res = await process.get_json("/v1/governance/access-requests",
                                     {"datasetId": ds["id"], "requester": requester, "status": "APPROVED"})
        now = utcnow()
        return any(not x.get("expiresAt") or parse_dt(x["expiresAt"]) > now for x in res["items"])

    def masked(ds: dict, user: Principal, approved: bool) -> set[str]:
        pii = {fd["name"] for fd in ds["schema"] if fd["pii"]}
        if user.is_service:  # acting for a named user: approval governs both PII and sensitive fields
            return set() if approved else pii | set(ds.get("sensitiveFields", []))
        hide = set() if user.pii_access else pii
        if not approved:
            hide |= set(ds.get("sensitiveFields", []))
        return hide

    def filters_of(ds: dict, request: Request) -> dict:
        allowed = {fl["name"] for fl in ds.get("filters", [])}
        return {k: v for k, v in request.query_params.items() if k in allowed and v != ""}

    # ---------------------------------------------------------------- catalog
    @r.get("/datasets", summary="Browse the catalog (search, domain, classification, type)")
    async def list_datasets(request: Request, q: str | None = None, domain: str | None = None,
                            classification: str | None = None, type: str | None = None, tag: str | None = None,
                            accessible: bool | None = None):
        user = current_user(request)
        facts = await live_facts()
        items = []
        for ds in DATASETS:
            if not visible(ds, user):
                continue
            text = " ".join([ds["name"], ds["object"], ds["summary"], ds["description"], " ".join(ds["tags"]),
                             " ".join(fd["name"] for fd in ds["schema"])]).lower()
            if q and not all(t in text for t in q.lower().split()):
                continue
            if domain and domain.lower() not in (ds["domain"].lower(), ds["domainId"]):
                continue
            if classification and ds["classification"].lower() != classification.lower():
                continue
            if type and type.lower() not in ds["type"].lower():
                continue
            if tag and tag not in ds["tags"]:
                continue
            c = card(ds, facts["facts"], user)
            if accessible is not None and c["accessible"] != accessible:
                continue
            items.append(c)
        seen = [d for d in DATASETS if visible(d, user)]
        return {"items": items, "total": len(items), "domains": sorted({d["domain"] for d in seen}),
                "classifications": sorted({d["classification"] for d in seen}),
                "types": sorted({d["type"] for d in seen}),
                "fulfilledBy": facts["fulfilledBy"] if not user.is_external else []}

    @r.get("/datasets/{ds_id}", summary="Dataset card: definition, owner, SLA, schema, lineage, consumption, live facts")
    async def get_dataset(ds_id: str, request: Request):
        ds = dataset(ds_id)
        user = current_user(request)
        if not visible(ds, user):
            raise ApiError(404, "Dataset not found", f"No dataset '{ds_id}' in the catalog")
        facts = await live_facts()
        me = who(request)
        mine = await process.get_json("/v1/governance/access-requests", {"datasetId": ds_id, "requester": me})
        c = card(ds, facts["facts"], user)
        approved = c["accessible"] and await has_approval(ds, me, user)
        out = {**ds, **c, "requester": me,
               "myAccess": {"accessible": c["accessible"], "lockedReason": c["lockedReason"], "approved": approved,
                            "canExport": approved and user.can_export,
                            "exportBlockedReason": None if user.can_export else "Exporting needs the DataMart.Export role",
                            "piiAccess": user.pii_access, "requests": mine["items"]},
               "maskedFields": sorted(masked(ds, user, approved)),
               "formats": list(FORMATS), "maxExportRows": MAX_EXPORT_ROWS}
        if user.is_external:
            out.pop("lineage", None)
            out.pop("consumers", None)
            out["owner"] = {"name": ds["owner"]["name"], "team": ds["owner"]["team"]}
        return out

    @r.get("/datasets/{ds_id}/schema", summary="Data definition (fields, types, keys, PII flags)")
    async def get_schema(ds_id: str, request: Request):
        ds = dataset(ds_id)
        if not visible(ds, current_user(request)):
            raise ApiError(404, "Dataset not found", ds_id)
        return {"datasetId": ds_id, "version": ds["version"], "primaryKey": ds["primaryKey"], "grain": ds["grain"],
                "fields": ds["schema"], "schemaRef": ds.get("consumption", {}).get("schemaRef")}

    @r.get("/datasets/{ds_id}/lineage", summary="Upstream sources and downstream consumers")
    async def get_lineage(ds_id: str, request: Request):
        ds = dataset(ds_id)
        user = current_user(request)
        if not visible(ds, user) or user.is_external:
            raise ApiError(404, "Dataset not found", ds_id)
        return {"datasetId": ds_id, **ds["lineage"]}

    # ---------------------------------------------------------------- data access
    @r.get("/datasets/{ds_id}/preview", summary="Sample rows (PII masked without approval)")
    async def preview(ds_id: str, request: Request, limit: int = Query(20, ge=1, le=200), fields: str | None = None):
        ds = dataset(ds_id)
        user = current_user(request)
        guard(ds, user)
        ff = Fulfilment(process, system)
        flt = filters_of(ds, request)
        rows = await fetch_rows(ds, ff, scope_filters(ds, user, flt), limit, newest_first=True)
        rows = [x for x in rows if row_allowed(ds, user, x)]
        cols, rows = _project(rows, ds["schema"], fields.split(",") if fields else None)
        hide = masked(ds, user, await has_approval(ds, who(request), user))
        if hide:
            rows = [{k: (MASK if k in hide and v not in (None, "") else v) for k, v in row.items()} for row in rows]
        return {"datasetId": ds_id, "columns": cols, "rows": rows, "rowCount": len(rows), "filters": flt,
                "maskedFields": sorted(hide & set(cols)), "rowScopeNote": scope_note(ds, user),
                "fulfilledBy": ff.calls if not user.is_external else []}

    @r.get("/datasets/{ds_id}/export", summary="Retrieve / export the dataset as CSV, JSON, NDJSON or Excel",
           response_class=Response)
    async def export(ds_id: str, request: Request, format: Literal["csv", "json", "ndjson", "xlsx"] = "csv",
                     limit: int = Query(10_000, ge=1, le=MAX_EXPORT_ROWS), fields: str | None = None):
        ds = dataset(ds_id)
        user = current_user(request)
        guard(ds, user)
        if not user.can_export:
            raise forbid("Exporting data needs the DataMart.Export role")
        me = who(request)
        if not await has_approval(ds, me, user):
            raise ApiError(403, "Access approval required",
                           f"'{ds['name']}' is {ds['classification']}. Request access from the dataset page; "
                           f"the owner ({ds['owner']['email']}) must approve it before {me} can export.")
        ff = Fulfilment(process, system)
        flt = filters_of(ds, request)
        rows = await fetch_rows(ds, ff, scope_filters(ds, user, flt), limit)
        rows = [x for x in rows if row_allowed(ds, user, x)]
        cols, rows = _project(rows, ds["schema"], fields.split(",") if fields else None)
        hide = masked(ds, user, True)
        if hide:
            rows = [{k: (MASK if k in hide and v not in (None, "") else v) for k, v in row.items()} for row in rows]
        if format == "csv":
            body = to_csv(cols, rows)
        elif format == "xlsx":
            body = to_xlsx(cols, rows, ds)
        elif format == "ndjson":
            body = "".join(json.dumps(x, default=str) + "\n" for x in rows).encode()
        else:
            body = json.dumps({"dataset": {"id": ds_id, "name": ds["name"], "version": ds["version"],
                                           "exportedAt": iso(utcnow()), "filters": flt, "rowCount": len(rows)},
                               "columns": cols, "rows": rows}, default=str, indent=2).encode()
        audit = await process.post_json("/v1/governance/exports", {
            "datasetId": ds_id, "requester": me, "format": format, "filters": flt, "fields": cols,
            "rowCount": len(rows), "bytes": len(body), "fulfilledBy": ff.calls})
        ctype, ext = FORMATS[format]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return Response(content=body, media_type=ctype, headers={
            "Content-Disposition": f'attachment; filename="{ds_id}-{stamp}.{ext}"',
            "x-tfso-row-count": str(len(rows)), "x-tfso-export-id": audit["exportId"],
            "x-tfso-fulfilled-by": " | ".join(ff.calls)[:4000],
            "Access-Control-Expose-Headers": "Content-Disposition, x-tfso-row-count, x-tfso-export-id, x-tfso-fulfilled-by"})

    # ---------------------------------------------------------------- governance
    @r.post("/datasets/{ds_id}/access-requests", status_code=201, summary="Request access to a dataset")
    async def request_access(ds_id: str, body: AccessRequestIn, request: Request):
        ds = dataset(ds_id)
        user = current_user(request)
        if not visible(ds, user):
            raise ApiError(404, "Dataset not found", ds_id)
        if user.is_external:
            raise forbid("Your organisation's access is managed by your Taylor Farms account team")
        return await process.post_json("/v1/governance/access-requests", {
            "datasetId": ds_id, "requester": who(request), "team": body.team, "purpose": body.purpose,
            "consumerSystem": body.consumerSystem, "accessLevel": body.accessLevel,
            "autoApprove": ds["accessPolicy"] == "auto" and locked_reason(ds, user) is None})

    @r.get("/access-requests", summary="Access requests (filter by dataset, requester, status)")
    async def list_requests(request: Request, datasetId: str | None = None, requester: str | None = None,
                            status: str | None = None):
        user = current_user(request)
        if not user.is_steward:  # everyone else only sees their own requests
            requester = who(request)
        return await process.get_json("/v1/governance/access-requests",
                                      {"datasetId": datasetId, "requester": requester, "status": status})

    @r.post("/access-requests/{request_id}/decision", summary="Data owner approves / rejects / revokes access")
    async def decide(request_id: str, body: DecisionIn, request: Request):
        if not current_user(request).is_steward:
            raise forbid("Only data stewards (DataMart.Steward) or admins can decide access requests")
        return await process.post_json(f"/v1/governance/access-requests/{request_id}/decision",
                                       {"decision": body.decision, "decidedBy": who(request), "note": body.note})

    @r.get("/exports", summary="Export audit trail")
    async def exports(request: Request, datasetId: str | None = None, requester: str | None = None):
        if not current_user(request).is_steward:
            requester = who(request)
        return await process.get_json("/v1/governance/exports", {"datasetId": datasetId, "requester": requester})

    @r.get("/domains", summary="Domains with dataset counts")
    async def domains(request: Request):
        user = current_user(request)
        items = []
        for d in MART_DOMAINS:
            mine = [x for x in DATASETS if x["domainId"] == d["domainId"] and visible(x, user)]
            if not mine:
                continue
            open_ = [x for x in mine if locked_reason(x, user) is None]
            items.append({**d, "domain": d["name"], "datasets": len(mine), "accessibleDatasets": len(open_),
                          "accessible": bool(open_), "confidentialDatasets": sum(1 for x in mine if x["classification"] == "Confidential"),
                          "objects": sorted({x["object"] for x in mine})})
        return {"items": items}

    return r
