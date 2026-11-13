"""Unit tests: state machine, X12 translation, e-mail extraction, JSON diff."""
from __future__ import annotations

from tfso.common import x12
from tfso.common.statuses import OrderStatus as S, can_transition, is_stale
from tfso.experience.extract import parse_table, parse_text
from tfso.process.service import json_diff

SAMPLE = ("ISA*00*          *00*          *ZZ*FRESHMART      *ZZ*TAYLORFARMS    *261113*1200*U*00401*000000123*0*T*>~"
          "GS*PO*FRESHMART*TAYLORFARMS*20261113*1200*123*X*004010~ST*850*0001~BEG*00*SA*PO-9**20261110~"
          "REF*DP*042~PER*BD*Pat*TE*5550101*EM*pat@x.example~DTM*002*20261120~TD5**2*CRSF~"
          "N1*ST*FreshMart DC 12*92*FM-DC12~N3*1200 Distribution Way~N4*Stockton*CA*95206*US~"
          "PO1*1*40*CA*24.50*PE*IN*FM-55012*VN*TF-10001*UK*10614141100011~PID*F****Chopped Romaine~"
          "SAC*A*C310***2500~CTT*1~SE*14*0001~GE*1*123~IEA*1*000000123~")


def test_state_machine():
    assert can_transition(S.SUBMITTED_TO_ERP, S.ACCEPTED)
    assert can_transition(S.ACCEPTED, S.SHIPPED)  # forward skip allowed (WMS ships without pick event)
    assert not can_transition(S.SHIPPED, S.PICKED)
    assert not can_transition(S.CLOSED, S.ACCEPTED)
    assert can_transition(S.PARTIALLY_SHIPPED, S.PARTIALLY_SHIPPED)
    assert can_transition(S.EXCEPTION, S.INGESTED)
    assert is_stale(S.SHIPPED, S.PICKED) and not is_stale(S.PICKED, S.SHIPPED)


def test_parse_850():
    ic = x12.parse_interchange(SAMPLE)
    assert ic.sender_id == "FRESHMART" and ic.control_number == "000000123" and not ic.transactions[0].errors
    d = x12.read_850(ic.transactions[0])
    assert d["purchaseOrder"] == {"poNumber": "PO-9", "releaseNumber": None, "poDate": "20261110", "department": "042"}
    assert d["dates"]["requestedDeliveryDate"] == "20261120"
    assert d["parties"]["shipTo"]["ediLocationCode"] == "FM-DC12"
    assert d["parties"]["shipTo"]["address"]["city"] == "Stockton"
    line = d["lines"][0]
    assert line["item"] == {"buyerItemNumber": "FM-55012", "vendorItemNumber": "TF-10001", "gtin": "10614141100011",
                            "description": "Chopped Romaine"}
    assert line["quantity"] == {"value": "40", "uom": "CA"} and line["unitPrice"]["amount"] == "24.50"
    assert d["shipping"]["carrierScac"] == "CRSF"
    assert line["allowancesCharges"][0]["amount"] == "25.0"  # SAC after PO1 is line-level


def test_segment_count_error_yields_rejection():
    broken = SAMPLE.replace("SE*14*0001", "SE*99*0001")
    ic = x12.parse_interchange(broken)
    assert ic.transactions[0].errors
    ack = x12.build_997(ic, {"0001": "R"}, "TAYLORFARMS", 5)
    assert "AK5*R" in ack and "AK9*R*1*1*0" in ack


def test_997_structure():
    ic = x12.parse_interchange(SAMPLE)
    ack = x12.build_997(ic, {"0001": "A"}, "TAYLORFARMS", 7)
    segs = [s.strip() for s in ack.split("~") if s.strip()]
    assert segs[0].startswith("ISA") and segs[2] == "ST*997*0001" and "AK1*PO*123" in segs
    se = next(s for s in segs if s.startswith("SE*"))
    assert int(se.split("*")[1]) == len([s for s in segs if not s.startswith(("ISA", "GS", "GE", "IEA"))])


def test_email_text_and_table_parsing():
    p = parse_text("PO #: GL-123\nCustomer: C-1003\nShip-To: ST-1003-01\nDeliver: 11/20/2026\n"
                   "TF-10001 10 CA @ 24.50\n2) TF-30001, 5 cases\n")
    assert p["header"] == {"poNumber": "GL-123", "customerId": "C-1003", "shipTo": "ST-1003-01", "deliveryDate": "11/20/2026"}
    assert p["lines"] == [{"itemNumber": "TF-10001", "quantity": "10", "uom": "CA", "unitPrice": "24.50"},
                          {"itemNumber": "TF-30001", "quantity": "5", "uom": "CA", "unitPrice": None}]
    rows = parse_table([["SKU", "Quantity", "Unit", "Unit Price"], ["TF-10002", 3, "cs", 31.0], [None, None, None, None]])
    assert rows == [{"itemNumber": "TF-10002", "buyerItemNumber": None, "quantity": "3", "uom": "CA", "unitPrice": "31.0"}]


def test_json_diff():
    a = {"x": 1, "l": [{"q": 1}], "gone": True}
    b = {"x": 2, "l": [{"q": 3}], "new": "y"}
    ops = {(o["op"], o["path"]) for o in json_diff(a, b)}
    assert ops == {("replace", "/x"), ("replace", "/l/0/q"), ("remove", "/gone"), ("add", "/new")}
