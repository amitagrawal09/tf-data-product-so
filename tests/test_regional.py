"""Regional operating model: network sourcing, national/regional scope filters, OTIF and orders at risk."""
from __future__ import annotations

import pytest

from tests.conftest import delivery

pytestmark = pytest.mark.asyncio
MON_SAT = ("MON", "TUE", "WED", "THU", "FRI", "SAT")


async def test_home_site_sourcing_by_region(h):
    acc = await h.portal(customerPo="R-DAL", shipToId="ST-1001-03", requestedDeliveryDate=delivery(MON_SAT))
    order = (await h.process.get(f"/v1/orders/{acc['orderId']}")).json()
    ful = order["fulfillment"]
    assert (ful["regionId"], ful["warehouse"], ful["sourcing"]["rule"]) == ("CENTRAL", "WH-DAL", "HOME_SITE")
    assert ful["accountScope"] == "NATIONAL" and ful["crossRegion"] is False
    assert order["shipping"]["shipFromWarehouse"] == "WH-DAL"
    erp = (await h.system.get("/v1/d365/sales-orders", params={"orderId": acc["orderId"]})).json()
    assert erp["warehouse"] == "WH-DAL"  # D365 order created against the sourced warehouse
    assert "ORDER_SOURCED" in [e["eventType"] for e in await h.history(acc["orderId"])]


async def test_alternate_and_cross_region_sourcing(h):
    # New Jersey has only 40 cases of Spring Mix; 100 cases must come from Maryland (same region) or beyond
    acc = await h.portal(customerId="C-1006", shipToId="ST-1006-01", customerPo="R-NE",
                         requestedDeliveryDate=delivery(MON_SAT),
                         lines=[{"itemNumber": "TF-10002", "quantity": 100, "uom": "CA"}])
    ful = (await h.process.get(f"/v1/orders/{acc['orderId']}")).json()["fulfillment"]
    assert ful["regionId"] in ("NORTHEAST", "SOUTHEAST", "CENTRAL") and ful["sourcing"]["rule"] in (
        "ALTERNATE_SITE_IN_REGION", "CROSS_REGION")
    home = next(c for c in ful["sourcing"]["candidates"] if c["home"])
    assert home["warehouse"] == "WH-NJ" and home["fillRate"] < 1
    # Broccoli only exists in Salinas: a Northeast order crosses regions
    acc = await h.portal(customerId="C-1006", shipToId="ST-1006-01", customerPo="R-NE2",
                         requestedDeliveryDate=delivery(MON_SAT, lead=4),
                         lines=[{"itemNumber": "TF-40001", "quantity": 5, "uom": "CA"}])
    ful = (await h.process.get(f"/v1/orders/{acc['orderId']}")).json()["fulfillment"]
    assert ful["sourcing"]["rule"] == "CROSS_REGION" and ful["warehouse"] == "WH-SAL" and ful["crossRegion"] is True
    assert ful["customerRegionId"] == "NORTHEAST"


async def test_scope_filters_and_regional_kpis(h):
    west = await h.portal(customerPo="S-W")                                    # Stockton → WEST
    central = await h.portal(customerPo="S-C", shipToId="ST-1001-03")          # Dallas → CENTRAL
    ne = await h.portal(customerId="C-1006", shipToId="ST-1006-01", customerPo="S-NE")  # regional account
    await h.advance(west["orderId"], "INVOICED")
    res = (await h.process.get("/v1/orders", params={"region": "CENTRAL"})).json()
    assert [i["orderId"] for i in res["items"]] == [central["orderId"]] and res["items"][0]["regionName"] == "Central"
    assert (await h.process.get("/v1/orders", params={"site": "WH-SAL"})).json()["total"] == 1
    assert (await h.process.get("/v1/orders", params={"accountScope": "REGIONAL"})).json()["items"][0]["orderId"] == ne["orderId"]
    k = (await h.process.get("/v1/metrics/kpis")).json()
    assert {r["region"] for r in k["byRegion"]} == {"WEST", "CENTRAL", "NORTHEAST"}
    west_k = next(r for r in k["byRegion"] if r["region"] == "WEST")
    assert west_k["otifRate"] == 1.0 and west_k["fillRate"] == 1.0 and west_k["revenue"] == 245.0
    kw = (await h.process.get("/v1/metrics/kpis", params={"region": "WEST"})).json()
    assert kw["ordersTotal"] == 1 and kw["scope"]["region"] == "WEST" and kw["otifRate"] == 1.0
    assert {s["site"] for s in kw["bySite"]} == {"WH-SAL"}
    summ = (await h.process.get("/v1/status/summary", params={"accountScope": "NATIONAL"})).json()
    assert summ["total"] == 2 and summ["byRegion"] == {"WEST": 1, "CENTRAL": 1}
    ev = (await h.process.get("/v1/order-events", params={"region": "CENTRAL"})).json()
    assert {e["orderId"] for e in ev["items"]} == {central["orderId"]}


async def test_orders_at_risk(h):
    bo = await h.portal(customerId="C-1002", shipToId="ST-1002-01", customerPo="RISK-BO",
                        requestedDeliveryDate=delivery(lead=4), lines=[{"itemNumber": "TF-40001", "quantity": 30, "uom": "CA"}])
    bad = await h.portal(customerPo="RISK-EX", lines=[{"itemNumber": "TF-10001", "quantity": 5, "uom": "CA", "unitPrice": 1}])
    ok = await h.portal(customerPo="RISK-OK")
    res = (await h.process.get("/v1/orders/at-risk")).json()
    by = {i["orderId"]: i for i in res["items"]}
    assert ok["orderId"] not in by
    assert {r["code"] for r in by[bo["orderId"]]["reasons"]} >= {"BACKORDER"}
    assert by[bad["orderId"]]["severity"] == "HIGH" and by[bad["orderId"]]["reasons"][0]["code"] == "ACTION_REQUIRED"
    assert res["valueAtRisk"] > 0 and res["byReason"]["BACKORDER"] == 1
    assert (await h.process.get("/v1/orders/at-risk", params={"region": "CENTRAL"})).json()["total"] == 0
    k = (await h.process.get("/v1/metrics/kpis")).json()
    assert k["ordersAtRisk"] == 2


async def test_regions_reference_and_inventory(h):
    regions = (await h.exp.get("/portal/v1/catalog/regions")).json()["items"]
    assert {r["regionId"] for r in regions} == {"WEST", "SOUTHWEST", "CENTRAL", "SOUTHEAST", "NORTHEAST"}
    inv = (await h.system.get("/v1/masterdata/inventory", params={"region": "WEST", "itemNumber": "TF-40001"})).json()
    assert {(i["warehouse"], i["available"]) for i in inv["items"]} == {("WH-SAL", 20), ("WH-TRC", 0)}


async def test_exception_orders_carry_their_demand_region(h):
    bad = await h.portal(customerId="C-1006", shipToId="ST-1006-01", customerPo="NE-BAD",
                         lines=[{"itemNumber": "TF-10001", "quantity": 5, "uom": "CA", "unitPrice": 1}])
    assert bad["status"] == "EXCEPTION"
    q = (await h.process.get("/v1/exceptions", params={"region": "NORTHEAST"})).json()
    assert [i["orderId"] for i in q["items"]] == [bad["orderId"]] and q["items"][0]["accountScope"] == "REGIONAL"
