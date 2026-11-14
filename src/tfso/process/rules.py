"""Order quality rules (DQ-001..DQ-011) and master-data enrichment.

Rules are defined once and applied to every order regardless of channel (deck slide 3 / 12).
`validate()` resolves identities (customer, ship-to, items) and evaluates the rules; `enrich()` completes
the order from master data. Both work on the camelCase canonical document (dict).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..common.util import money, new_id, parse_date, utcnow
from .masterdata import MasterDataClient

PRICE_TOLERANCE = Decimal("0.005")   # ±0.5 %
CUTOFF_HOUR_UTC = 22                 # orders after 22:00 UTC (14:00 PT) count from the next day
EXTRACTION_MIN_CONFIDENCE = 0.80
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

RULES = {
    "DQ-001": ("Sold-to customer exists and is active", "ERROR"),
    "DQ-002": ("Ship-to exists, belongs to the customer and is active", "ERROR"),
    "DQ-003": ("Item exists and is active", "ERROR"),
    "DQ-004": ("Unit of measure is valid for the item", "ERROR"),
    "DQ-005": ("Price matches the customer agreement", "ERROR"),
    "DQ-006": ("No duplicate of an open order", "ERROR"),
    "DQ-007": ("Requested delivery date can be met", "ERROR"),
    "DQ-008": ("Quantity is positive, meets minimum and order increment", "ERROR"),
    "DQ-009": ("Required elements are present", "ERROR"),
    "DQ-010": ("Shelf life and temperature are compatible with the ship-to", "WARNING"),
    "DQ-011": ("Extraction confidence is sufficient", "ERROR"),
}


class Checks:
    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []

    def add(self, rule: str, passed: bool, message: str | None = None, *, line: int | None = None,
            field: str | None = None, code: str | None = None, severity: str | None = None) -> None:
        name, default_sev = RULES[rule]
        r = {"ruleId": rule, "name": name, "passed": passed, "severity": severity or default_sev}
        if message:
            r["message"] = message
        if line is not None:
            r["lineNumber"] = line
        if field:
            r["field"] = field
        if code:
            r["code"] = code
        self.results.append(r)

    @property
    def errors(self) -> list[dict]:
        return [r for r in self.results if not r["passed"] and r["severity"] == "ERROR"]


async def validate(order: dict[str, Any], md: MasterDataClient, find_duplicates) -> Checks:
    """Resolve references and evaluate every rule. `find_duplicates(customer_id, po, exclude_order_id)`
    returns open order ids with the same customer PO."""
    c = Checks()
    parties = order.setdefault("parties", {})
    sold = parties.setdefault("soldTo", {"role": "SOLD_TO"})
    ship = parties.setdefault("shipTo", {"role": "SHIP_TO"})
    po = (order.get("purchaseOrder") or {}).get("poNumber")
    dates = order.setdefault("dates", {})

    # DQ-011 extraction confidence (email / OCR)
    extraction = (order.get("source") or {}).get("extraction")
    if extraction:
        conf = float(extraction.get("confidence", 1))
        c.add("DQ-011", conf >= EXTRACTION_MIN_CONFIDENCE,
              None if conf >= EXTRACTION_MIN_CONFIDENCE else
              f"Extraction confidence {conf:.2f} below {EXTRACTION_MIN_CONFIDENCE}; fields: "
              f"{', '.join(extraction.get('fieldsLowConfidence', [])) or 'n/a'}", code="EXTRACTION_LOW_CONFIDENCE")

    # DQ-009 required elements
    missing = []
    if not po:
        missing.append("purchaseOrder.poNumber")
    if not (sold.get("partyId")):
        missing.append("parties.soldTo")
    if not any(ship.get(k) for k in ("partyId", "ediLocationCode", "gln", "name")):
        missing.append("parties.shipTo")
    if not order.get("lines"):
        missing.append("lines")
    if not dates.get("requestedDeliveryDate"):
        missing.append("dates.requestedDeliveryDate")
    for ln in order.get("lines", []):
        if not any((ln.get("item") or {}).get(k) for k in ("itemNumber", "buyerItemNumber", "gtin", "upc", "vendorItemNumber")):
            missing.append(f"lines[{ln['lineNumber']}].item")
        if (ln.get("quantity") or {}).get("value") in (None, ""):
            missing.append(f"lines[{ln['lineNumber']}].quantity")
    c.add("DQ-009", not missing, f"Missing required elements: {', '.join(missing)}" if missing else None,
          code="MISSING_REQUIRED_DATA", field=missing[0] if missing else None)

    # DQ-001 customer
    customer = await md.customer(sold.get("partyId")) if sold.get("partyId") else None
    if sold.get("partyId"):
        if customer is None:
            c.add("DQ-001", False, f"Customer {sold['partyId']} not found", code="CUSTOMER_NOT_FOUND", field="parties.soldTo.partyId")
        elif not customer.get("active"):
            c.add("DQ-001", False, f"Customer {customer['customerId']} is inactive"
                  + (f" ({customer['blockedReason']})" if customer.get("blockedReason") else ""),
                  code="CUSTOMER_INACTIVE", field="parties.soldTo.partyId")
        else:
            c.add("DQ-001", True)
            sold["partyId"] = customer["customerId"]
    cust_id = customer["customerId"] if customer else sold.get("partyId")

    # DQ-002 ship-to (resolve by id, EDI location code, GLN or name)
    st = None
    if ship.get("partyId"):
        st = await md.ship_to(ship["partyId"])
    if st is None and cust_id and any(ship.get(k) for k in ("ediLocationCode", "gln", "name")):
        st = await md.find_ship_to(cust_id, edi_code=ship.get("ediLocationCode"), gln=ship.get("gln"),
                                   name=None if ship.get("ediLocationCode") or ship.get("gln") else ship.get("name"))
    if any(ship.get(k) for k in ("partyId", "ediLocationCode", "gln", "name")):
        ref = ship.get("partyId") or ship.get("ediLocationCode") or ship.get("gln") or ship.get("name")
        if st is None:
            c.add("DQ-002", False, f"Ship-to {ref} not found for customer {cust_id}", code="SHIPTO_NOT_FOUND",
                  field="parties.shipTo")
        elif cust_id and st["customerId"] != cust_id:
            c.add("DQ-002", False, f"Ship-to {st['shipToId']} belongs to {st['customerId']}, not {cust_id}",
                  code="SHIPTO_CUSTOMER_MISMATCH", field="parties.shipTo")
        elif not st.get("active"):
            c.add("DQ-002", False, f"Ship-to {st['shipToId']} is inactive", code="SHIPTO_INACTIVE", field="parties.shipTo")
        else:
            c.add("DQ-002", True)
            ship["partyId"] = st["shipToId"]
            ship["customerId"] = st["customerId"]
            await _assign_demand_region(order, st, customer, md)

    # DQ-006 duplicate open order
    if cust_id and po:
        dups = find_duplicates(cust_id, po, order["orderId"])
        if order.get("purpose") in ("CHANGE", "CANCELLATION"):
            dups = []
        c.add("DQ-006", not dups, f"Open order(s) {', '.join(dups)} already exist for PO {po}" if dups else None,
              code="DUPLICATE_ORDER", field="purchaseOrder.poNumber")

    # Line rules
    rdd = parse_date(dates.get("requestedDeliveryDate"))
    max_lead = 1
    for ln in order.get("lines", []):
        n = ln["lineNumber"]
        item_ref = ln.setdefault("item", {})
        item = await md.resolve_item(cust_id, item_ref)
        if item is None:
            label = item_ref.get("itemNumber") or item_ref.get("buyerItemNumber") or item_ref.get("gtin") or item_ref.get("upc")
            c.add("DQ-003", False, f"Item {label} not recognised", line=n, field="item", code="ITEM_NOT_FOUND")
            continue
        if not item.get("active"):
            c.add("DQ-003", False, f"Item {item['itemNumber']} is not active for sale", line=n, field="item",
                  code="ITEM_INACTIVE")
            continue
        c.add("DQ-003", True, line=n)
        item_ref["itemNumber"] = item["itemNumber"]
        max_lead = max(max_lead, int(item.get("leadTimeDays", 1)))
        qty = ln.get("quantity") or {}
        uom = (qty.get("uom") or "").upper()
        qty["uom"] = uom
        if uom not in item.get("salesUoms", ["CA"]):
            c.add("DQ-004", False, f"UOM {uom or '(blank)'} not valid for {item['itemNumber']} "
                  f"(allowed: {', '.join(item.get('salesUoms', []))})", line=n, field="quantity.uom", code="UOM_INVALID")
        else:
            c.add("DQ-004", True, line=n)
        try:
            q = Decimal(str(qty.get("value")))
        except Exception:  # noqa: BLE001
            q = Decimal(-1)
        inc, mn = Decimal(str(item.get("orderIncrement", 1))), Decimal(str(item.get("minOrderQty", 1)))
        if q <= 0:
            c.add("DQ-008", False, f"Quantity {qty.get('value')} must be greater than zero", line=n,
                  field="quantity.value", code="QUANTITY_INVALID")
        elif q < mn or (q % inc) != 0:
            c.add("DQ-008", False, f"Quantity {q} must be ≥ {mn} and a multiple of {inc}", line=n,
                  field="quantity.value", code="QUANTITY_INCREMENT")
        else:
            c.add("DQ-008", True, line=n)
        # DQ-005 price vs agreement (only when the customer sent a price)
        price = (ln.get("unitPrice") or {}).get("amount")
        if price not in (None, "") and cust_id:
            agreement = await md.agreement(cust_id, item["itemNumber"], rdd.isoformat() if rdd else None)
            expected = Decimal(str(agreement["price"] if agreement else item["listPrice"]))
            if uom == "EA" and item.get("uomConversions", {}).get("EA"):
                expected = (expected / Decimal(str(item["uomConversions"]["EA"]))).quantize(Decimal("0.0001"))
            got = Decimal(str(price))
            ok = abs(got - expected) <= max(expected * PRICE_TOLERANCE, Decimal("0.01"))
            c.add("DQ-005", ok, None if ok else
                  f"Price {got} differs from {'agreement ' + agreement['agreementId'] if agreement else 'list price'} "
                  f"{expected} by more than 0.5%", line=n, field="unitPrice.amount", code="PRICE_MISMATCH")
        # DQ-010 shelf life / temperature
        if st:
            transit = int(st.get("transitDays", 1))
            msl = ln.get("minRemainingShelfLifeDays")
            if msl and int(item.get("shelfLifeDays", 0)) - transit < int(msl):
                c.add("DQ-010", False, f"Item shelf life {item.get('shelfLifeDays')}d minus transit {transit}d is below "
                      f"required {msl}d", line=n, code="SHELF_LIFE_RISK")
            elif item.get("temperatureZone") == "FROZEN" and st.get("temperatureZone") != "FROZEN":
                c.add("DQ-010", False, "Frozen item to a non-frozen receiving location", line=n, code="TEMPERATURE_MISMATCH")
            else:
                c.add("DQ-010", True, line=n)

    # DQ-007 requested date achievable
    if rdd:
        now = utcnow()
        earliest_ship = now.date() + timedelta(days=max_lead - 1 + (1 if now.hour >= CUTOFF_HOUR_UTC else 0))
        transit = int(st.get("transitDays", 1)) if st else 1
        latest_ship = rdd - timedelta(days=transit)
        if rdd < now.date():
            c.add("DQ-007", False, f"Requested delivery date {rdd} is in the past", field="dates.requestedDeliveryDate",
                  code="DATE_IN_PAST")
        elif latest_ship < earliest_ship:
            c.add("DQ-007", False, f"Delivery on {rdd} needs shipping by {latest_ship}; earliest ship date is "
                  f"{earliest_ship} (lead time {max_lead}d, transit {transit}d)", field="dates.requestedDeliveryDate",
                  code="DATE_NOT_ACHIEVABLE")
        else:
            c.add("DQ-007", True)
            if st and st.get("deliveryDays") and DAYS[rdd.weekday()] not in st["deliveryDays"]:
                c.add("DQ-007", False, f"{st['name']} does not receive on {DAYS[rdd.weekday()]} "
                      f"(receives {', '.join(st['deliveryDays'])}); delivery may move to the next receiving day",
                      field="dates.requestedDeliveryDate", code="NON_RECEIVING_DAY", severity="WARNING")
    return c


def apply_results(order: dict[str, Any], checks: Checks, actor: str) -> list[dict[str, Any]]:
    """Write data-quality results and (re)build the open exception list. Returns new exceptions."""
    now = utcnow().isoformat()
    dq = order.setdefault("dataQuality", {"ruleSetVersion": "1.0", "attempt": 0, "results": []})
    dq["attempt"] = int(dq.get("attempt", 0)) + 1
    dq["validatedAt"] = now
    dq["results"] = checks.results
    dq["passed"] = not checks.errors
    if dq.get("firstPass") is None:
        dq["firstPass"] = dq["passed"]
    total = len(checks.results) or 1
    dq["score"] = round(sum(1 for r in checks.results if r["passed"]) / total, 4)
    # resolve previously open validation exceptions; open new ones for each failing ERROR rule
    for ex in order.setdefault("exceptions", []):
        if ex.get("state") == "OPEN" and (ex.get("ruleId") or "").startswith("DQ-"):
            ex.update(state="RESOLVED", resolvedAt=now, resolvedBy=actor, resolutionNote="Passed re-validation")
    new = []
    for r in checks.errors:
        ex = {"exceptionId": new_id("ex-"), "code": r.get("code") or r["ruleId"], "ruleId": r["ruleId"],
              "severity": "ERROR", "message": r.get("message") or r["name"], "detectedAt": now,
              "detectedBy": "process-api", "state": "OPEN"}
        if r.get("lineNumber") is not None:
            ex["lineNumber"] = r["lineNumber"]
        if r.get("field"):
            ex["field"] = r["field"]
        order["exceptions"].append(ex)
        new.append(ex)
    roi = order["roi"]
    for ex in new:
        if ex["code"] not in roi.setdefault("exceptionReasonCodes", []):
            roi["exceptionReasonCodes"].append(ex["code"])
    return new


async def enrich(order: dict[str, Any], md: MasterDataClient) -> None:
    parties = order["parties"]
    sold, ship = parties["soldTo"], parties["shipTo"]
    customer = await md.customer(sold["partyId"])
    st = await md.ship_to(ship["partyId"])
    sold.update(role="SOLD_TO", name=customer["name"], erpAccount=customer["erpAccount"])
    for k in ("duns", "gln"):
        if customer.get(k):
            sold[k] = customer[k]
    if customer.get("contacts") and not sold.get("contacts"):
        sold["contacts"] = customer["contacts"]
    ship.update(role="SHIP_TO", customerId=st["customerId"], name=st["name"], erpAccount=st.get("erpLocationId"),
                address=st.get("address"), deliveryDays=st.get("deliveryDays", []),
                receivingHours=st.get("receivingHours"))
    for k in ("gln", "ediLocationCode"):
        if st.get(k):
            ship[k] = st[k]
    parties["billTo"] = {"role": "BILL_TO", "partyId": customer["customerId"], "customerId": customer["customerId"],
                         "erpAccount": customer.get("invoiceAccount") or customer["erpAccount"], "name": customer["name"]}
    parties["supplier"] = {"role": "SUPPLIER", "partyId": "TAYLOR-FARMS", "name": "Taylor Farms",
                           "duns": "000000000"}
    parties["shipFrom"] = {"role": "SHIP_FROM", "partyId": st.get("warehouse"), "name": st.get("site")}

    com = order.setdefault("commercial", {})
    com["currency"] = com.get("currency") or customer.get("currency", "USD")
    if customer.get("paymentTerms"):
        com["paymentTerms"] = {**customer["paymentTerms"], **{k: v for k, v in (com.get("paymentTerms") or {}).items()
                                                               if v and k != "code"}}
    com["customerPriceGroup"] = customer.get("priceGroup")
    if customer.get("salesResponsible"):
        com["salesResponsible"] = customer["salesResponsible"]

    shp = order.setdefault("shipping", {})
    shp.update({k: v for k, v in {"deliveryMode": st.get("deliveryMode"), "shipFromSite": st.get("site"),
                                  "shipFromWarehouse": st.get("warehouse"),
                                  "appointmentRequired": bool(st.get("appointmentRequired"))}.items()
                if v is not None and not shp.get(k)})
    shp.setdefault("transportResponsibility", "SUPPLIER_DELIVERED")
    shp["temperature"] = shp.get("temperature") or {"zone": st.get("temperatureZone", "REFRIGERATED"),
                                                    "minCelsius": "0.5", "maxCelsius": "4.0"}

    dates = order["dates"]
    rdd = parse_date(dates["requestedDeliveryDate"])
    if not dates.get("requestedShipDate"):
        dates["requestedShipDate"] = (rdd - timedelta(days=int(st.get("transitDays", 1)))).isoformat()
    dates.setdefault("orderDate", (order.get("purchaseOrder") or {}).get("poDate")
                     or order["source"]["receivedAt"][:10])

    subtotal, qty_total, weight, agreement_id = Decimal(0), Decimal(0), Decimal(0), None
    for ln in order["lines"]:
        item = await md.item(ln["item"]["itemNumber"])
        ln["item"].update({k: v for k, v in {
            "description": item.get("description"), "gtin": item.get("gtin"), "upc": item.get("upc"),
            "packSize": item.get("packSize"), "brand": item.get("brand"), "temperatureZone": item.get("temperatureZone"),
            "shelfLifeDays": item.get("shelfLifeDays"), "caseWeightLb": item.get("caseWeightLb"),
            "commodityCode": item.get("commodityCode")}.items() if v is not None and not ln["item"].get(k)})
        ln["item"]["description"] = item.get("description")  # canonical description wins
        uom = ln["quantity"]["uom"]
        q = Decimal(str(ln["quantity"]["value"]))
        agreement = await md.agreement(customer["customerId"], item["itemNumber"], rdd.isoformat())
        base = Decimal(str(agreement["price"] if agreement else item["listPrice"]))
        if uom == "EA" and item.get("uomConversions", {}).get("EA"):
            base = (base / Decimal(str(item["uomConversions"]["EA"]))).quantize(Decimal("0.0001"))
        price = ln.get("unitPrice") or {}
        if price.get("amount") in (None, ""):
            price = {"amount": str(base), "source": "CUSTOMER_AGREEMENT" if agreement else "LIST"}
        price.update(currency=com["currency"], basisUom=price.get("basisUom") or uom,
                     agreementPrice=str(base), listPrice=str(item["listPrice"]))
        if agreement:
            price["agreementId"] = agreement["agreementId"]
            agreement_id = agreement["agreementId"]
        ln["unitPrice"] = price
        amount = money(q * Decimal(str(price["amount"])))
        ln["lineAmount"] = str(amount)
        ln["requestedDeliveryDate"] = ln.get("requestedDeliveryDate") or dates["requestedDeliveryDate"]
        ln["requestedShipDate"] = ln.get("requestedShipDate") or dates["requestedShipDate"]
        qs = ln.setdefault("quantities", {})
        qs["ordered"] = str(q)
        ln["status"] = ln.get("status") or "OPEN"
        subtotal += amount
        qty_total += q
        cases = q / Decimal(str(item.get("uomConversions", {}).get(uom, 1)))
        weight += cases * Decimal(str(item.get("caseWeightLb") or 0))
    if agreement_id:
        com["priceAgreementId"] = agreement_id
    allowances, charges = Decimal(0), Decimal(0)
    for ac in order.get("allowancesCharges", []):
        amt = Decimal(str(ac.get("amount") or 0))
        if ac.get("indicator") == "ALLOWANCE":
            allowances += amt
        else:
            charges += amt
    order["totals"] = {"lineCount": len(order["lines"]), "totalQuantity": str(qty_total), "subtotal": str(money(subtotal)),
                       "allowances": str(money(allowances)), "charges": str(money(charges)), "tax": "0.00",
                       "grandTotal": str(money(subtotal - allowances + charges)), "currency": com["currency"],
                       "totalWeightLb": str(money(weight))}


def earliest_valid_delivery(st: dict | None, lead_days: int = 1) -> date:
    """Helper for clients: earliest delivery date that satisfies DQ-007 for a ship-to."""
    now = utcnow()
    d = now.date() + timedelta(days=lead_days - 1 + (1 if now.hour >= CUTOFF_HOUR_UTC else 0)
                               + int((st or {}).get("transitDays", 1)))
    days = (st or {}).get("deliveryDays") or DAYS
    while DAYS[d.weekday()] not in days:
        d += timedelta(days=1)
    return d


async def source(order: dict[str, Any], md: MasterDataClient) -> dict[str, Any]:
    """Network-aware sourcing: pick the processing site that ships the order.

    Candidates are evaluated in order - the ship-to region's primary site, its other sites, then adjacent
    regions, then the rest of the network. The first site that can fill every line from on-hand wins;
    otherwise the site with the best fill rate (home site wins ties). The decision is stored on the order
    (`fulfillment.sourcing`) so planners and customers can see *why* a site was chosen.
    """
    st = await md.ship_to(order["parties"]["shipTo"]["partyId"])
    customer = await md.customer(order["parties"]["soldTo"]["partyId"])
    regions = {r["regionId"]: r for r in await md.regions()}
    home_region = st.get("region") or next(iter(regions), None)
    ordered_regions = [home_region] + [r for r in (regions.get(home_region) or {}).get("adjacentRegions", []) if r in regions]
    ordered_regions += [r for r in regions if r not in ordered_regions]
    candidates: list[dict[str, Any]] = []
    for rid in ordered_regions:
        for site in sorted(regions[rid]["sites"], key=lambda x: not x.get("primary")):
            candidates.append({"warehouse": site["warehouse"], "siteId": site["siteId"], "siteName": site["name"],
                               "regionId": rid, "regionName": regions[rid]["name"],
                               "home": rid == home_region and bool(site.get("primary"))})
    on_hand: dict[tuple[str, str], float] = {(r["warehouse"], r["itemNumber"]): float(r["available"])
                                             for r in await md.inventory()}
    needs: list[tuple[str, float]] = []
    for ln in order["lines"]:
        item = await md.item(ln["item"]["itemNumber"])
        conv = float((item or {}).get("uomConversions", {}).get(ln["quantity"]["uom"], 1) or 1)
        needs.append((ln["item"]["itemNumber"], float(ln["quantity"]["value"]) / conv))
    total = sum(q for _, q in needs) or 1.0
    for c in candidates:
        c["fillRate"] = round(sum(min(q, on_hand.get((c["warehouse"], i), 0.0)) for i, q in needs) / total, 4)
    chosen = next((c for c in candidates if c["fillRate"] >= 1.0), None)
    if chosen is None:
        chosen = max(candidates, key=lambda c: (c["fillRate"], c["home"]))
        rule = "BEST_AVAILABLE"
        reason = (f"No site can fill the whole order; {chosen['siteName']} has the best fill "
                  f"({chosen['fillRate']:.0%}) - remaining quantity will be backordered")
    elif chosen["home"]:
        rule, reason = "HOME_SITE", f"{chosen['siteName']} (home site for {chosen['regionName']}) can fill the order"
    elif chosen["regionId"] == home_region:
        rule, reason = "ALTERNATE_SITE_IN_REGION", (f"Home site short on inventory; {chosen['siteName']} in the same "
                                                    f"region fills the order")
    else:
        rule, reason = "CROSS_REGION", (f"No {regions[home_region]['name']} site can fill the order; sourced from "
                                        f"{chosen['siteName']} ({chosen['regionName']}) - extra freight and transit risk")
    for c in candidates:
        c["chosen"] = c is chosen
    order["fulfillment"] = {
        "regionId": chosen["regionId"], "regionName": chosen["regionName"], "customerRegionId": home_region,
        "siteId": chosen["siteId"], "warehouse": chosen["warehouse"], "siteName": chosen["siteName"],
        "accountScope": (customer or {}).get("accountScope"), "crossRegion": chosen["regionId"] != home_region,
        "sourcing": {"rule": rule, "reason": reason, "decidedAt": utcnow().isoformat(),
                     "candidates": [{k: c[k] for k in ("warehouse", "siteId", "regionId", "fillRate", "home", "chosen")}
                                    for c in candidates]},
    }
    shp = order.setdefault("shipping", {})
    shp["shipFromSite"], shp["shipFromWarehouse"] = chosen["siteId"], chosen["warehouse"]
    order["parties"]["shipFrom"] = {"role": "SHIP_FROM", "partyId": chosen["warehouse"], "name": chosen["siteName"]}
    return order["fulfillment"]


async def _assign_demand_region(order: dict[str, Any], st: dict, customer: dict | None, md: MasterDataClient) -> None:
    """As soon as the ship-to is known, the order belongs to its demand region - so regional teams see their
    exceptions even when the order never reaches sourcing. Sourcing later confirms or changes the region."""
    ful = order.get("fulfillment") or {}
    if ful.get("sourcing") or not st.get("region"):
        return
    regions = {r["regionId"]: r for r in await md.regions()}
    order["fulfillment"] = {"regionId": st["region"], "regionName": (regions.get(st["region"]) or {}).get("name"),
                            "customerRegionId": st["region"], "accountScope": (customer or {}).get("accountScope"),
                            "crossRegion": False}
