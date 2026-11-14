"""Load realistic demo activity across every region, channel and account type through the public APIs.

    python -m tfso.tools.seed_demo --base http://localhost:8080/api --api-key local-dev-key

Creates portal, manual, partner and EDI orders for national accounts (FreshMart, Sunrise) and regional
accounts (Greenleaf, Bayside, Metro Fresh) shipping to the West, Southwest, Central, Southeast and Northeast,
then moves them through fulfilment with the simulator so the dashboard shows OTIF, fill rate, backorders,
cross-region sourcing, exceptions, credit holds and orders at risk.
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import uuid
from datetime import date, timedelta

import httpx

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def deliver_on(days_ok: tuple[str, ...], lead: int) -> str:
    d = date.today() + timedelta(days=lead)
    while DAYS[d.weekday()] not in days_ok:
        d += timedelta(days=1)
    return d.isoformat()


MON_SAT = ("MON", "TUE", "WED", "THU", "FRI", "SAT")
MON_FRI = ("MON", "TUE", "WED", "THU", "FRI")
# (customer, ship-to, receiving days, lines, channel, target stage)
PLAN = [
    ("C-1001", "ST-1001-01", MON_SAT, [("TF-10001", 40), ("TF-20001", 12)], "PORTAL", "INVOICED"),
    ("C-1001", "ST-1001-02", ("MON", "WED", "FRI"), [("TF-10002", 30)], "PORTAL", "DELIVERED"),
    ("C-1001", "ST-1001-03", MON_SAT, [("TF-10001", 60), ("TF-30001", 24)], "PORTAL", "INVOICED"),
    ("C-1001", "ST-1001-03", MON_SAT, [("TF-20002", 18)], "PORTAL", "SHIPPED"),
    ("C-1001", "ST-1001-04", MON_SAT, [("TF-10001", 35), ("TF-10002", 20)], "PORTAL", "INVOICED"),
    ("C-1001", "ST-1001-04", MON_SAT, [("TF-40001", 6)], "PORTAL", "ACCEPTED"),        # cross-region (only Salinas has broccoli)
    ("C-1001", "ST-1001-05", MON_SAT, [("TF-30002", 20), ("TF-20001", 10)], "PORTAL", "DELIVERED:late"),
    ("C-1002", "ST-1002-01", MON_FRI, [("TF-40001", 30), ("TF-20002", 10)], "PARTNER_API", "DELIVERED"),  # backorder → not in full
    ("C-1002", "ST-1002-02", MON_FRI, [("TF-20001", 40), ("TF-10001", 20)], "PARTNER_API", "INVOICED:late"),  # Southwest, late
    ("C-1002", "ST-1002-02", MON_FRI, [("TF-30001", 15)], "PARTNER_API", "SHIPPED"),
    ("C-1002", "ST-1002-03", MON_FRI, [("TF-20001", 50)], "PARTNER_API", "INVOICED"),
    ("C-1002", "ST-1002-03", MON_FRI, [("TF-20002", 25), ("TF-30001", 12)], "PARTNER_API", "RELEASED"),
    ("C-1003", "ST-1003-01", MON_FRI, [("TF-10001", 8), ("TF-30001", 6)], "MANUAL", "INVOICED"),
    ("C-1003", "ST-1003-02", ("MON", "WED", "FRI"), [("TF-10002", 220)], "PORTAL", "ACCEPTED"),  # credit hold
    ("C-1005", "ST-1005-01", tuple(DAYS), [("TF-30001", 48), ("TF-30002", 36)], "PARTNER_API", "INVOICED"),
    ("C-1005", "ST-1005-01", tuple(DAYS), [("TF-50001", 20)], "PARTNER_API", "PICKED"),
    ("C-1006", "ST-1006-01", MON_SAT, [("TF-10001", 20), ("TF-20001", 8)], "PORTAL", "INVOICED"),
    ("C-1006", "ST-1006-01", MON_SAT, [("TF-10002", 100)], "PORTAL", "SHIPPED"),        # alternate site / cross-region
    ("C-1006", "ST-1006-02", ("MON", "WED", "FRI"), [("TF-30001", 10)], "MANUAL", "ACCEPTED"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://localhost:8080/api")
    ap.add_argument("--api-key", default="local-dev-key")
    ap.add_argument("--subscription-key")
    a = ap.parse_args()
    h = {"x-api-key": a.api_key} if a.api_key else {}
    if a.subscription_key:
        h["Ocp-Apim-Subscription-Key"] = a.subscription_key
    c = httpx.Client(base_url=a.base.rstrip("/"), headers=h, timeout=60)
    rnd = random.Random(42)
    created = []
    print("Seeding demo orders across regions and channels…")
    for cust, ship, days, lines, channel, stage in PLAN:
        po = f"DEMO-{uuid.uuid4().hex[:6].upper()}"
        rdd = deliver_on(days, rnd.choice([3, 4, 5]))
        if channel == "PARTNER_API":
            r = c.post("/exp/partner/v1/orders", headers={"x-partner-id": "demo-partner"}, json={
                "customerPo": po, "soldTo": {"customerId": cust}, "shipTo": {"shipToId": ship}, "requestedDeliveryDate": rdd,
                "lines": [{"lineNumber": i, "item": {"itemNumber": it}, "quantity": {"value": q, "uom": "CA"}}
                          for i, (it, q) in enumerate(lines, 1)]})
        else:
            r = c.post("/exp/portal/v1/orders", json={
                "channel": channel, "customerId": cust, "shipToId": ship, "customerPo": po, "requestedDeliveryDate": rdd,
                "enteredBy": "demo.cs" if channel == "MANUAL" else None,
                "lines": [{"itemNumber": it, "quantity": q, "uom": "CA"} for it, q in lines]})
        r.raise_for_status()
        created.append((r.json()["orderId"], stage, rdd))
    # two EDI interchanges (FreshMart West + Central) and one exception (bad price) for the queue
    for st_code, item in (("FM-DC12", "FM-55012"), ("FM-DC40", "FM-55013")):
        ctrl = f"{int(time.time() * 1000) % 999999999:09d}"
        body = [f"ST*850*0001", f"BEG*00*SA*EDI{ctrl[-6:]}**{date.today():%Y%m%d}",
                f"DTM*002*{deliver_on(MON_SAT, 3).replace('-', '')}", f"N1*ST*FreshMart*92*{st_code}",
                f"PO1*1*48*CA***IN*{item}", "CTT*1"]
        x12 = (f"ISA*00*          *00*          *ZZ*FRESHMART      *ZZ*TAYLORFARMS    *{date.today():%y%m%d}*1200*U*00401*{ctrl}*0*P*>~"
               f"GS*PO*FRESHMART*TAYLORFARMS*{date.today():%Y%m%d}*1200*{int(ctrl)}*X*004010~" + "~".join(body)
               + f"~SE*{len(body) + 1}*0001~GE*1*{int(ctrl)}~IEA*1*{ctrl}~")
        res = c.post("/exp/edi/v1/x12/850", content=x12, headers={"content-type": "text/plain"})
        res.raise_for_status()
        created += [(o["orderId"], "INVOICED", None) for o in res.json()["orders"]]
        time.sleep(0.01)
    c.post("/exp/portal/v1/orders", json={"channel": "MANUAL", "customerId": "C-1002", "shipToId": "ST-1002-03",
                                          "customerPo": f"DEMO-BAD-{uuid.uuid4().hex[:4]}", "enteredBy": "demo.cs",
                                          "requestedDeliveryDate": deliver_on(MON_FRI, 4),
                                          "lines": [{"itemNumber": "TF-20001", "quantity": 10, "unitPrice": 12.0}]}).raise_for_status()
    time.sleep(1.5)  # let D365 (simulator) accept the orders
    for oid, stage, rdd in created:
        if stage in ("ACCEPTED",):
            continue
        body = {"to": stage.split(":")[0]}
        if stage.endswith(":late") and rdd:  # proof of delivery one day after the requested date
            late = date.fromisoformat(rdd) + timedelta(days=1)
            if stage.startswith("INVOICED"):
                c.post(f"/system/v1/sim/orders/{oid}/advance", json={"to": "DELIVERED", "deliveredAt": f"{late}T15:30:00Z"})
            else:
                body["deliveredAt"] = f"{late}T15:30:00Z"
        r = c.post(f"/system/v1/sim/orders/{oid}/advance", json=body)
        if r.status_code not in (200, 409):
            print(f"  ! {oid}: {r.status_code} {r.text[:120]}")
    time.sleep(1.0)
    k = c.get("/process/v1/metrics/kpis").json()
    print(f"Done: {k['ordersTotal']} orders · revenue ${k['revenue']:,.0f} · OTIF {k['otifRate']} · "
          f"fill {k['fillRate']} · at risk {k['ordersAtRisk']}")
    for r in k["byRegion"]:
        print(f"  {r['regionName']:<10} orders {r['orders']:>3}  revenue ${r['revenue']:>9,.0f}  OTIF {r['otifRate']}  fill {r['fillRate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
