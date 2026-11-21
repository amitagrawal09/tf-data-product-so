"""Experience APIs (Tier 2, API-led 'Experience' layer): one per channel, all over the shared Process API.

* Portal / manual entry : POST /portal/v1/orders (customers and customer-service staff share the same form/rules)
* Partner API           : POST /partner/v1/orders (system-to-system, canonical draft)
* EDI                   : POST /edi/v1/x12/850    (decoded X12 from the trading-partner gateway; returns the 997)
* E-mail                : POST /email/v1/orders   (message + attachments from the monitored inbox)
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any, Literal

from fastapi import Body, Depends, FastAPI, Header, Query, Request
from pydantic import BaseModel, Field

from ..common import x12
from ..common.config import Settings, get_settings
from ..common.util import group_id as new_group_id, iso, utcnow
from ..common.identity import DOMAINS, PERSONAS, ROLES
from ..common.web import ApiError, ServiceClient, create_app, current_user, forbid
from .extract import extract_email
from .marketplace import build_router

BEG_TO_PURPOSE = x12.BEG_PURPOSE


class Contact(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class PortalLine(BaseModel):
    itemNumber: str
    quantity: float = Field(gt=0)
    uom: str = "CA"
    unitPrice: float | None = None
    notes: str | None = None


class PortalOrderRequest(BaseModel):
    channel: Literal["PORTAL", "MANUAL"] = "PORTAL"
    customerId: str
    shipToId: str
    customerPo: str = Field(min_length=1, max_length=40)
    requestedDeliveryDate: date
    orderType: Literal["STANDARD", "RUSH", "STANDING", "SAMPLE", "REPLACEMENT"] = "STANDARD"
    currency: str | None = None
    notes: str | None = None
    contact: Contact | None = None
    enteredBy: str | None = None
    lines: list[PortalLine] = Field(min_length=1, max_length=500)


class EmailAttachment(BaseModel):
    fileName: str
    contentType: str | None = None
    contentBase64: str


class EmailMessage(BaseModel):
    messageId: str | None = None
    from_: str = Field(alias="from")
    to: str | None = None
    subject: str | None = None
    receivedAt: str | None = None
    body: str | None = None
    attachments: list[EmailAttachment] = Field(default_factory=list)


def create_experience_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings("experience")
    app = create_app(settings, "Sales Order - Experience APIs",
                     "Channel-facing APIs for EDI, e-mail, customer portal / manual entry and partner orders. "
                     "Each channel has its own experience API over the shared Order Intake and Order Status process APIs.")
    process = ServiceClient(settings.process_url, settings)
    system = ServiceClient(settings.system_url, settings)

    # ------------------------------------------------------------------ authorisation (docs/07 §6)
    def authorize(request: Request) -> None:
        user = current_user(request)
        if user.is_service:
            return
        path, method = request.url.path, request.method
        if path.startswith(("/auth/v1/", "/marketplace/v1/")):
            return  # every signed-in user; datasets are authorised individually
        if path.startswith("/admin/v1/"):
            if not user.is_steward:
                raise forbid("Access administration requires DataMart.Admin or DataMart.Steward")
            return
        if path.startswith("/portal/v1/"):
            if user.is_external_customer:
                return  # row-level checks in each route
            if user.is_external:
                raise forbid("The customer portal is for customers")
            if method == "POST" and not user.can_operate_orders:
                raise forbid("Entering orders requires the Orders.Operate role")
            if method == "GET" and not (user.can_read_orders or user.can_operate_orders):
                raise forbid("Viewing orders requires the Orders.Read role")
            return
        # channel APIs (partner, EDI, e-mail): integration identities, or internal order operators
        if user.is_external or (method != "GET" and not user.can_operate_orders) or \
                (method == "GET" and not user.can_read_orders):
            raise forbid("Channel APIs are for integration partners and order operators")

    app.router.dependencies.append(Depends(authorize))
    app.include_router(build_router(process, system))

    def enforce_customer(request: Request, customer_id: str | None) -> None:
        """Row-level security for external customers: only their own customer accounts."""
        user = current_user(request)
        if user.is_external_customer and (not customer_id or customer_id not in user.customer_ids):
            raise forbid("You can only access your own orders")

    # ------------------------------------------------------------------ identity
    @app.get("/auth/v1/me", tags=["identity"], summary="The signed-in principal: roles, domains and row scope")
    async def me(request: Request):
        from .catalog import DATASETS
        user = current_user(request)
        ext = []
        if user.is_external_customer:
            ext += [d["domainId"] for d in DATASETS if "external-customer" in d["audiences"]]
        if user.is_external_vendor:
            ext += [d["domainId"] for d in DATASETS if "external-vendor" in d["audiences"]]
        return user.to_dict(external_domains=sorted(set(ext)))

    @app.get("/auth/v1/personas", tags=["identity"], summary="Development sign-in personas (dev mode only)")
    async def personas():
        if settings.auth_mode != "dev":
            raise ApiError(404, "Not Found", "Personas are only available in development sign-in mode")
        return {"items": PERSONAS}

    @app.get("/admin/v1/roles", tags=["administration"], summary="App role catalogue")
    async def roles():
        return {"items": ROLES}

    @app.get("/admin/v1/role-matrix", tags=["administration"], summary="Which roles read which domains")
    async def role_matrix():
        matrix = []
        for r in ROLES:
            row = {"role": r["role"], "kind": r["kind"], "domains": {}}
            for d in DOMAINS:
                row["domains"][d["domainId"]] = (r["role"] in ("DataMart.Admin", d["role"]))
            matrix.append(row)
        return {"domains": [{"domainId": d["domainId"], "name": d["name"]} for d in DOMAINS], "roles": matrix,
                "personas": PERSONAS if settings.auth_mode == "dev" else []}

    async def intake(body: dict[str, Any], idem: str | None) -> dict[str, Any]:
        headers = {"Idempotency-Key": idem} if idem else None
        res = await process.post_json("/v1/orders/intake", body, headers=headers)
        res["statusUrl"] = f"/exp/portal/v1/orders/{res['orderId']}"
        return res

    @app.get("/health", tags=["ops"])
    async def health():
        deps = {}
        for name, cli in (("process", process), ("system", system)):
            try:
                deps[name] = (await cli.request("GET", "/health")).status_code == 200
            except Exception:  # noqa: BLE001
                deps[name] = False
        return {"status": "ok" if all(deps.values()) else "degraded", "service": "experience-api",
                "env": settings.env, "dependencies": deps}

    # ------------------------------------------------------------------ catalog (portal pickers)
    @app.get("/portal/v1/catalog/customers", tags=["portal"])
    async def catalog_customers(request: Request):
        items = (await system.get_json("/v1/masterdata/customers"))["items"]
        user = current_user(request)
        if user.is_external_customer:
            items = [c for c in items if c["customerId"] in user.customer_ids]
        return {"items": [{k: c.get(k) for k in ("customerId", "name", "active", "segment", "channels", "currency",
                                                 "accountScope", "regions", "homeRegion")}
                          for c in items]}

    @app.get("/portal/v1/catalog/regions", tags=["portal"], summary="Operating regions and processing sites")
    async def catalog_regions():
        return await system.get_json("/v1/masterdata/regions")

    @app.get("/portal/v1/catalog/customers/{customer_id}/ship-tos", tags=["portal"])
    async def catalog_ship_tos(customer_id: str, request: Request):
        enforce_customer(request, customer_id)
        items = (await system.get_json(f"/v1/masterdata/customers/{customer_id}/ship-tos"))["items"]
        return {"items": [{"shipToId": s["shipToId"], "name": s["name"], "active": s["active"],
                           "region": s.get("region"),
                           "address": {"city": (s.get("address") or {}).get("city"),
                                       "state": (s.get("address") or {}).get("stateProvince"),
                                       **(s.get("address") or {})},
                           "deliveryDays": s.get("deliveryDays"), "transitDays": s.get("transitDays")} for s in items]}

    @app.get("/portal/v1/catalog/items", tags=["portal"])
    async def catalog_items(request: Request, customerId: str | None = None):
        user = current_user(request)
        if user.is_external_customer and not customerId and user.customer_ids:
            customerId = user.customer_ids[0]
        enforce_customer(request, customerId)
        items = (await system.get_json("/v1/masterdata/items", {"active": "true"}))["items"]
        prices = {}
        if customerId:
            for a in (await system.get_json("/v1/masterdata/price-agreements", {"customerId": customerId}))["items"]:
                prices[a["itemNumber"]] = a
        out = []
        for i in items:
            a = prices.get(i["itemNumber"])
            out.append({"itemNumber": i["itemNumber"], "description": i["description"], "packSize": i.get("packSize"),
                        "uom": i.get("inventoryUom", "CA"), "salesUoms": i.get("salesUoms", ["CA"]),
                        "listPrice": i.get("listPrice"), "contractPrice": a["price"] if a else i.get("listPrice"),
                        "agreementId": a["agreementId"] if a else None, "gtin": i.get("gtin"),
                        "temperatureZone": i.get("temperatureZone"), "leadTimeDays": i.get("leadTimeDays"),
                        "orderIncrement": i.get("orderIncrement"), "minOrderQty": i.get("minOrderQty")})
        return {"items": out}

    # ------------------------------------------------------------------ portal / manual entry
    @app.post("/portal/v1/orders", tags=["portal"], status_code=202,
              summary="Submit an order from the customer portal or customer-service manual entry")
    async def portal_order(req: PortalOrderRequest, request: Request,
                           idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        enforce_customer(request, req.customerId)
        draft = {"customerId": req.customerId, "shipToId": req.shipToId,
                 "purchaseOrder": {"poNumber": req.customerPo, "poDate": date.today().isoformat()},
                 "requestedDeliveryDate": req.requestedDeliveryDate.isoformat(), "orderType": req.orderType,
                 "lines": [{"lineNumber": i, "item": {"itemNumber": l.itemNumber},
                            "quantity": {"value": l.quantity, "uom": l.uom},
                            **({"unitPrice": {"amount": l.unitPrice, "source": "MANUAL_OVERRIDE" if req.channel == "MANUAL" else "PURCHASE_ORDER"}}
                               if l.unitPrice is not None else {}),
                            **({"notes": l.notes} if l.notes else {})} for i, l in enumerate(req.lines, start=1)]}
        if req.currency:
            draft["commercial"] = {"currency": req.currency}
        if req.notes:
            draft["notes"] = [{"type": "CUSTOMER", "text": req.notes}]
        if req.contact:
            draft["soldTo"] = {"partyId": req.customerId, "contacts": [{"role": "OC", **req.contact.model_dump(exclude_none=True)}]}
        actor_id = req.enteredBy or current_user(request).email or current_user(request).id
        body = {"sourceChannel": req.channel,
                "channelDetail": "customer-service-console" if req.channel == "MANUAL" else "customer-portal",
                "receivedAt": iso(utcnow()),
                "sourceDocument": {"documentType": "PORTAL_FORM", "reference": f"portal-{uuid.uuid4().hex[:12]}",
                                   "senderId": req.customerId, "rawContent": req.model_dump_json(indent=2),
                                   "contentType": "application/json"},
                "submittedBy": {"type": "USER" if req.channel == "MANUAL" else "CUSTOMER", "id": actor_id},
                "draft": draft}
        return await intake(body, idempotency_key)

    @app.get("/portal/v1/orders/{order_id}", tags=["portal"], summary="Customer-facing order status")
    async def portal_status(order_id: str, request: Request):
        view = await process.get_json(f"/v1/orders/{order_id}/status", {"view": "customer"})
        enforce_customer(request, view.get("customerId"))
        return view

    @app.get("/portal/v1/my/orders", tags=["portal"], summary="My orders: the signed-in customer's own orders")
    async def my_orders(request: Request, status: str | None = None, q: str | None = None, page: int = 1,
                        pageSize: int = Query(25, ge=1, le=200)):
        user = current_user(request)
        params = {"statusCategory": status, "q": q, "page": page, "pageSize": pageSize}
        if user.is_external_customer:
            if not user.customer_ids:
                return {"items": [], "total": 0, "page": page, "pageSize": pageSize}
            params["customerId"] = user.customer_ids[0]  # External ID accounts are bound to one customer
        res = await process.get_json("/v1/orders", {k: v for k, v in params.items() if v is not None})
        if user.is_external_customer:  # defence in depth
            res["items"] = [i for i in res["items"] if i.get("customerId") in user.customer_ids]
            for i in res["items"]:
                for k in ("touchless", "openExceptionCount", "crossRegion", "accountScope"):
                    i.pop(k, None)
        return res

    @app.get("/portal/v1/customers/{customer_id}/orders", tags=["portal"])
    async def portal_orders(customer_id: str, request: Request, status: str | None = None, page: int = 1,
                            pageSize: int = 25):
        enforce_customer(request, customer_id)
        return await process.get_json("/v1/orders", {"customerId": customer_id, "statusCategory": status,
                                                     "page": page, "pageSize": pageSize})

    # ------------------------------------------------------------------ partner API
    @app.post("/partner/v1/orders", tags=["partner"], status_code=202,
              summary="Partner system-to-system order in the canonical draft format")
    async def partner_order(request: Request, order: dict[str, Any] = Body(...),
                            idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
                            partner_id: str | None = Header(None, alias="x-partner-id")):
        if not isinstance(order.get("lines"), list) or not order["lines"]:
            raise ApiError(422, "Invalid order", "lines[] is required")
        sold = order.get("soldTo") or (order.get("parties") or {}).get("soldTo") or {}
        customer = order.get("customerId") or sold.get("customerId") or sold.get("partyId") or partner_id
        draft = dict(order)
        draft["customerId"] = customer
        if order.get("customerPo") and not order.get("purchaseOrder"):
            draft["purchaseOrder"] = {"poNumber": order["customerPo"]}
        body = {"sourceChannel": "PARTNER_API", "channelDetail": partner_id or "partner-api",
                "receivedAt": iso(utcnow()),
                "sourceDocument": {"documentType": "PARTNER_JSON", "reference": order.get("externalOrderId"),
                                   "senderId": partner_id or customer, "rawContent": json.dumps(order, indent=2),
                                   "contentType": "application/json"},
                "submittedBy": {"type": "PARTNER", "id": partner_id or request.state.principal.get("sub", "partner")},
                "draft": draft}
        return await intake(body, idempotency_key)

    @app.get("/partner/v1/orders/{order_id}/status", tags=["partner"])
    async def partner_status(order_id: str):
        return await process.get_json(f"/v1/orders/{order_id}/status", {"view": "customer"})

    # ------------------------------------------------------------------ EDI
    @app.post("/edi/v1/x12/850", tags=["edi"], status_code=202,
              summary="Receive an X12 850 interchange (one or many purchase orders); returns the 997",
              openapi_extra={"requestBody": {"content": {"text/plain": {"schema": {"type": "string"}}}}})
    async def edi_850(request: Request, partner_header: str | None = Header(None, alias="x-partner-id")):
        raw = (await request.body()).decode("utf-8", errors="replace")
        try:
            ic = x12.parse_interchange(raw)
        except x12.X12Error as e:
            raise ApiError(400, "Invalid X12 interchange", str(e))
        sender = ic.sender_id
        if partner_header and partner_header.strip() != sender:
            raise ApiError(403, "Sender mismatch", f"ISA06 {sender} does not match authenticated partner {partner_header}")
        partner = await system.get_json(f"/v1/masterdata/trading-partners/{sender}", not_found_ok=True)
        if not partner:
            raise ApiError(422, "Unknown trading partner", f"No trading partner agreement for ISA06 '{sender}'")
        results = {tx.control_number: ("A" if not tx.errors else "R") for tx in ic.transactions}
        ack997 = x12.build_997(ic, results, settings.isa_sender_id, int(ic.control_number or 0) % 999_999_999 + 1)
        group = new_group_id()
        orders, rejected = [], []
        for tx in ic.transactions:
            if tx.errors:
                rejected.append({"transactionSetControlNumber": tx.control_number, "errors": tx.errors})
                continue
            draft = x12.read_850(tx)
            draft["customerId"] = partner["customerId"]
            seg_text = ic.delimiters.segment.join(ic.delimiters.element.join(s) for s in tx.segments) + ic.delimiters.segment
            body = {"sourceChannel": "EDI", "channelDetail": f"AS2/VAN {partner.get('name', sender)}",
                    "receivedAt": iso(utcnow()), "orderGroupId": group,
                    "sourceDocument": {"documentType": "X12_850", "reference": f"ISA{ic.control_number}/ST{tx.control_number}",
                                       "senderId": sender, "receiverId": ic.receiver_id,
                                       "interchangeControlNumber": ic.control_number,
                                       "groupControlNumber": ic.group_control_number,
                                       "transactionSetControlNumber": tx.control_number, "x12Version": ic.version,
                                       "rawContent": seg_text.replace(ic.delimiters.segment, ic.delimiters.segment + "\n"),
                                       "contentType": "application/edi-x12"},
                    "submittedBy": {"type": "PARTNER", "id": sender, "name": partner.get("name")},
                    "acknowledgments": [{"type": "FUNCTIONAL_ACK_997", "documentType": "X12_997", "content": ack997,
                                         "controlNumber": str(int(ic.control_number or 0) % 999_999_999 + 1),
                                         "status": "ACCEPTED"}] if partner.get("send997", True) else [],
                    "draft": draft}
            idem = f"edi:{sender}:{ic.control_number}:{tx.control_number}"
            orders.append(await intake(body, idem))
        return {"interchangeControlNumber": ic.control_number, "sender": sender, "customerId": partner["customerId"],
                "orderGroupId": group if orders else None, "transactionSets": len(ic.transactions),
                "orders": orders, "rejected": rejected, "ack997": ack997}

    @app.get("/edi/v1/orders/{order_id}/documents", tags=["edi"])
    async def edi_documents(order_id: str, includeContent: bool = False):
        docs = await process.get_json(f"/v1/orders/{order_id}/documents")
        if includeContent:
            for d in docs["items"]:
                d["content"] = (await process.get_json(f"/v1/orders/{order_id}/documents/{d['documentId']}"))["content"]
        return docs

    # ------------------------------------------------------------------ e-mail
    @app.post("/email/v1/orders", tags=["email"], status_code=202,
              summary="Order received in the monitored inbox (from Logic Apps / Graph subscription)")
    async def email_order(msg: EmailMessage, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
        ex = await extract_email(msg.model_dump(by_alias=True), settings)
        h = ex["header"]
        customer_id = h.get("customerId")
        if not customer_id:  # fall back to the sender's e-mail domain
            domain = msg.from_.split("@")[-1].lower()
            for c in (await system.get_json("/v1/masterdata/customers"))["items"]:
                if any((ct.get("email") or "").lower().endswith("@" + domain) for ct in c.get("contacts", [])):
                    customer_id = c["customerId"]
                    break
        draft = {"customerId": customer_id, "purchaseOrder": {"poNumber": h.get("poNumber")},
                 "shipTo": {"partyId": h.get("shipTo")} if (h.get("shipTo") or "").upper().startswith("ST-")
                 else {"ediLocationCode": h.get("shipTo"), "name": h.get("shipTo")},
                 "requestedDeliveryDate": h.get("deliveryDate"),
                 "soldTo": {"partyId": customer_id, "contacts": [{"role": "OC", "email": msg.from_}]},
                 "lines": [{"lineNumber": i, "item": {k: v for k, v in {"itemNumber": l.get("itemNumber"),
                                                                       "buyerItemNumber": l.get("buyerItemNumber")}.items() if v},
                            "quantity": {"value": l.get("quantity"), "uom": l.get("uom") or "CA"},
                            **({"unitPrice": {"amount": l["unitPrice"]}} if l.get("unitPrice") else {})}
                           for i, l in enumerate(ex["lines"], start=1)]}
        raw = {"from": msg.from_, "to": msg.to, "subject": msg.subject, "body": msg.body,
               "attachments": [a.fileName for a in msg.attachments]}
        body = {"sourceChannel": "EMAIL", "channelDetail": msg.to or "orders inbox", "receivedAt": msg.receivedAt or iso(utcnow()),
                "sourceDocument": {"documentType": "PDF" if any(a.fileName.lower().endswith(".pdf") for a in msg.attachments)
                                   else ("SPREADSHEET" if msg.attachments else "EMAIL_MESSAGE"),
                                   "reference": msg.messageId or msg.subject, "senderId": msg.from_,
                                   "rawContent": json.dumps(raw, indent=2), "contentType": "application/json"},
                "extraction": {"method": ex["method"], "confidence": ex["confidence"], "modelId": ex["modelId"],
                               "fieldsLowConfidence": ex["lowConfidenceFields"]},
                "submittedBy": {"type": "CUSTOMER", "id": msg.from_},
                "draft": draft}
        res = await intake(body, idempotency_key or (f"email:{msg.messageId}" if msg.messageId else None))
        res["extraction"] = {"confidence": ex["confidence"], "method": ex["method"], "sources": ex["sources"],
                             "linesFound": len(ex["lines"]), "lowConfidenceFields": ex["lowConfidenceFields"]}
        return res

    return app


def __getattr__(name: str):  # lazy module attribute so importing the package has no side effects
    if name == "app":
        globals()["app"] = create_experience_app()
        return globals()["app"]
    raise AttributeError(name)
