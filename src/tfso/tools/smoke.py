"""End-to-end smoke test through the gateway (local) or API Management (Azure).

    python -m tfso.tools.smoke --base http://localhost:8080/api --api-key local-dev-key
    python -m tfso.tools.smoke --base https://apim-x.azure-api.net --subscription-key <key>

Exercises: health, portal order → D365 → fulfilment → invoice, EDI 850 (2 POs) → 997/855/856/810,
exception → correction → resubmit, Order Status API reads, KPIs. Exits non-zero on the first failure.
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import date, timedelta

import httpx

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


class Smoke:
    def __init__(self, base: str, api_key: str | None, sub_key: str | None, timeout: float):
        h = {}
        if api_key:
            h["x-api-key"] = api_key
        if sub_key:
            h["Ocp-Apim-Subscription-Key"] = sub_key
        self.c = httpx.Client(base_url=base.rstrip("/"), headers=h, timeout=timeout)
        self.ok = 0

    def step(self, name: str) -> None:
        print(f"  • {name}", flush=True)

    def check(self, cond: bool, msg: str) -> None:
        if not cond:
            raise AssertionError(msg)
        self.ok += 1

    def call(self, method: str, path: str, expect: tuple[int, ...] = (200, 201, 202), **kw) -> dict:
        r = self.c.request(method, path, **kw)
        if r.status_code not in expect:
            raise AssertionError(f"{method} {path} → {r.status_code}: {r.text[:600]}")
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else {}

    def wait_status(self, order_id: str, wanted: set[str], seconds: float = 30) -> dict:
        deadline = time.time() + seconds
        last = {}
        while time.time() < deadline:
            last = self.call("GET", f"/process/v1/orders/{order_id}/status")
            if last["status"] in wanted:
                return last
            time.sleep(0.5)
        raise AssertionError(f"{order_id} stuck in {last.get('status')} (wanted {sorted(wanted)})")

    @staticmethod
    def delivery_date(days_ok: list[str], lead: int = 3) -> date:
        d = date.today() + timedelta(days=lead)
        while DAYS[d.weekday()] not in days_ok:
            d += timedelta(days=1)
        return d

    def run(self) -> None:
        print("Sales Order data product - smoke test")
        self.step("health of experience, process and system APIs")
        for api in ("exp", "process", "system"):
            h = self.call("GET", f"/{api}/health")
            self.check(h.get("status") in ("ok", "degraded"), f"{api} unhealthy: {h}")
        sysh = self.call("GET", "/system/health")
        simulator = sysh.get("d365Mode") == "simulator"

        self.step("portal order → validated, enriched, published, submitted to D365")
        rdd = self.delivery_date(["MON", "TUE", "WED", "THU", "FRI", "SAT"])
        po = f"SMOKE-{uuid.uuid4().hex[:8].upper()}"
        acc = self.call("POST", "/exp/portal/v1/orders", headers={"Idempotency-Key": po}, json={
            "channel": "PORTAL", "customerId": "C-1001", "shipToId": "ST-1001-01", "customerPo": po,
            "requestedDeliveryDate": rdd.isoformat(), "contact": {"name": "Smoke Test", "email": "smoke@example.com"},
            "lines": [{"itemNumber": "TF-10001", "quantity": 10, "uom": "CA"},
                      {"itemNumber": "TF-20001", "quantity": 4, "uom": "CA"}]})
        oid = acc["orderId"]
        self.check(acc["status"] in ("SUBMITTED_TO_ERP", "ACCEPTED"), f"unexpected intake status {acc}")
        replay = self.call("POST", "/exp/portal/v1/orders", headers={"Idempotency-Key": po}, json={
            "customerId": "C-1001", "shipToId": "ST-1001-01", "customerPo": po, "requestedDeliveryDate": rdd.isoformat(),
            "lines": [{"itemNumber": "TF-10001", "quantity": 10}]})
        self.check(replay["orderId"] == oid, "idempotent replay returned a different order")

        if simulator:
            self.step("D365 accepts the order")
            st = self.wait_status(oid, {"ACCEPTED"})
            self.check(bool(st.get("erpSalesOrderId")), "no ERP sales order id")
            self.step("warehouse ships, TMS delivers, D365 invoices (simulator)")
            self.call("POST", f"/system/v1/sim/orders/{oid}/advance", json={"to": "SHIPPED"})
            self.wait_status(oid, {"SHIPPED"})
            self.call("POST", f"/system/v1/sim/orders/{oid}/advance", json={"to": "INVOICED"})
            st = self.wait_status(oid, {"INVOICED"})
            self.check(all(m["status"] == "DONE" for m in st["milestones"]), "milestones not complete")
            hist = self.call("GET", f"/process/v1/orders/{oid}/history")
            types = [h["eventType"] for h in hist["items"]]
            for t in ("ORDER_RECEIVED", "VALIDATION_PASSED", "ORDER_PUBLISHED", "ERP_ACCEPTED", "SHIPPED", "INVOICED"):
                self.check(t in types, f"history missing {t}: {types}")
            self.check([h["sequence"] for h in hist["items"]] == list(range(1, len(types) + 1)), "history not sequential")

        self.step("EDI 850 interchange with two purchase orders → 997 + two orders")
        isa_ctrl = f"{int(time.time()) % 999999999:09d}"
        rdd2 = self.delivery_date(["MON", "TUE", "WED", "THU", "FRI", "SAT"]).strftime("%Y%m%d")
        sets = []
        for n, (bpo, item, qty) in enumerate([(f"FM{isa_ctrl[-6:]}A", "FM-55012", 40), (f"FM{isa_ctrl[-6:]}B", "FM-66001", 12)], 1):
            body = [f"ST*850*{n:04d}", f"BEG*00*SA*{bpo}**{date.today():%Y%m%d}", f"DTM*002*{rdd2}",
                    "N1*ST*FreshMart DC 12*92*FM-DC12", f"PO1*1*{qty}*CA***IN*{item}", "CTT*1"]
            sets.append("~".join(body + [f"SE*{len(body) + 1}*{n:04d}"]))
        x12 = (f"ISA*00*          *00*          *ZZ*FRESHMART      *ZZ*TAYLORFARMS    *{date.today():%y%m%d}*1200*U*00401*{isa_ctrl}*0*T*>~"
               f"GS*PO*FRESHMART*TAYLORFARMS*{date.today():%Y%m%d}*1200*{int(isa_ctrl)}*X*004010~" + "~".join(sets) +
               f"~GE*2*{int(isa_ctrl)}~IEA*1*{isa_ctrl}~")
        edi = self.call("POST", "/exp/edi/v1/x12/850", content=x12, headers={"content-type": "text/plain"})
        self.check(len(edi["orders"]) == 2 and "AK9*A" in edi["ack997"], f"EDI result {edi}")
        group = edi["orderGroupId"]
        if simulator:
            e1 = edi["orders"][0]["orderId"]
            self.wait_status(e1, {"ACCEPTED"})
            self.call("POST", f"/system/v1/sim/orders/{e1}/advance", json={"to": "INVOICED"})
            self.wait_status(e1, {"INVOICED"})
            docs = self.call("GET", f"/process/v1/orders/{e1}/documents")["items"]
            kinds = {d["documentType"] for d in docs}
            for k in ("X12_850", "X12_997", "X12_855", "X12_856", "X12_810"):
                self.check(k in kinds, f"missing {k} in {kinds}")
        gh = self.call("GET", f"/process/v1/order-groups/{group}/history")
        self.check(len(gh["orderIds"]) == 2, "order group should contain two orders")

        self.step("bad price → exception queue → correction → resubmit")
        bad = self.call("POST", "/exp/portal/v1/orders", json={
            "channel": "MANUAL", "customerId": "C-1002", "shipToId": "ST-1002-01", "customerPo": f"BAD-{po}",
            "requestedDeliveryDate": self.delivery_date(["MON", "TUE", "WED", "THU", "FRI"]).isoformat(),
            "enteredBy": "smoke.cs.user", "lines": [{"itemNumber": "TF-20001", "quantity": 5, "uom": "CA", "unitPrice": 9.99}]})
        self.check(bad["status"] == "EXCEPTION" and any(e["code"] == "PRICE_MISMATCH" for e in bad["exceptions"]),
                   f"expected PRICE_MISMATCH exception: {bad}")
        q = self.call("GET", "/process/v1/exceptions")
        self.check(any(i["orderId"] == bad["orderId"] for i in q["items"]), "order not in exception queue")
        order = self.call("GET", f"/process/v1/orders/{bad['orderId']}")
        self.call("PATCH", f"/process/v1/orders/{bad['orderId']}", headers={"If-Match": f'"{order["version"]}"'},
                  json={"changes": {"lines": [{"lineNumber": 1, "unitPrice": None}]}, "reason": "Use contract price",
                        "actor": "smoke.cs.user"})
        res = self.call("POST", f"/process/v1/orders/{bad['orderId']}/resubmit", json={"actor": "smoke.cs.user"})
        self.check(res["status"] in ("SUBMITTED_TO_ERP", "ACCEPTED"), f"resubmit did not clear: {res}")

        self.step("Order Status API reads, search and KPIs")
        found = self.call("GET", "/process/v1/orders", params={"customerPo": po})
        self.check(found["total"] == 1, "search by PO failed")
        self.call("GET", f"/process/v1/orders/by-po/{po}", params={"customerId": "C-1001"})
        self.call("GET", "/process/v1/reference/statuses")
        k = self.call("GET", "/process/v1/metrics/kpis")
        self.check(k["ordersTotal"] >= 4, "KPI total too small")
        print(f"PASSED - {self.ok} checks")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8080/api")
    ap.add_argument("--api-key")
    ap.add_argument("--subscription-key")
    ap.add_argument("--timeout", type=float, default=30)
    a = ap.parse_args()
    try:
        Smoke(a.base, a.api_key, a.subscription_key, a.timeout).run()
        return 0
    except (AssertionError, httpx.HTTPError) as e:
        print(f"FAILED - {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
