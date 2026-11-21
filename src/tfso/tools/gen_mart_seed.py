"""Generate the synthetic Enterprise Data Mart seed (Vendor, Procurement, Logistics, Finance AP, Product cost).

    python -m tfso.tools.gen_mart_seed            # writes src/tfso/system/seed/mart.json

ILLUSTRATIVE DATA ONLY: names, vendors, carriers, prices and volumes are invented for demonstration and testing.
Deterministic (fixed random seed and anchor date) so tests and demos are repeatable. In production these datasets come
from D365 F&O (procurement, AP), the TMS and the Databricks gold layer through the System API.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "system" / "seed"
ANCHOR = date(2026, 11, 20)
rnd = random.Random(2026)

md = json.loads((ROOT / "masterdata.json").read_text())
SITES = [(r["regionId"], s["warehouse"], s["siteId"], s["name"]) for r in md["regions"] for s in r["sites"]]
REGION_SITES: dict[str, list] = {}
for reg, wh, sid, nm in SITES:
    REGION_SITES.setdefault(reg, []).append((wh, sid, nm))

VENDORS = [  # (name, category, home region, country, certification)
    ("Salinas Valley Greens", "LEAFY_GREENS", "WEST", "US", "PrimusGFS"),
    ("Yuma Desert Farms", "LEAFY_GREENS", "SOUTHWEST", "US", "PrimusGFS"),
    ("Central Coast Carrot Co", "VEGETABLES", "WEST", "US", "SQF"),
    ("Imperial Onion Growers", "VEGETABLES", "SOUTHWEST", "US", "PrimusGFS"),
    ("Sierra Madre Produce", "VEGETABLES", "SOUTHWEST", "MX", "GLOBALG.A.P."),
    ("Monterey Broccoli Partners", "VEGETABLES", "WEST", "US", "PrimusGFS"),
    ("Rio Grande Cabbage", "VEGETABLES", "CENTRAL", "US", "SQF"),
    ("Sunbelt Tropical Fruit", "FRUIT", "SOUTHEAST", "US", "BRCGS"),
    ("Pacifico Melon Exporters", "FRUIT", "SOUTHWEST", "MX", "GLOBALG.A.P."),
    ("Garden State Greens", "LEAFY_GREENS", "NORTHEAST", "US", "SQF"),
    ("Lake Country Lettuce", "LEAFY_GREENS", "CENTRAL", "US", "PrimusGFS"),
    ("Blue Ridge Vegetable Farms", "VEGETABLES", "SOUTHEAST", "US", "SQF"),
    ("ClearPak Packaging", "PACKAGING", "WEST", "US", "BRCGS Packaging"),
    ("EcoClam Containers", "PACKAGING", "CENTRAL", "US", "BRCGS Packaging"),
    ("Midwest Corrugated", "PACKAGING", "CENTRAL", "US", "SQF Packaging"),
    ("FlexFilm Solutions", "PACKAGING", "NORTHEAST", "US", "BRCGS Packaging"),
    ("Golden Gate Dressings", "INGREDIENTS", "WEST", "US", "SQF"),
    ("Hearthstone Bakery Ingredients", "INGREDIENTS", "CENTRAL", "US", "SQF"),
    ("Atlantic Herb Growers", "VEGETABLES", "NORTHEAST", "US", "PrimusGFS"),
    ("Valle Verde Cooperative", "LEAFY_GREENS", "SOUTHWEST", "MX", "GLOBALG.A.P."),
]
MATERIALS = [  # (id, description, commodity, uom, unit cost, vendor category)
    ("RM-1001", "Romaine hearts, bulk", "LEAFY_GREENS", "LB", 0.62, "LEAFY_GREENS"),
    ("RM-1002", "Spring mix greens, bulk", "LEAFY_GREENS", "LB", 1.45, "LEAFY_GREENS"),
    ("RM-1003", "Yellow onions, jumbo", "VEGETABLES", "LB", 0.38, "VEGETABLES"),
    ("RM-1004", "Carrots, cello", "VEGETABLES", "LB", 0.41, "VEGETABLES"),
    ("RM-1005", "Broccoli crowns", "VEGETABLES", "LB", 0.95, "VEGETABLES"),
    ("RM-1006", "Green cabbage", "VEGETABLES", "LB", 0.29, "VEGETABLES"),
    ("RM-1007", "Pineapple, whole", "FRUIT", "LB", 0.72, "FRUIT"),
    ("RM-1008", "Cantaloupe, whole", "FRUIT", "LB", 0.55, "FRUIT"),
    ("RM-2001", "Clamshell 2 lb, PET", "PACKAGING", "EA", 0.18, "PACKAGING"),
    ("RM-2002", "Salad kit film, printed", "PACKAGING", "ROLL", 86.00, "PACKAGING"),
    ("RM-2003", "Corrugated case, waxed", "PACKAGING", "EA", 1.12, "PACKAGING"),
    ("RM-3001", "Caesar dressing pouch 2 oz", "INGREDIENTS", "EA", 0.21, "INGREDIENTS"),
    ("RM-3002", "Seasoned croutons", "INGREDIENTS", "LB", 2.35, "INGREDIENTS"),
]
CARRIERS = [  # (scac, name, mode, regions, base cost per mile, on-time propensity)
    ("CRSF", "Cold Road Freight", "REEFER_TL", ["WEST", "SOUTHWEST", "CENTRAL"], 3.10, 0.95),
    ("VLYX", "Valley Reefer Express", "REEFER_TL", ["WEST", "SOUTHWEST"], 2.95, 0.91),
    ("FRZN", "Frontier Cold Chain", "REEFER_TL", ["CENTRAL", "SOUTHEAST", "NORTHEAST"], 3.25, 0.93),
    ("ATLC", "Atlantic Chill Logistics", "REEFER_TL", ["NORTHEAST", "SOUTHEAST"], 3.40, 0.89),
    ("PLNS", "Plains Temperature Carriers", "REEFER_LTL", ["CENTRAL"], 4.10, 0.86),
    ("GULF", "Gulf Coast Reefer", "REEFER_TL", ["SOUTHEAST", "CENTRAL"], 3.05, 0.92),
]
BUYERS = ["Pat Buyer", "Lee Moreno", "Dana Okafor", "Chris Lindqvist"]


def d(x: date) -> str:
    return x.isoformat()


def money(x: float) -> float:
    return round(x, 2)


def build() -> dict:
    vendors, scorecard_src = [], {}
    for i, (name, cat, reg, country, cert) in enumerate(VENDORS, start=1):
        vid = f"V-{1000 + i}"
        slug = "".join(ch for ch in name.lower() if ch.isalnum())[:12]
        vendors.append({
            "vendorId": vid, "name": name, "category": cat, "homeRegion": reg, "country": country,
            "status": "ON_HOLD" if i == 18 else "ACTIVE", "paymentTerms": rnd.choice(["Net30", "Net30", "Net45", "Net15"]),
            "certification": cert, "gfsiCertified": True, "riskTier": rnd.choice(["LOW", "LOW", "MEDIUM", "HIGH"]) if country == "MX" else rnd.choice(["LOW", "LOW", "LOW", "MEDIUM"]),
            "onboardedDate": d(ANCHOR - timedelta(days=rnd.randint(200, 3000))),
            "d365VendorAccount": f"VEND-{2000 + i}",
            "contactName": rnd.choice(["Alex", "Jordan", "Sam", "Rene", "Maria", "Luis", "Kim"]) + " " + rnd.choice(["Ortega", "Nguyen", "Smith", "Patel", "Garcia", "Chen"]),
            "contactEmail": f"sales@{slug}.example", "contactPhone": f"555-{rnd.randint(1000, 9999)}",
        })
    by_cat: dict[str, list] = {}
    for v in vendors:
        if v["status"] == "ACTIVE":
            by_cat.setdefault(v["category"], []).append(v)

    pos, lines, receipts, ap = [], [], [], []
    po_no, gr_no, vi_no = 4600100, 8800100, 9300100
    for _ in range(220):
        cat = rnd.choice(["LEAFY_GREENS"] * 4 + ["VEGETABLES"] * 4 + ["FRUIT"] * 2 + ["PACKAGING"] * 2 + ["INGREDIENTS"])
        v = rnd.choice(by_cat[cat])
        region = v["homeRegion"] if rnd.random() < 0.7 else rnd.choice(list(REGION_SITES))
        wh, sid, site_name = rnd.choice(REGION_SITES[region])
        order_date = ANCHOR - timedelta(days=rnd.randint(1, 95))
        lead = rnd.randint(1, 4) if cat in ("LEAFY_GREENS", "VEGETABLES", "FRUIT") else rnd.randint(5, 14)
        expected = order_date + timedelta(days=lead)
        po_no += 1
        po = f"PO-{po_no}"
        mats = [m for m in MATERIALS if m[5] == cat]
        po_lines, total, rec_total = [], 0.0, 0.0
        for ln, m in enumerate(rnd.sample(mats, k=min(len(mats), rnd.randint(1, 3))), start=1):
            qty = rnd.choice([2000, 4000, 6000, 10000, 20000]) if m[3] == "LB" else rnd.choice([500, 1000, 5000, 20000]) if m[3] == "EA" else rnd.choice([10, 20, 40])
            cost = money(m[4] * rnd.uniform(0.9, 1.15))
            due = expected <= ANCHOR
            received = (qty if rnd.random() < 0.8 else int(qty * rnd.uniform(0.6, 0.95))) if due else 0
            rejected = int(received * rnd.choice([0, 0, 0, 0, 0.01, 0.03, 0.08])) if m[2] != "PACKAGING" else 0
            amount = money(qty * cost)
            total += amount
            rec_total += money(received * cost)
            status = "OPEN" if not due else ("RECEIVED" if received >= qty else "PARTIALLY_RECEIVED")
            po_lines.append({"poNumber": po, "lineNumber": ln, "vendorId": v["vendorId"], "materialId": m[0],
                             "description": m[1], "commodity": m[2], "uom": m[3], "orderedQty": qty,
                             "receivedQty": received, "rejectedQty": rejected, "unitCost": cost, "lineAmount": amount,
                             "expectedDate": d(expected), "status": status, "region": region, "warehouse": wh})
            if received:
                gr_no += 1
                late = rnd.random() < (0.12 if v["riskTier"] == "LOW" else 0.28)
                received_on = expected + timedelta(days=rnd.randint(1, 3) if late else 0)
                result = "REJECT" if rejected > received * 0.05 else ("HOLD" if rejected else "PASS")
                receipts.append({"receiptId": f"GR-{gr_no}", "poNumber": po, "lineNumber": ln, "vendorId": v["vendorId"],
                                 "materialId": m[0], "warehouse": wh, "region": region, "receivedDate": d(received_on),
                                 "quantity": received, "rejectedQty": rejected, "uom": m[3],
                                 "lotNumber": f"VL{received_on:%y%j}{rnd.randint(100, 999)}",
                                 "pulpTempF": round(rnd.uniform(33.0, 41.5), 1) if m[2] != "PACKAGING" else None,
                                 "inspectionResult": result, "onTime": not late})
        any_received = any(x["receivedQty"] for x in po_lines)
        all_received = all(x["receivedQty"] >= x["orderedQty"] for x in po_lines)
        status = ("CANCELLED" if rnd.random() < 0.03 else
                  "CLOSED" if all_received and order_date < ANCHOR - timedelta(days=30) else
                  "RECEIVED" if all_received else "PARTIALLY_RECEIVED" if any_received else "OPEN")
        pos.append({"poNumber": po, "vendorId": v["vendorId"], "vendorName": v["name"], "category": cat,
                    "region": region, "warehouse": wh, "siteName": site_name, "buyer": rnd.choice(BUYERS),
                    "orderDate": d(order_date), "expectedDate": d(expected), "status": status,
                    "lineCount": len(po_lines), "totalAmount": money(total), "receivedAmount": money(rec_total),
                    "currency": "USD", "d365PurchaseOrder": f"D365-{po_no}"})
        lines.extend(po_lines)
        if any_received and status != "CANCELLED":
            vi_no += 1
            inv_date = expected + timedelta(days=rnd.randint(0, 5))
            terms = int(v["paymentTerms"].replace("Net", ""))
            match = rnd.choices(["MATCHED", "PRICE_VARIANCE", "QTY_VARIANCE"], [0.82, 0.1, 0.08])[0]
            billed = money(rec_total * (rnd.uniform(1.01, 1.06) if match == "PRICE_VARIANCE" else 1))
            due = inv_date + timedelta(days=terms)
            paid = due <= ANCHOR and match == "MATCHED"
            ap.append({"apInvoiceId": f"VI-{vi_no}", "vendorId": v["vendorId"], "vendorName": v["name"],
                       "vendorInvoiceNumber": f"{v['vendorId'][2:]}-{rnd.randint(10000, 99999)}", "poNumber": po,
                       "region": region, "invoiceDate": d(inv_date), "dueDate": d(due), "amount": billed,
                       "receivedValue": money(rec_total), "variance": money(billed - rec_total), "currency": "USD",
                       "matchStatus": match,
                       "status": "PAID" if paid else ("DISPUTED" if match != "MATCHED" else ("OPEN" if due > ANCHOR else "APPROVED")),
                       "paidDate": d(due - timedelta(days=rnd.randint(0, 3))) if paid else None})
        scorecard_src.setdefault(v["vendorId"], []).append((order_date, po_lines, total))

    # vendor scorecard by month
    scorecard = []
    rec_by_po = {}
    for r in receipts:
        rec_by_po.setdefault(r["poNumber"], []).append(r)
    po_by_no = {p["poNumber"]: p for p in pos}
    for v in vendors:
        months: dict[str, list] = {}
        for p in pos:
            if p["vendorId"] == v["vendorId"]:
                months.setdefault(p["orderDate"][:7], []).append(p)
        for month, plist in sorted(months.items()):
            recs = [r for p in plist for r in rec_by_po.get(p["poNumber"], [])]
            pl = [ln for ln in lines if ln["poNumber"] in {p["poNumber"] for p in plist}]
            ordered = sum(x["orderedQty"] for x in pl) or 1
            on_time = sum(1 for r in recs if r["onTime"]) / len(recs) if recs else None
            accept = 1 - sum(r["rejectedQty"] for r in recs) / max(1, sum(r["quantity"] for r in recs)) if recs else None
            fill = sum(x["receivedQty"] for x in pl) / ordered
            lead = sum((date.fromisoformat(po_by_no[p["poNumber"]]["expectedDate"]) - date.fromisoformat(p["orderDate"])).days for p in plist) / len(plist)
            score = (on_time or 0) * 0.4 + (accept or 0) * 0.4 + min(fill, 1) * 0.2
            scorecard.append({"month": month, "vendorId": v["vendorId"], "vendorName": v["name"], "category": v["category"],
                              "poCount": len(plist), "spend": money(sum(p["totalAmount"] for p in plist)),
                              "onTimeRate": round(on_time, 4) if on_time is not None else None,
                              "fillRate": round(min(fill, 1), 4),
                              "qualityAcceptRate": round(accept, 4) if accept is not None else None,
                              "avgLeadTimeDays": round(lead, 1),
                              "grade": "A" if score >= 0.95 else "B" if score >= 0.85 else "C"})

    spend: dict[tuple, dict] = {}
    for ln in lines:
        p = po_by_no[ln["poNumber"]]
        k = (p["orderDate"][:7], p["region"], ln["commodity"])
        row = spend.setdefault(k, {"month": k[0], "region": k[1], "commodity": k[2], "spend": 0.0, "qty": 0,
                                   "poNumbers": set(), "vendors": set()})
        row["spend"] += ln["lineAmount"]
        row["qty"] += ln["orderedQty"]
        row["poNumbers"].add(ln["poNumber"])
        row["vendors"].add(ln["vendorId"])
    spend_rows = [{"month": r["month"], "region": r["region"], "commodity": r["commodity"], "spend": money(r["spend"]),
                   "poCount": len(r["poNumbers"]), "vendorCount": len(r["vendors"]),
                   "avgUnitCost": round(r["spend"] / max(1, r["qty"]), 4)} for r in spend.values()]
    spend_rows.sort(key=lambda r: (r["month"], r["region"], r["commodity"]))

    carriers, loads = [], []
    ld_no = 7700100
    for scac, name, mode, regions, cpm, ontime_p in CARRIERS:
        carriers.append({"scac": scac, "name": name, "mode": mode, "regionsServed": regions, "status": "ACTIVE",
                         "baseCostPerMile": cpm, "insuranceExpires": d(ANCHOR + timedelta(days=rnd.randint(30, 400))),
                         "smartWayCertified": rnd.random() < 0.7})
    for _ in range(260):
        scac, name, mode, regions, cpm, ontime_p = rnd.choice(CARRIERS)
        origin_region = rnd.choice(regions)
        wh, sid, site_name = rnd.choice(REGION_SITES[origin_region])
        dest = rnd.choice(regions)
        ship = ANCHOR - timedelta(days=rnd.randint(0, 60))
        miles = rnd.randint(60, 420) if dest == origin_region else rnd.randint(450, 2100)
        transit = 1 if miles < 500 else 2 if miles < 1200 else 3
        on_time = rnd.random() < ontime_p
        delivered = ship + timedelta(days=transit + (0 if on_time else 1))
        cost = money(miles * cpm * rnd.uniform(0.92, 1.12))
        ld_no += 1
        loads.append({"loadId": f"LD-{ld_no}", "scac": scac, "carrierName": name, "mode": mode,
                      "originWarehouse": wh, "originRegion": origin_region, "destinationRegion": dest,
                      "shipDate": d(ship), "deliveryDate": d(delivered) if delivered <= ANCHOR else None,
                      "status": "DELIVERED" if delivered <= ANCHOR else "IN_TRANSIT", "stops": rnd.randint(1, 4),
                      "pallets": rnd.randint(8, 26), "miles": miles, "freightCost": cost,
                      "costPerMile": round(cost / miles, 2), "onTime": on_time if delivered <= ANCHOR else None,
                      "temperatureExcursion": rnd.random() < 0.03})
    for c in carriers:
        mine = [x for x in loads if x["scac"] == c["scac"]]
        done = [x for x in mine if x["onTime"] is not None]
        c["loads90d"] = len(mine)
        c["onTimeRate"] = round(sum(1 for x in done if x["onTime"]) / len(done), 4) if done else None
        c["avgCostPerMile"] = round(sum(x["costPerMile"] for x in mine) / len(mine), 2) if mine else None
        c["temperatureExcursions"] = sum(1 for x in mine if x["temperatureExcursion"])

    item_costs = []
    for it in md["items"]:
        list_price = float(it["listPrice"])
        raw = round(list_price * rnd.uniform(0.32, 0.42), 2)
        pack = round(list_price * rnd.uniform(0.08, 0.12), 2)
        labor = round(list_price * rnd.uniform(0.12, 0.18), 2)
        overhead = round(list_price * rnd.uniform(0.05, 0.09), 2)
        std = round(raw + pack + labor + overhead, 2)
        item_costs.append({"itemNumber": it["itemNumber"], "description": it["description"], "listPrice": list_price,
                           "standardCost": std, "rawMaterialCost": raw, "packagingCost": pack, "laborCost": labor,
                           "overheadCost": overhead, "marginPct": round((list_price - std) / list_price, 4),
                           "costVersion": "FY27-Q1", "costEffectiveDate": "2026-10-01", "active": it["active"]})

    return {"_note": "ILLUSTRATIVE synthetic data generated by tfso.tools.gen_mart_seed - not real Taylor Farms data.",
            "_anchorDate": d(ANCHOR),
            "datasets": {"vendors": vendors, "vendor-scorecard": scorecard, "purchase-orders": pos,
                         "purchase-order-lines": lines, "goods-receipts": receipts, "ap-invoices": ap,
                         "spend-by-category": spend_rows, "carriers": carriers, "carrier-loads": loads,
                         "item-costs": item_costs}}


def main() -> None:
    data = build()
    out = ROOT / "mart.json"
    out.write_text(json.dumps(data, indent=1))
    print("wrote", out, {k: len(v) for k, v in data["datasets"].items()})


if __name__ == "__main__":
    main()
