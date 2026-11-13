"""ANSI ASC X12 support: parse 850 purchase orders; generate 997, 855, 856 and 810.

The translator is deliberately dependency-free. In Azure the production EDI path terminates AS2/VAN in
Logic Apps (Integration Account X12 decode) and posts the decoded interchange to the Experience API;
this module is the same contract used for portal/API uploads and for local end-to-end testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .util import utcnow


class X12Error(ValueError):
    pass


@dataclass
class Delimiters:
    element: str = "*"
    segment: str = "~"
    component: str = ">"
    repetition: str = "^"


@dataclass
class TransactionSet:
    control_number: str
    segments: list[list[str]]
    errors: list[str] = field(default_factory=list)


@dataclass
class Interchange:
    delimiters: Delimiters
    isa: list[str]
    gs: list[str]
    transactions: list[TransactionSet]
    errors: list[str] = field(default_factory=list)

    @property
    def sender_id(self) -> str:
        return self.isa[6].strip()

    @property
    def receiver_id(self) -> str:
        return self.isa[8].strip()

    @property
    def control_number(self) -> str:
        return self.isa[13].strip()

    @property
    def group_control_number(self) -> str:
        return self.gs[6].strip() if len(self.gs) > 6 else ""

    @property
    def functional_id(self) -> str:
        return self.gs[1].strip() if len(self.gs) > 1 else ""

    @property
    def version(self) -> str:
        return self.gs[8].strip() if len(self.gs) > 8 else ""


def _split(raw: str) -> tuple[Delimiters, list[list[str]]]:
    raw = raw.lstrip("﻿").strip()
    if not raw.startswith("ISA") or len(raw) < 106:
        raise X12Error("Interchange must start with a 106-character ISA segment")
    d = Delimiters(element=raw[3], component=raw[104], segment=raw[105])
    if d.segment in ("\r", "\n"):
        d.segment = "\n"
    isa_parts = raw[:105].split(d.element)
    if len(isa_parts) >= 12:
        rep = isa_parts[11]
        d.repetition = rep if rep not in ("U", "") else "^"
    segs = []
    for chunk in raw.split(d.segment):
        chunk = chunk.strip("\r\n \t")
        if chunk:
            segs.append(chunk.split(d.element))
    return d, segs


def parse_interchange(raw: str) -> Interchange:
    d, segs = _split(raw)
    isa = segs[0]
    if len(isa) < 17:
        raise X12Error("ISA segment has fewer than 16 elements")
    gs: list[str] = []
    txs: list[TransactionSet] = []
    errors: list[str] = []
    current: TransactionSet | None = None
    st_count = 0
    for seg in segs[1:]:
        tag = seg[0]
        if tag == "GS":
            gs = seg
        elif tag == "ST":
            current = TransactionSet(control_number=seg[2] if len(seg) > 2 else "", segments=[seg])
            if len(seg) < 2 or seg[1] != "850":
                current.errors.append(f"Unsupported transaction set {seg[1] if len(seg) > 1 else '?'}")
        elif tag == "SE":
            if current is None:
                errors.append("SE without ST")
                continue
            current.segments.append(seg)
            declared = int(seg[1]) if len(seg) > 1 and seg[1].isdigit() else -1
            if declared != len(current.segments):
                current.errors.append(f"SE01 segment count {declared} != actual {len(current.segments)}")
            if len(seg) > 2 and seg[2] != current.control_number:
                current.errors.append("SE02 does not match ST02")
            txs.append(current)
            st_count += 1
            current = None
        elif tag == "GE":
            if len(seg) > 1 and seg[1].isdigit() and int(seg[1]) != st_count:
                errors.append(f"GE01 transaction count {seg[1]} != actual {st_count}")
        elif tag == "IEA":
            pass
        elif current is not None:
            current.segments.append(seg)
    if not gs:
        raise X12Error("Missing GS functional group header")
    if current is not None:
        current.errors.append("Transaction set not terminated by SE")
        txs.append(current)
    if not txs:
        raise X12Error("Interchange contains no transaction sets")
    return Interchange(delimiters=d, isa=isa, gs=gs, transactions=txs, errors=errors)


def _el(seg: list[str], i: int) -> str | None:
    return seg[i].strip() if len(seg) > i and seg[i].strip() != "" else None


BEG_PURPOSE = {"00": "ORIGINAL", "01": "CANCELLATION", "04": "CHANGE", "05": "REPLACE", "06": "CONFIRMATION"}
BEG_TYPE = {"SA": "STANDARD", "RL": "BLANKET_RELEASE", "DS": "DROP_SHIP", "RO": "RUSH", "KN": "STANDING",
            "NE": "STANDARD", "OS": "SAMPLE"}


def read_850(tx: TransactionSet) -> dict[str, Any]:
    """Translate one 850 transaction set into a channel-neutral order draft (camelCase dict)."""
    draft: dict[str, Any] = {"references": [], "notes": [], "contacts": [], "parties": {}, "lines": [],
                             "dates": {}, "allowancesCharges": [], "shipping": {}}
    party: dict[str, Any] | None = None
    line: dict[str, Any] | None = None
    for seg in tx.segments:
        tag = seg[0]
        if tag == "BEG":
            draft["purpose"] = BEG_PURPOSE.get(_el(seg, 1) or "00", "ORIGINAL")
            draft["orderType"] = BEG_TYPE.get(_el(seg, 2) or "SA", "STANDARD")
            draft["purchaseOrder"] = {"poNumber": _el(seg, 3), "releaseNumber": _el(seg, 4), "poDate": _el(seg, 5)}
        elif tag == "CUR":
            draft["currency"] = _el(seg, 2)
        elif tag == "REF":
            q, v = _el(seg, 1), _el(seg, 2) or _el(seg, 3)
            draft["references"].append({"qualifier": q, "value": v, "description": _el(seg, 3)})
            if q == "DP":
                draft["purchaseOrder"]["department"] = v
            elif q == "CT":
                draft["purchaseOrder"]["contractNumber"] = v
            elif q == "PD":
                draft["purchaseOrder"]["promotionDealNumber"] = v
        elif tag == "PER":
            draft["contacts"].append({"role": _el(seg, 1), "name": _el(seg, 2),
                                      **{("phone" if _el(seg, i) == "TE" else "email"): _el(seg, i + 1)
                                         for i in (3, 5) if _el(seg, i) in ("TE", "EM")}})
        elif tag == "FOB":
            draft.setdefault("commercial", {})["fobPoint"] = _el(seg, 1)
        elif tag == "ITD":
            draft.setdefault("commercial", {})["paymentTerms"] = {
                "code": f"X12-{_el(seg, 1) or '01'}", "discountPercent": _el(seg, 3),
                "discountDays": _el(seg, 5), "netDays": _el(seg, 7), "description": _el(seg, 12)}
        elif tag == "DTM":
            code, d = _el(seg, 1), _el(seg, 2)
            key = {"002": "requestedDeliveryDate", "010": "requestedShipDate", "001": "cancelByDate",
                   "037": "requestedShipDate", "063": "requestedDeliveryDate", "064": "requestedDeliveryDate"}.get(code or "")
            if key and d:
                if line is not None:
                    line[key] = d
                else:
                    draft["dates"][key] = d
        elif tag == "TD5":
            draft["shipping"].update({"carrierScac": _el(seg, 3), "routing": _el(seg, 5)})
        elif tag == "SAC":
            target = line.setdefault("allowancesCharges", []) if line is not None else draft["allowancesCharges"]
            amt = _el(seg, 5)
            target.append({"indicator": "ALLOWANCE" if _el(seg, 1) == "A" else "CHARGE", "code": _el(seg, 2),
                           "amount": str(int(amt) / 100) if amt and amt.isdigit() else amt,
                           "description": _el(seg, 15)})
        elif tag == "MSG" or tag == "NTE":
            text = _el(seg, 1) if tag == "MSG" else _el(seg, 2)
            if text:
                (line.setdefault("notes", []) if line is not None else draft["notes"]).append({"type": "CUSTOMER", "text": text})
        elif tag == "N1":
            code = _el(seg, 1)
            role = {"ST": "shipTo", "BT": "billTo", "BY": "buyer", "SF": "shipFrom", "SO": "soldTo"}.get(code or "")
            party = {"name": _el(seg, 2)}
            q, ident = _el(seg, 3), _el(seg, 4)
            if q == "UL":
                party["gln"] = ident
            elif q == "1":
                party["duns"] = ident
            elif ident:
                party["ediLocationCode"] = ident
            if role:
                draft["parties"][role] = party
        elif tag == "N3" and party is not None:
            party.setdefault("address", {})["line1"] = _el(seg, 1)
            if _el(seg, 2):
                party["address"]["line2"] = _el(seg, 2)
        elif tag == "N4" and party is not None:
            party.setdefault("address", {}).update({"city": _el(seg, 1), "stateProvince": _el(seg, 2),
                                                    "postalCode": _el(seg, 3), "country": _el(seg, 4) or "US"})
        elif tag == "PO1":
            line = {"buyerLineNumber": _el(seg, 1), "quantity": {"value": _el(seg, 2), "uom": _el(seg, 3)},
                    "item": {}}
            if _el(seg, 4):
                line["unitPrice"] = {"amount": _el(seg, 4), "basisUom": _el(seg, 5), "source": "PURCHASE_ORDER"}
            i = 6
            while i + 1 < len(seg):
                q, v = _el(seg, i), _el(seg, i + 1)
                if q and v:
                    k = {"IN": "buyerItemNumber", "BP": "buyerItemNumber", "VN": "vendorItemNumber",
                         "VP": "vendorItemNumber", "UP": "upc", "UK": "gtin", "EN": "gtin"}.get(q)
                    if k:
                        line["item"][k] = v
                i += 2
            draft["lines"].append(line)
        elif tag == "PID" and line is not None:
            line["item"]["description"] = _el(seg, 5)
        elif tag == "CTT":
            draft["declaredLineCount"] = _el(seg, 1)
    # attach contacts to buyer
    if draft["contacts"]:
        draft["parties"].setdefault("buyer", {})["contacts"] = draft["contacts"]
    return draft


# --------------------------------------------------------------------------- outbound generation
class X12Writer:
    def __init__(self, sender_id: str, receiver_id: str, control_number: int, *, usage: str = "P",
                 sender_qual: str = "ZZ", receiver_qual: str = "ZZ", version: str = "004010"):
        self.sender_id, self.receiver_id = sender_id, receiver_id
        self.control = control_number
        self.usage, self.sq, self.rq, self.version = usage, sender_qual, receiver_qual, version
        self.now = utcnow()

    def _isa(self) -> str:
        return ("ISA*00*          *00*          *{sq}*{s:<15}*{rq}*{r:<15}*{d}*{t}*U*00401*{c:09d}*0*{u}*>"
                .format(sq=self.sq, s=self.sender_id[:15], rq=self.rq, r=self.receiver_id[:15],
                        d=self.now.strftime("%y%m%d"), t=self.now.strftime("%H%M"), c=self.control, u=self.usage))

    def build(self, functional_id: str, transaction_sets: list[list[str]]) -> str:
        segs = [self._isa(),
                f"GS*{functional_id}*{self.sender_id}*{self.receiver_id}*{self.now:%Y%m%d}*{self.now:%H%M}*{self.control}*X*{self.version}"]
        for i, body in enumerate(transaction_sets, start=1):
            st_ctrl = f"{i:04d}"
            ts_code = body[0]
            seg_list = [f"ST*{ts_code}*{st_ctrl}"] + body[1:]
            seg_list.append(f"SE*{len(seg_list) + 1}*{st_ctrl}")
            segs.extend(seg_list)
        segs.append(f"GE*{len(transaction_sets)}*{self.control}")
        segs.append(f"IEA*1*{self.control:09d}")
        return "~\n".join(segs) + "~"


def _d(dt: datetime | Any) -> str:
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y%m%d")
    return str(dt).replace("-", "")[:8]


def build_997(ic: Interchange, results: dict[str, str], our_id: str, control: int) -> str:
    """results: {ST02: 'A'|'E'|'R'}. AK9 summarises the group."""
    w = X12Writer(our_id, ic.sender_id, control)
    body = ["997", f"AK1*{ic.functional_id}*{ic.group_control_number}"]
    for tx in ic.transactions:
        code = results.get(tx.control_number, "R")
        body.append(f"AK2*850*{tx.control_number}")
        if tx.errors:
            body.append("AK3*SE*1**4")
        body.append(f"AK5*{code}" + ("" if code == "A" else "*5"))
    accepted = sum(1 for v in results.values() if v in ("A", "E"))
    group_code = "A" if accepted == len(ic.transactions) else ("P" if accepted else "R")
    body.append(f"AK9*{group_code}*{len(ic.transactions)}*{len(ic.transactions)}*{accepted}")
    return w.build("FA", [body])


def build_855(order: dict[str, Any], our_id: str, partner_id: str, control: int) -> str:
    po = order.get("purchaseOrder", {})
    status_changed = any((ln.get("quantities", {}).get("backordered") not in (None, "0", 0, "0.00"))
                         for ln in order.get("lines", []))
    body = ["855", f"BAK*00*{'AC' if status_changed else 'AD'}*{po.get('poNumber', '')}*{_d(po.get('poDate') or utcnow())}"
                   f"****{(order.get('erp') or {}).get('salesOrderId', '')}*{_d(utcnow())}",
            f"REF*VN*{(order.get('erp') or {}).get('salesOrderId', order['orderId'])}"]
    if order.get("dates", {}).get("promisedShipDate"):
        body.append(f"DTM*068*{_d(order['dates']['promisedShipDate'])}")
    st = order.get("parties", {}).get("shipTo", {})
    body.append(f"N1*ST*{st.get('name', '')}*92*{st.get('ediLocationCode') or st.get('partyId', '')}")
    for ln in order.get("lines", []):
        q = ln.get("quantity", {})
        price = (ln.get("unitPrice") or {}).get("amount", "")
        item = ln.get("item", {})
        body.append(f"PO1*{ln.get('buyerLineNumber') or ln['lineNumber']}*{q.get('value')}*{q.get('uom')}*{price}**"
                    f"IN*{item.get('buyerItemNumber') or ''}*VN*{item.get('itemNumber') or ''}")
        qs = ln.get("quantities", {})
        bo = float(qs.get("backordered") or 0)
        conf = float(qs.get("confirmed") or 0)
        if bo > 0:
            body.append(f"ACK*IB*{conf:g}*{q.get('uom')}")
            body.append(f"ACK*BP*{bo:g}*{q.get('uom')}")
        else:
            body.append(f"ACK*IA*{conf or q.get('value')}*{q.get('uom')}*068*{_d(order.get('dates', {}).get('promisedShipDate') or utcnow())}")
    body.append(f"CTT*{len(order.get('lines', []))}")
    return X12Writer(our_id, partner_id, control).build("PR", [body])


def build_856(order: dict[str, Any], shipment: dict[str, Any], our_id: str, partner_id: str, control: int) -> str:
    po = order.get("purchaseOrder", {})
    st = order.get("parties", {}).get("shipTo", {})
    hl = 1
    body = ["856", f"BSN*00*{shipment.get('asnNumber') or shipment['shipmentId']}*{_d(utcnow())}*{utcnow():%H%M}*0001",
            f"HL*{hl}**S", "TD1*CTN25*" + str(sum(float(x.get('quantity', 0)) for x in shipment.get("lines", []))),
            f"TD5**2*{shipment.get('carrierScac') or ''}*M*{shipment.get('carrierName') or ''}",
            f"REF*BM*{shipment.get('billOfLading') or ''}", f"REF*CN*{shipment.get('proNumber') or shipment.get('trackingNumber') or ''}",
            f"DTM*011*{_d(shipment.get('shippedAt') or utcnow())}",
            f"N1*ST*{st.get('name', '')}*92*{st.get('ediLocationCode') or st.get('partyId', '')}",
            f"N1*SF*Taylor Farms*92*{shipment.get('shipFromWarehouse') or ''}"]
    hl += 1
    body += [f"HL*{hl}*1*O", f"PRF*{po.get('poNumber', '')}"]
    order_hl = hl
    lines_by_no = {ln["lineNumber"]: ln for ln in order.get("lines", [])}
    for sl in shipment.get("lines", []):
        hl += 1
        ln = lines_by_no.get(sl["lineNumber"], {})
        item = ln.get("item", {})
        body += [f"HL*{hl}*{order_hl}*I",
                 f"LIN*{ln.get('buyerLineNumber') or sl['lineNumber']}*IN*{item.get('buyerItemNumber') or ''}*VN*{item.get('itemNumber') or sl.get('itemNumber') or ''}*UK*{item.get('gtin') or ''}",
                 f"SN1**{sl['quantity']}*{sl.get('uom') or ln.get('quantity', {}).get('uom', 'CA')}"]
        for lot in sl.get("lots", []):
            body.append(f"REF*LT*{lot.get('lotNumber')}")
    body.append(f"CTT*{len(shipment.get('lines', []))}")
    return X12Writer(our_id, partner_id, control).build("SH", [body])


def build_810(order: dict[str, Any], invoice: dict[str, Any], our_id: str, partner_id: str, control: int) -> str:
    po = order.get("purchaseOrder", {})
    lines_by_no = {ln["lineNumber"]: ln for ln in order.get("lines", [])}
    body = ["810", f"BIG*{_d(invoice['invoiceDate'])}*{invoice['invoiceId']}**{po.get('poNumber', '')}",
            f"REF*VN*{(order.get('erp') or {}).get('salesOrderId', '')}",
            f"N1*ST*{order.get('parties', {}).get('shipTo', {}).get('name', '')}"]
    terms = (order.get("commercial") or {}).get("paymentTerms") or {}
    if terms.get("netDays"):
        body.append(f"ITD*01*3*****{_d(invoice.get('dueDate') or invoice['invoiceDate'])}*{terms['netDays']}")
    for il in invoice.get("lines", []):
        ln = lines_by_no.get(il["lineNumber"], {})
        body.append(f"IT1*{ln.get('buyerLineNumber') or il['lineNumber']}*{il['quantity']}*{ln.get('quantity', {}).get('uom', 'CA')}"
                    f"*{il.get('unitPrice') or ''}**IN*{ln.get('item', {}).get('buyerItemNumber') or ''}*VN*{ln.get('item', {}).get('itemNumber') or ''}")
    total_cents = int(round(float(invoice["amount"]) * 100))
    body += [f"TDS*{total_cents}", f"CTT*{len(invoice.get('lines', []))}"]
    return X12Writer(our_id, partner_id, control).build("IN", [body])
