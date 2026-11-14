"""End-to-end lifecycle tests across Experience → Process → System APIs."""
from __future__ import annotations

import base64

import pytest

from tfso.common.cdm import SalesOrder
from tests.conftest import delivery

pytestmark = pytest.mark.asyncio


async def test_portal_order_full_lifecycle(h):
    acc = await h.portal(customerPo="PO-LIFE-1", lines=[{"itemNumber": "TF-10001", "quantity": 10, "uom": "CA"},
                                                        {"itemNumber": "TF-20001", "quantity": 4, "uom": "CA"}])
    oid = acc["orderId"]
    assert acc["status"] == "SUBMITTED_TO_ERP"
    st = await h.status(oid)
    assert st["status"] == "ACCEPTED" and st["erpSalesOrderId"].startswith("SO-")
    await h.advance(oid, "SHIPPED")
    assert (await h.status(oid))["status"] == "SHIPPED"
    await h.advance(oid, "INVOICED")
    st = await h.status(oid)
    assert st["status"] == "INVOICED" and st["statusCategory"] == "INVOICED"
    assert all(m["status"] == "DONE" for m in st["milestones"])
    assert st["invoices"][0]["amount"] == "331.00"  # 10 x 24.50 + 4 x 21.50 (FreshMart agreement)

    hist = await h.history(oid)
    types = [e["eventType"] for e in hist]
    assert types[:8] == ["ORDER_RECEIVED", "ORDER_INGESTED", "VALIDATION_PASSED", "ORDER_ENRICHED",
                         "ORDER_SOURCED", "ORDER_PUBLISHED", "SUBMITTED_TO_ERP", "ERP_ACCEPTED"]
    for t in ("RELEASED_TO_WAREHOUSE", "PICKED", "PACKED", "SHIPPED", "DELIVERED", "INVOICED", "NOTIFICATION_SENT"):
        assert t in types
    assert [e["sequence"] for e in hist] == list(range(1, len(hist) + 1))
    assert {e["tier"] for e in hist} == {"TIER1", "TIER2", "TIER3"}

    order = (await h.process.get(f"/v1/orders/{oid}")).json()
    model = SalesOrder.model_validate(order)  # the published record honours the contract
    assert len(model.order_history) == len(hist)
    assert order["roi"]["touchless"] is True and order["roi"]["stageTimestamps"]["invoiced"]
    assert order["lines"][0]["lots"][0]["traceabilityLotCode"]  # FSMA 204 TLC captured at ship
    assert order["totals"]["grandTotal"] == "331.00"


async def test_edi_multi_po_interchange_acks_and_group_history(h):
    rdd = delivery(("MON", "TUE", "WED", "THU", "FRI", "SAT")).replace("-", "")
    sets = []
    for n, (po, item, qty) in enumerate([("FM-A1", "FM-55012", 40), ("FM-A2", "FM-66001", 12)], 1):
        seg = [f"ST*850*{n:04d}", f"BEG*00*SA*{po}**20261113", f"DTM*002*{rdd}", "N1*ST*FreshMart DC 12*92*FM-DC12",
               f"PO1*1*{qty}*CA***IN*{item}", "PID*F****Item", "CTT*1"]
        sets.append("~".join(seg + [f"SE*{len(seg) + 1}*{n:04d}"]))
    x12 = ("ISA*00*          *00*          *ZZ*FRESHMART      *ZZ*TAYLORFARMS    *261113*1200*U*00401*000000777*0*T*>~"
           "GS*PO*FRESHMART*TAYLORFARMS*20261113*1200*777*X*004010~" + "~".join(sets) + "~GE*2*777~IEA*1*000000777~")
    r = await h.exp.post("/edi/v1/x12/850", content=x12, headers={"content-type": "text/plain"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert len(body["orders"]) == 2 and "AK9*A*2*2*2" in body["ack997"]
    await h.pump()
    oid = body["orders"][0]["orderId"]
    order = (await h.process.get(f"/v1/orders/{oid}")).json()
    assert order["lines"][0]["item"]["itemNumber"] == "TF-10001"  # buyer item FM-55012 cross-referenced
    assert order["parties"]["shipTo"]["partyId"] == "ST-1001-01"  # N1*ST*92 location code resolved
    await h.advance(oid, "INVOICED")
    docs = {d["documentType"] for d in (await h.process.get(f"/v1/orders/{oid}/documents")).json()["items"]}
    assert {"X12_850", "X12_997", "X12_855", "X12_856", "X12_810"} <= docs
    group = (await h.process.get(f"/v1/order-groups/{body['orderGroupId']}/history")).json()
    assert len(group["orderIds"]) == 2 and {e["orderId"] for e in group["history"]} == set(group["orderIds"])
    # replaying the same interchange is idempotent (edi:sender:ISA13:ST02)
    again = (await h.exp.post("/edi/v1/x12/850", content=x12, headers={"content-type": "text/plain"})).json()
    assert [o["orderId"] for o in again["orders"]] == [o["orderId"] for o in body["orders"]]


async def test_unknown_partner_and_bad_x12(h):
    r = await h.exp.post("/edi/v1/x12/850", content="NOT X12", headers={"content-type": "text/plain"})
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")
    bad = ("ISA*00*          *00*          *ZZ*NOBODY         *ZZ*TAYLORFARMS    *261113*1200*U*00401*000000001*0*T*>~"
           "GS*PO*NOBODY*TAYLORFARMS*20261113*1200*1*X*004010~ST*850*0001~BEG*00*SA*X**20261113~SE*3*0001~GE*1*1~IEA*1*000000001~")
    r = await h.exp.post("/edi/v1/x12/850", content=bad, headers={"content-type": "text/plain"})
    assert r.status_code == 422


async def test_exception_correction_resubmit_with_optimistic_concurrency(h):
    acc = await h.portal(customerPo="PO-BAD-1", channel="MANUAL", enteredBy="cs.agent",
                         lines=[{"itemNumber": "TF-10001", "quantity": 5, "uom": "CA", "unitPrice": 9.99},
                                {"itemNumber": "TF-99999", "quantity": 1, "uom": "CA"}])
    oid = acc["orderId"]
    assert acc["status"] == "EXCEPTION"
    assert {e["code"] for e in acc["exceptions"]} == {"PRICE_MISMATCH", "ITEM_NOT_FOUND"}
    queue = (await h.process.get("/v1/exceptions")).json()
    assert any(i["orderId"] == oid for i in queue["items"])

    order = (await h.process.get(f"/v1/orders/{oid}")).json()
    change = {"changes": {"lines": [{"lineNumber": 1, "unitPrice": None}, {"lineNumber": 2, "itemNumber": "TF-20001"}]},
              "reason": "Use contract price; correct item", "actor": "cs.agent"}
    assert (await h.process.patch(f"/v1/orders/{oid}", json=change)).status_code == 428
    assert (await h.process.patch(f"/v1/orders/{oid}", json=change, headers={"If-Match": '"999"'})).status_code == 412
    r = await h.process.patch(f"/v1/orders/{oid}", json=change, headers={"If-Match": f'"{order["version"]}"'})
    assert r.status_code == 200, r.text
    r = await h.process.post(f"/v1/orders/{oid}/resubmit", json={"actor": "cs.agent"})
    assert r.json()["status"] == "SUBMITTED_TO_ERP"
    await h.pump()
    order = (await h.process.get(f"/v1/orders/{oid}")).json()
    assert order["status"] == "ACCEPTED"
    assert order["roi"]["touchless"] is False and order["roi"]["touchCount"] >= 3  # entry + correction + resubmit
    assert order["dataQuality"]["firstPass"] is False and order["dataQuality"]["attempt"] == 2
    assert all(e["state"] == "RESOLVED" for e in order["exceptions"])
    corrected = next(e for e in order["orderHistory"] if e["eventType"] == "ORDER_CORRECTED")
    assert any(c["path"].startswith("/lines/1/item") for c in corrected["changes"])


async def test_credit_hold_and_release(h):
    acc = await h.portal(customerId="C-1003", shipToId="ST-1003-01", customerPo="PO-CREDIT",
                         requestedDeliveryDate=delivery(),
                         lines=[{"itemNumber": "TF-10002", "quantity": 200, "uom": "CA"}])  # 6,350 > available credit
    st = await h.status(acc["orderId"])
    assert st["status"] == "CREDIT_HOLD" and st["statusCategory"] == "ACTION_REQUIRED"
    assert st["openExceptions"][0]["code"] == "CREDIT_HOLD"
    r = await h.system.post(f"/v1/sim/orders/{acc['orderId']}/release-credit-hold")
    assert r.status_code == 200
    await h.pump()
    st = await h.status(acc["orderId"])
    assert st["status"] == "ACCEPTED" and not st["openExceptions"]


async def test_backorder_and_partial_shipment(h):
    acc = await h.portal(customerId="C-1002", shipToId="ST-1002-01", customerPo="PO-BO",
                         requestedDeliveryDate=delivery(lead=4),
                         lines=[{"itemNumber": "TF-40001", "quantity": 30, "uom": "CA"},
                                {"itemNumber": "TF-20002", "quantity": 10, "uom": "CA"}])
    oid = acc["orderId"]
    st = await h.status(oid)
    line1 = st["lines"][0]
    assert st["status"] == "ACCEPTED" and line1["confirmed"] == "20" and line1["backordered"] == "10"
    assert "LINE_BACKORDERED" in [e["eventType"] for e in await h.history(oid)]
    await h.advance(oid, "SHIPPED", lines=[{"lineNumber": 2, "quantity": 10}])
    assert (await h.status(oid))["status"] == "PARTIALLY_SHIPPED"
    await h.advance(oid, "SHIPPED")
    st = await h.status(oid)
    assert st["status"] == "SHIPPED" and len(st["shipments"]) == 2


async def test_duplicate_po_and_date_rules(h):
    await h.portal(customerPo="PO-DUP")
    dup = await h.portal(customerPo="PO-DUP")
    assert dup["status"] == "EXCEPTION" and dup["exceptions"][0]["code"] == "DUPLICATE_ORDER"
    past = await h.portal(customerPo="PO-PAST", requestedDeliveryDate="2020-01-01")
    assert any(e["code"] == "DATE_IN_PAST" for e in past["exceptions"])
    inactive = await h.portal(customerId="C-1004", shipToId="ST-1001-01", customerPo="PO-INACTIVE")
    assert {e["code"] for e in inactive["exceptions"]} >= {"CUSTOMER_INACTIVE"}


async def test_email_csv_attachment_and_low_confidence(h):
    csv = "Item,Qty,UOM\nTF-10001,8,CA\nTF-30001,6,CA\n"
    msg = {"from": "kitchen@greenleafcafe.example", "subject": "Order",
           "body": f"PO: GL-77\nShip-To: ST-1003-01\nDeliver: {delivery()}\n",
           "attachments": [{"fileName": "order.csv", "contentType": "text/csv",
                            "contentBase64": base64.b64encode(csv.encode()).decode()}]}
    r = await h.exp.post("/email/v1/orders", json=msg)
    assert r.status_code == 202, r.text
    res = r.json()
    assert res["customerId"] == "C-1003"  # resolved from the sender's domain
    assert res["status"] == "SUBMITTED_TO_ERP" and res["extraction"]["linesFound"] == 2
    vague = await h.exp.post("/email/v1/orders", json={"from": "kitchen@greenleafcafe.example",
                                                       "body": "please send the usual TF-10001 5 CA"})
    codes = {e["code"] for e in vague.json()["exceptions"]}
    assert "EXTRACTION_LOW_CONFIDENCE" in codes


async def test_partner_order_prices_from_agreement(h):
    r = await h.exp.post("/partner/v1/orders", headers={"x-partner-id": "bayside-partner"}, json={
        "customerPo": "BMK-1", "soldTo": {"customerId": "C-1005"}, "shipTo": {"shipToId": "ST-1005-01"},
        "requestedDeliveryDate": delivery(("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")),
        "lines": [{"lineNumber": 1, "item": {"itemNumber": "TF-30001"}, "quantity": {"value": 12, "uom": "CA"}}]})
    assert r.status_code == 202, r.text
    order = (await h.process.get(f"/v1/orders/{r.json()['orderId']}")).json()
    price = order["lines"][0]["unitPrice"]
    assert price["amount"] == "17.95" and price["source"] == "CUSTOMER_AGREEMENT" and price["agreementId"] == "TA-BMK-2026"


async def test_status_events_idempotent_and_out_of_order(h):
    acc = await h.portal(customerPo="PO-EVT")
    oid = acc["orderId"]
    await h.advance(oid, "SHIPPED")
    late = {"eventId": "wms-late-1", "eventType": "PICKED", "orderId": oid, "sourceSystem": "WMS",
            "occurredAt": "2026-01-01T00:00:00Z"}
    r1 = await h.process.post("/v1/events", json=late)
    r2 = await h.process.post("/v1/events", json=late)
    assert r1.status_code == 202 and r2.json()["duplicate"] is True
    hist = await h.history(oid)
    assert hist[-1]["eventType"] == "LATE_EVENT_IGNORED" and (await h.status(oid))["status"] == "SHIPPED"


async def test_hold_release_and_cancel(h):
    acc = await h.portal(customerPo="PO-HOLD")
    oid = acc["orderId"]
    r = await h.process.post(f"/v1/orders/{oid}/hold", json={"reason": "Customer called", "actor": "cs"})
    assert r.json()["status"] == "ON_HOLD"
    r = await h.process.post(f"/v1/orders/{oid}/release-hold", json={"actor": "cs"})
    assert r.json()["status"] == "ACCEPTED"
    r = await h.process.post(f"/v1/orders/{oid}/cancel", json={"reasonCode": "CUSTOMER_REQUEST", "actor": "cs"})
    assert r.json()["status"] == "CANCELLED"
    await h.pump()
    erp = (await h.system.get("/v1/d365/sales-orders", params={"orderId": oid})).json()
    assert erp["stage"] == "CANCELLED"
    acc2 = await h.portal(customerPo="PO-SHIPPED")
    await h.advance(acc2["orderId"], "SHIPPED")
    assert (await h.process.post(f"/v1/orders/{acc2['orderId']}/cancel", json={})).status_code == 409


async def test_search_summary_kpis_reference_and_auth(h):
    a = await h.portal(customerPo="PO-S1")
    await h.portal(customerPo="PO-S2", channel="MANUAL")
    res = (await h.process.get("/v1/orders", params={"customerPo": "PO-S1"})).json()
    assert res["total"] == 1 and res["items"][0]["orderId"] == a["orderId"]
    assert (await h.process.get("/v1/orders", params={"sort": "-bogus"})).status_code == 400
    assert (await h.process.get("/v1/orders/by-po/PO-S2")).status_code == 200
    k = (await h.process.get("/v1/metrics/kpis")).json()
    assert k["ordersTotal"] == 2 and k["touchlessRate"] == 0.5
    ref = (await h.process.get("/v1/reference/statuses")).json()
    assert "ACCEPTED" in next(s for s in ref["statuses"] if s["status"] == "SUBMITTED_TO_ERP")["allowedTransitions"]
    r = await h.process.get(f"/v1/orders/{a['orderId']}")
    etag = r.headers["etag"]
    assert (await h.process.get(f"/v1/orders/{a['orderId']}", headers={"If-None-Match": etag})).status_code == 304
    noauth = await h.process.get("/v1/orders", headers={"x-api-key": "wrong"})
    assert noauth.status_code == 401
    assert (await h.process.get("/health", headers={"x-api-key": ""})).status_code == 200
    customer_view = (await h.exp.get(f"/portal/v1/orders/{a['orderId']}")).json()
    assert "roi" not in customer_view and "openIssues" in customer_view


async def test_webhook_subscription_crud(h):
    r = await h.process.post("/v1/subscriptions", json={"callbackUrl": "https://example.com/hook", "eventTypes": ["SHIPPED"]})
    assert r.status_code == 201 and r.json()["secret"]
    sid = r.json()["subscriptionId"]
    assert any(x["subscriptionId"] == sid for x in (await h.process.get("/v1/subscriptions")).json()["items"])
    assert (await h.process.delete(f"/v1/subscriptions/{sid}")).status_code == 204


async def test_lakehouse_and_crm_receive_events(h):
    acc = await h.portal(customerPo="PO-LAKE")
    files = (await h.system.get("/v1/lakehouse/files")).json()["items"]
    assert files and sum(f["rows"] for f in files) >= 7
    crm = (await h.system.get(f"/v1/crm/orders/{acc['orderId']}/activities")).json()["items"]
    assert any(a["eventType"] == "ERP_ACCEPTED" for a in crm)
