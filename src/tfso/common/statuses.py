"""Order and line status state machine (blueprint §5) and history event catalogue (§6)."""
from __future__ import annotations

from enum import Enum


class OrderStatus(str, Enum):
    RECEIVED = "RECEIVED"
    INGESTED = "INGESTED"
    EXCEPTION = "EXCEPTION"
    VALIDATED = "VALIDATED"
    ENRICHED = "ENRICHED"
    PUBLISHED = "PUBLISHED"
    SUBMITTED_TO_ERP = "SUBMITTED_TO_ERP"
    ERP_REJECTED = "ERP_REJECTED"
    CREDIT_HOLD = "CREDIT_HOLD"
    ACCEPTED = "ACCEPTED"
    ON_HOLD = "ON_HOLD"
    RELEASED = "RELEASED"
    PICKED = "PICKED"
    PACKED = "PACKED"
    PARTIALLY_SHIPPED = "PARTIALLY_SHIPPED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    INVOICED = "INVOICED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class StatusCategory(str, Enum):
    RECEIVED = "RECEIVED"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    CONFIRMED = "CONFIRMED"
    IN_FULFILLMENT = "IN_FULFILLMENT"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    INVOICED = "INVOICED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class LineStatus(str, Enum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    BACKORDERED = "BACKORDERED"
    ALLOCATED = "ALLOCATED"
    RELEASED = "RELEASED"
    PICKED = "PICKED"
    PACKED = "PACKED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    INVOICED = "INVOICED"
    CANCELLED = "CANCELLED"


S = OrderStatus
C = StatusCategory

STATUS_META: dict[OrderStatus, tuple[StatusCategory, int, str, str]] = {
    # status: (category, rank, tier, description)
    S.RECEIVED: (C.RECEIVED, 10, "TIER1", "Captured by a Tier-1 channel"),
    S.INGESTED: (C.RECEIVED, 20, "TIER2", "Parsed into the working format; order ID assigned"),
    S.EXCEPTION: (C.ACTION_REQUIRED, 25, "TIER2", "Failed a quality rule; held in the exception queue"),
    S.VALIDATED: (C.RECEIVED, 30, "TIER2", "All quality rules passed"),
    S.ENRICHED: (C.RECEIVED, 40, "TIER2", "Master data applied"),
    S.PUBLISHED: (C.RECEIVED, 50, "TIER2", "Canonical order published under the contract"),
    S.SUBMITTED_TO_ERP: (C.RECEIVED, 60, "TIER2", "Create command sent to D365"),
    S.ERP_REJECTED: (C.ACTION_REQUIRED, 65, "TIER3", "D365 refused the order"),
    S.CREDIT_HOLD: (C.ACTION_REQUIRED, 68, "TIER3", "D365 credit check placed the order on hold"),
    S.ACCEPTED: (C.CONFIRMED, 70, "TIER3", "Sales order created in D365 and inventory reserved"),
    S.ON_HOLD: (C.ACTION_REQUIRED, 72, "TIER2", "Manual hold"),
    S.RELEASED: (C.IN_FULFILLMENT, 80, "TIER3", "Released to the warehouse"),
    S.PICKED: (C.IN_FULFILLMENT, 85, "TIER3", "Picked in the warehouse"),
    S.PACKED: (C.IN_FULFILLMENT, 88, "TIER3", "Packed and staged for loading"),
    S.PARTIALLY_SHIPPED: (C.SHIPPED, 90, "TIER3", "Some lines or quantity shipped"),
    S.SHIPPED: (C.SHIPPED, 92, "TIER3", "Shipped; ship notice sent"),
    S.DELIVERED: (C.DELIVERED, 95, "TIER3", "Proof of delivery received from TMS"),
    S.INVOICED: (C.INVOICED, 98, "TIER3", "Invoice posted in D365"),
    S.CLOSED: (C.CLOSED, 100, "TIER3", "Order complete"),
    S.CANCELLED: (C.CANCELLED, 100, "TIER2", "Cancelled"),
    S.REJECTED: (C.CANCELLED, 100, "TIER2", "Rejected; cannot be corrected"),
}

TERMINAL = {S.CLOSED, S.CANCELLED, S.REJECTED}
CORRECTABLE = {S.EXCEPTION, S.ERP_REJECTED, S.ON_HOLD}
CANCELLABLE_BEFORE_RANK = 90  # cannot cancel once anything shipped

# Allowed transitions. Fulfillment statuses may be skipped forward (e.g. WMS reports SHIPPED without PICKED);
# that is handled by rank in `can_transition`, this table lists the explicit non-forward moves.
_EXPLICIT: dict[OrderStatus, set[OrderStatus]] = {
    S.RECEIVED: {S.INGESTED, S.EXCEPTION, S.REJECTED, S.CANCELLED},
    S.INGESTED: {S.VALIDATED, S.EXCEPTION, S.CANCELLED, S.REJECTED},
    S.EXCEPTION: {S.INGESTED, S.VALIDATED, S.CANCELLED, S.REJECTED},
    S.VALIDATED: {S.ENRICHED, S.EXCEPTION, S.CANCELLED},
    S.ENRICHED: {S.PUBLISHED, S.EXCEPTION, S.CANCELLED},
    S.PUBLISHED: {S.SUBMITTED_TO_ERP, S.CANCELLED, S.ON_HOLD},
    S.SUBMITTED_TO_ERP: {S.ACCEPTED, S.ERP_REJECTED, S.CREDIT_HOLD, S.CANCELLED},
    S.ERP_REJECTED: {S.INGESTED, S.VALIDATED, S.SUBMITTED_TO_ERP, S.CANCELLED, S.REJECTED},
    S.CREDIT_HOLD: {S.ACCEPTED, S.CANCELLED, S.ON_HOLD},
    S.ACCEPTED: {S.ON_HOLD, S.CREDIT_HOLD, S.CANCELLED},
    S.ON_HOLD: {S.PUBLISHED, S.ACCEPTED, S.RELEASED, S.CANCELLED, S.INGESTED, S.SUBMITTED_TO_ERP},
    S.RELEASED: {S.ON_HOLD, S.CANCELLED},
    S.PICKED: {S.CANCELLED},
    S.PACKED: {S.CANCELLED},
    S.PARTIALLY_SHIPPED: set(),
    S.SHIPPED: set(),
    S.DELIVERED: set(),
    S.INVOICED: set(),
}
_FULFILLMENT_FLOW = [S.ACCEPTED, S.RELEASED, S.PICKED, S.PACKED, S.PARTIALLY_SHIPPED, S.SHIPPED,
                     S.DELIVERED, S.INVOICED, S.CLOSED]


def category_of(status: OrderStatus | str) -> StatusCategory:
    return STATUS_META[OrderStatus(status)][0]


def rank_of(status: OrderStatus | str) -> int:
    return STATUS_META[OrderStatus(status)][1]


def can_transition(current: OrderStatus | str, target: OrderStatus | str) -> bool:
    current, target = OrderStatus(current), OrderStatus(target)
    if current == target:
        return target == S.PARTIALLY_SHIPPED  # repeated partial shipments are legitimate
    if current in TERMINAL:
        return False
    if target in _EXPLICIT.get(current, set()):
        return True
    # forward-only progression through fulfilment (statuses may be skipped, never reversed)
    if current in _FULFILLMENT_FLOW and target in _FULFILLMENT_FLOW:
        return _FULFILLMENT_FLOW.index(target) > _FULFILLMENT_FLOW.index(current)
    return False


def is_stale(current: OrderStatus | str, target: OrderStatus | str) -> bool:
    """A fulfilment event that arrives after the order already moved further along (out-of-order delivery)."""
    current, target = OrderStatus(current), OrderStatus(target)
    return (current in _FULFILLMENT_FLOW and target in _FULFILLMENT_FLOW
            and _FULFILLMENT_FLOW.index(target) <= _FULFILLMENT_FLOW.index(current)
            and target != S.PARTIALLY_SHIPPED)


# Customer-facing milestones rendered by the status view (in order).
MILESTONES: list[tuple[str, str, OrderStatus]] = [
    ("RECEIVED", "Order received", S.RECEIVED),
    ("VALIDATED", "Validated & enriched", S.VALIDATED),
    ("PUBLISHED", "Published to data product", S.PUBLISHED),
    ("ACCEPTED", "Confirmed in D365", S.ACCEPTED),
    ("RELEASED", "Released to warehouse", S.RELEASED),
    ("PICKED", "Picked & packed", S.PICKED),
    ("SHIPPED", "Shipped", S.SHIPPED),
    ("DELIVERED", "Delivered", S.DELIVERED),
    ("INVOICED", "Invoiced", S.INVOICED),
]


class HistoryEventType(str, Enum):
    ORDER_RECEIVED = "ORDER_RECEIVED"
    ORDER_INGESTED = "ORDER_INGESTED"
    VALIDATION_PASSED = "VALIDATION_PASSED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    ORDER_ENRICHED = "ORDER_ENRICHED"
    ORDER_PUBLISHED = "ORDER_PUBLISHED"
    ORDER_CORRECTED = "ORDER_CORRECTED"
    ORDER_RESUBMITTED = "ORDER_RESUBMITTED"
    SUBMITTED_TO_ERP = "SUBMITTED_TO_ERP"
    ERP_ACCEPTED = "ERP_ACCEPTED"
    ERP_REJECTED = "ERP_REJECTED"
    CREDIT_HOLD_PLACED = "CREDIT_HOLD_PLACED"
    HOLD_PLACED = "HOLD_PLACED"
    HOLD_RELEASED = "HOLD_RELEASED"
    INVENTORY_RESERVED = "INVENTORY_RESERVED"
    LINE_BACKORDERED = "LINE_BACKORDERED"
    RELEASED_TO_WAREHOUSE = "RELEASED_TO_WAREHOUSE"
    PICKED = "PICKED"
    PACKED = "PACKED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    INVOICED = "INVOICED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    DOCUMENT_SENT = "DOCUMENT_SENT"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    LATE_EVENT_IGNORED = "LATE_EVENT_IGNORED"
    ORDER_SPLIT = "ORDER_SPLIT"
    ORDER_LINKED = "ORDER_LINKED"


# Status events coming back from Tier-3 systems → target order status.
SYSTEM_EVENT_TO_STATUS: dict[str, OrderStatus] = {
    "ERP_ACCEPTED": S.ACCEPTED,
    "ERP_REJECTED": S.ERP_REJECTED,
    "CREDIT_HOLD_PLACED": S.CREDIT_HOLD,
    "CREDIT_HOLD_RELEASED": S.ACCEPTED,
    "RELEASED_TO_WAREHOUSE": S.RELEASED,
    "PICKED": S.PICKED,
    "PACKED": S.PACKED,
    "SHIPPED": S.SHIPPED,          # downgraded to PARTIALLY_SHIPPED when quantities remain open
    "DELIVERED": S.DELIVERED,
    "INVOICED": S.INVOICED,
    "CLOSED": S.CLOSED,
    "CANCELLED": S.CANCELLED,
}


def reference_catalogue() -> dict:
    """Machine-readable state machine for clients (GET /v1/reference/statuses)."""
    return {
        "statuses": [
            {"status": s.value, "category": m[0].value, "rank": m[1], "tier": m[2], "description": m[3],
             "terminal": s in TERMINAL, "correctable": s in CORRECTABLE,
             "allowedTransitions": sorted(t.value for t in OrderStatus if can_transition(s, t))}
            for s, m in STATUS_META.items()
        ],
        "categories": [c.value for c in StatusCategory],
        "lineStatuses": [ls.value for ls in LineStatus],
        "historyEventTypes": [e.value for e in HistoryEventType],
        "milestones": [{"milestone": m, "label": label, "status": st.value} for m, label, st in MILESTONES],
        "systemEvents": {k: v.value for k, v in SYSTEM_EVENT_TO_STATUS.items()},
    }
