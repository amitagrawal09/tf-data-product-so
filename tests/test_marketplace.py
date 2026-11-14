"""Data marketplace: catalog, live facts, preview, export (all formats), access governance and audit."""
from __future__ import annotations

import csv
import io
import json

import pytest
from openpyxl import load_workbook

from tfso.experience.catalog import DATASETS

pytestmark = pytest.mark.asyncio
ME = {"x-user-id": "analyst@taylorfarms.example"}


async def _seed(h) -> str:
    acc = await h.portal(customerPo="MKT-1", lines=[{"itemNumber": "TF-10001", "quantity": 10, "uom": "CA"},
                                                   {"itemNumber": "TF-20001", "quantity": 4, "uom": "CA"}])
    await h.advance(acc["orderId"], "INVOICED")
    return acc["orderId"]


async def test_catalog_lists_every_dataset_with_live_facts(h):
    await _seed(h)
    res = (await h.exp.get("/marketplace/v1/datasets")).json()
    assert {d["id"] for d in res["items"]} == {d["id"] for d in DATASETS}
    by = {d["id"]: d for d in res["items"]}
    assert by["sales-orders"]["recordCount"] == 1 and by["sales-order-lines"]["recordCount"] == 2
    assert by["shipments"]["recordCount"] == 1 and by["invoices"]["recordCount"] == 1
    assert by["order-lifecycle-events"]["recordCount"] >= 15
    assert by["items"]["recordCount"] == 9 and by["sales-orders"]["freshnessStatus"] == "ON_TIME"
    assert any(c.startswith("GET /process/v1/datasets/stats") for c in res["fulfilledBy"])
    assert [d["id"] for d in (await h.exp.get("/marketplace/v1/datasets", params={"q": "traceability"})).json()["items"]] == ["shipments"]
    assert all(d["domain"] == "Customer" for d in (await h.exp.get("/marketplace/v1/datasets", params={"domain": "Customer"})).json()["items"])


async def test_dataset_card_schema_and_lineage(h):
    card = (await h.exp.get("/marketplace/v1/datasets/sales-orders", headers=ME)).json()
    assert card["owner"]["team"] and card["refresh"]["freshnessSlaSeconds"] == 60
    assert card["myAccess"]["canExport"] is True and card["consumption"]["events"]["topic"] == "sales-order-events"
    schema = (await h.exp.get("/marketplace/v1/datasets/sales-orders/schema")).json()
    assert schema["primaryKey"] == ["orderId"] and any(f["name"] == "status" for f in schema["fields"])
    lin = (await h.exp.get("/marketplace/v1/datasets/sales-orders/lineage")).json()
    ids = {n["id"] for n in lin["nodes"]}
    assert {"ch-edi", "intake", "store", "dataset", "bronze", "gold", "d365"} <= ids
    assert all(e["from"] in ids and e["to"] in ids for e in lin["edges"])
    assert (await h.exp.get("/marketplace/v1/datasets/nope")).status_code == 404


async def test_preview_and_exports_are_fulfilled_by_backend_apis(h):
    oid = await _seed(h)
    pv = (await h.exp.get("/marketplace/v1/datasets/sales-order-lines/preview", headers=ME)).json()
    assert pv["rowCount"] == 2 and pv["rows"][0]["orderId"] == oid and "GET /process/v1/orders" in pv["fulfilledBy"][0]

    r = await h.exp.get("/marketplace/v1/datasets/sales-orders/export", params={"format": "csv"}, headers=ME)
    assert r.status_code == 200 and r.headers["x-tfso-row-count"] == "1" and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0]["orderId"] == oid and rows[0]["status"] == "INVOICED"

    r = await h.exp.get("/marketplace/v1/datasets/shipments/export", params={"format": "json", "fields": "orderId,traceabilityLotCode"}, headers=ME)
    body = r.json()
    assert body["columns"] == ["orderId", "traceabilityLotCode"] and body["rows"][0]["traceabilityLotCode"]

    r = await h.exp.get("/marketplace/v1/datasets/order-lifecycle-events/export",
                        params={"format": "ndjson", "eventType": "SHIPPED,INVOICED"}, headers=ME)
    events = [json.loads(x) for x in r.text.splitlines()]
    assert {e["eventType"] for e in events} == {"SHIPPED", "INVOICED"}

    r = await h.exp.get("/marketplace/v1/datasets/items/export", params={"format": "xlsx"}, headers=ME)
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["items", "About"] and wb["items"].max_row == 10

    assert (await h.exp.get("/marketplace/v1/datasets/items/export", params={"fields": "bogus"}, headers=ME)).status_code == 400
    audit = (await h.exp.get("/marketplace/v1/exports", params={"requester": ME["x-user-id"]})).json()["items"]
    assert len(audit) == 4 and all(a["fulfilledBy"] for a in audit)
    usage = (await h.exp.get("/marketplace/v1/datasets")).json()
    assert next(d for d in usage["items"] if d["id"] == "items")["usage"]["exports"] == 1


async def test_confidential_dataset_requires_approval_and_masks_pii(h):
    pv = (await h.exp.get("/marketplace/v1/datasets/customers/preview", headers=ME)).json()
    assert "contactEmail" in pv["maskedFields"] and pv["rows"][0]["contactEmail"].startswith("••••")
    r = await h.exp.get("/marketplace/v1/datasets/customers/export", headers=ME)
    assert r.status_code == 403 and "approve" in r.json()["detail"]

    req = (await h.exp.post("/marketplace/v1/datasets/customers/access-requests", headers=ME,
                            json={"purpose": "Customer segmentation analysis", "team": "Analytics"})).json()
    assert req["status"] == "PENDING"
    owner = {"x-user-id": "mdm@taylorfarms.example"}
    dec = (await h.exp.post(f"/marketplace/v1/access-requests/{req['requestId']}/decision", headers=owner,
                            json={"decision": "APPROVED", "note": "OK for analytics"})).json()
    assert dec["status"] == "APPROVED" and dec["decidedBy"] == "mdm@taylorfarms.example"
    pv = (await h.exp.get("/marketplace/v1/datasets/customers/preview", headers=ME)).json()
    assert not pv["maskedFields"] and "@" in pv["rows"][0]["contactEmail"]
    assert (await h.exp.get("/marketplace/v1/datasets/customers/export", headers=ME)).status_code == 200
    # another user is still blocked
    assert (await h.exp.get("/marketplace/v1/datasets/customers/export", headers={"x-user-id": "other"})).status_code == 403


async def test_internal_dataset_access_auto_approved(h):
    req = (await h.exp.post("/marketplace/v1/datasets/sales-orders/access-requests", headers=ME,
                            json={"purpose": "Order-to-cash dashboard", "consumerSystem": "Power BI"})).json()
    assert req["status"] == "APPROVED" and req["decidedBy"] == "policy:auto-approve"
