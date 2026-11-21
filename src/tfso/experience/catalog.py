"""Data marketplace catalog: the governed datasets published by the Sales Order data product and the
master-data domains it depends on.

Each entry is the dataset's *contract card*: definition, owner/steward, classification, refresh cadence and
freshness SLA, schema (data definition), lineage, quality rules and the ways to consume it. Live facts
(record count, last refresh, freshness status, usage) are added at request time by `marketplace.py`.
"""
from __future__ import annotations

from typing import Any

F = dict  # field definition shorthand


def f(name: str, type_: str, description: str, *, key: bool = False, pii: bool = False, nullable: bool = True,
      example: Any = None, source: str | None = None) -> dict[str, Any]:
    out = {"name": name, "type": type_, "description": description, "primaryKey": key, "pii": pii,
           "nullable": nullable and not key}
    if example is not None:
        out["example"] = example
    if source:
        out["source"] = source
    return out


# --------------------------------------------------------------------------------------- lineage
# Nodes carry a `stage` (column in the diagram, left → right) and a `kind`.
CHANNELS = [
    {"id": "ch-edi", "label": "EDI X12 850", "kind": "source", "stage": 0, "detail": "AS2 / VAN trading partners"},
    {"id": "ch-email", "label": "E-mail orders", "kind": "source", "stage": 0, "detail": "PDF / Excel / text, Document Intelligence"},
    {"id": "ch-portal", "label": "Customer portal", "kind": "source", "stage": 0, "detail": "Customers + CS manual entry"},
    {"id": "ch-partner", "label": "Partner API", "kind": "source", "stage": 0, "detail": "System-to-system REST"},
]
EXP = {"id": "exp", "label": "Experience APIs", "kind": "integration", "stage": 1, "detail": "/exp - one API per channel"}
INTAKE = {"id": "intake", "label": "Order intake (Process API)", "kind": "integration", "stage": 2,
          "detail": "Validate DQ-001..011, enrich, canonicalize"}
MD = {"id": "md", "label": "Master data (System API)", "kind": "integration", "stage": 1,
      "detail": "Customers, ship-tos, items, agreements"}
D365 = {"id": "d365", "label": "D365 F&O", "kind": "system", "stage": 0, "detail": "System of record (OData data entities)"}
STORE = {"id": "store", "label": "Sales Order data product", "kind": "store", "stage": 3,
         "detail": "Canonical order store (PostgreSQL JSONB) + order_history"}
FULFIL = {"id": "fulfil", "label": "Fulfilment events", "kind": "integration", "stage": 2,
          "detail": "D365 business events, WMS ship confirms, TMS POD"}
WMS_TMS = {"id": "wmstms", "label": "WMS / TMS", "kind": "system", "stage": 1, "detail": "Warehouse & transport"}
BUS = {"id": "bus", "label": "sales-order-events", "kind": "stream", "stage": 5, "detail": "Service Bus / Event Hubs (CloudEvents)"}
BRONZE = {"id": "bronze", "label": "Lakehouse bronze", "kind": "consumer", "stage": 6,
          "detail": "ADLS Gen2 raw events (sales-order-events)"}
SILVER = {"id": "silver", "label": "Lakehouse silver", "kind": "consumer", "stage": 6,
          "detail": "Delta Live Tables: sales_orders, order_history, shipments"}
GOLD = {"id": "gold", "label": "Lakehouse gold marts", "kind": "consumer", "stage": 6,
        "detail": "Status daily, cycle time, touchless, ROI (Power BI / Delta Sharing)"}
CRM = {"id": "crm", "label": "CRM timeline", "kind": "consumer", "stage": 6, "detail": "Account team view"}


def _order_lineage(dataset_label: str, extra_upstream: list[dict] | None = None) -> dict[str, Any]:
    ds = {"id": "dataset", "label": dataset_label, "kind": "dataset", "stage": 4, "detail": "This dataset"}
    nodes = CHANNELS + [EXP, MD, INTAKE, STORE, ds, BUS, BRONZE, SILVER, GOLD, CRM] + (extra_upstream or [])
    edges = [{"from": c["id"], "to": "exp"} for c in CHANNELS] + [
        {"from": "exp", "to": "intake", "label": "intake"},
        {"from": "md", "to": "intake", "label": "enrich"},
        {"from": "intake", "to": "store", "label": "publish"},
        {"from": "store", "to": "dataset", "label": "serve"},
        {"from": "store", "to": "bus", "label": "outbox"},
        {"from": "bus", "to": "bronze"}, {"from": "bus", "to": "silver", "label": "DLT"}, {"from": "bus", "to": "gold", "label": "marts"},
        {"from": "bus", "to": "crm"},
    ]
    return {"nodes": nodes, "edges": edges}


def _fulfilment_lineage(dataset_label: str) -> dict[str, Any]:
    g = _order_lineage(dataset_label)
    d365 = {**D365, "stage": 1}
    g["nodes"] += [d365, WMS_TMS, FULFIL]
    g["edges"] += [{"from": "d365", "to": "fulfil"}, {"from": "wmstms", "to": "fulfil"},
                   {"from": "fulfil", "to": "store", "label": "status events"}]
    return g


def _master_lineage(dataset_label: str, entity: str) -> dict[str, Any]:
    ds = {"id": "dataset", "label": dataset_label, "kind": "dataset", "stage": 2, "detail": "This dataset"}
    nodes = [{**D365, "detail": f"{entity} (OData data entity)"},
             {"id": "mdm", "label": "Master data (System API)", "kind": "integration", "stage": 1,
              "detail": "Cached 15 min, seed set in non-prod"},
             ds,
             {"id": "intake", "label": "Order intake validation & enrichment", "kind": "consumer", "stage": 3,
              "detail": "DQ-001..008, price agreement, ship-to"},
             {"id": "portal", "label": "Customer portal pickers", "kind": "consumer", "stage": 3, "detail": "Experience API catalog"}]
    edges = [{"from": "d365", "to": "mdm", "label": "OData"}, {"from": "mdm", "to": "dataset", "label": "serve"},
             {"from": "dataset", "to": "intake"}, {"from": "dataset", "to": "portal"}]
    return {"nodes": nodes, "edges": edges}


OWNER = {"name": "Sales Order data product owner", "team": "Sales & Customer Service", "email": "so-data-product@taylorfarms.example"}
STEWARD = {"name": "Customer Service data steward", "team": "Customer Service Operations", "email": "cs-data-steward@taylorfarms.example"}
MD_OWNER = {"name": "Master data owner", "team": "Enterprise Data Management", "email": "mdm@taylorfarms.example"}
DQ_ORDER = ["DQ-001", "DQ-002", "DQ-003", "DQ-004", "DQ-005", "DQ-006", "DQ-007", "DQ-008", "DQ-009", "DQ-010", "DQ-011"]

REGION_IDS = ["WEST", "SOUTHWEST", "CENTRAL", "SOUTHEAST", "NORTHEAST"]
SCOPE_FILTERS = [
    {"name": "region", "label": "Region", "type": "enum", "values": REGION_IDS, "help": "Fulfilling region (blank = national)"},
    {"name": "accountScope", "label": "Account scope", "type": "enum", "values": ["NATIONAL", "REGIONAL"]},
]

ORDER_FILTERS = SCOPE_FILTERS + [
    {"name": "status", "label": "Status", "type": "string", "help": "Comma separated, e.g. ACCEPTED,SHIPPED"},
    {"name": "statusCategory", "label": "Status category", "type": "string"},
    {"name": "channel", "label": "Channel", "type": "enum", "values": ["EDI", "EMAIL", "PORTAL", "MANUAL", "PARTNER_API"]},
    {"name": "customerId", "label": "Customer id", "type": "string"},
    {"name": "fromDate", "label": "Received from", "type": "date"},
    {"name": "toDate", "label": "Received to", "type": "date"},
]

DATASETS: list[dict[str, Any]] = [
    {
        "id": "sales-orders", "name": "Sales Orders", "object": "Sales Order (SO)", "domain": "Sales",
        "type": "Data product - output port", "version": "1.0.0", "classification": "Internal",
        "accessPolicy": "auto", "tags": ["order-to-cash", "canonical", "status", "certified"],
        "certified": True,
        "summary": "One row per sales order from every channel, with current status, customer, PO, D365 reference and totals.",
        "description": ("The canonical record of every sales order received by Taylor Farms through EDI, e-mail, the "
                        "customer portal, manual entry and partner APIs. Each order has passed the data product's "
                        "quality rules and been enriched with master data before publication. Status is kept current "
                        "from D365, WMS and TMS events. Full detail (parties, lines, shipments, invoices, ROI tags, "
                        "history) is available through the canonical order API."),
        "owner": OWNER, "steward": STEWARD,
        "refresh": {"mode": "Event-driven (real time)", "frequency": "Continuous", "freshnessSlaSeconds": 60,
                    "detail": "Updated in the same transaction as every order state change; published via the outbox."},
        "retention": "7 years (order-to-cash audit)", "grain": "One row per order",
        "primaryKey": ["orderId"], "statsKey": "orders",
        "qualityRules": DQ_ORDER,
        "filters": ORDER_FILTERS,
        "schema": [
            f("orderId", "string", "Data product order id SO-YYYYMMDD-XXXXXXXX", key=True, example="SO-20261113-63B9D3E7"),
            f("orderGroupId", "string", "Groups orders that arrived together (one interchange / e-mail) or were split"),
            f("status", "enum", "Current order status (state machine: RECEIVED … INVOICED, CLOSED, CANCELLED)", nullable=False),
            f("statusCategory", "enum", "Customer-facing category: RECEIVED, ACTION_REQUIRED, CONFIRMED, IN_FULFILLMENT, SHIPPED, DELIVERED, INVOICED, CLOSED, CANCELLED"),
            f("sourceChannel", "enum", "EDI, EMAIL, PORTAL, MANUAL, PARTNER_API", nullable=False),
            f("customerId", "string", "Sold-to customer id", source="Customer master"),
            f("customerName", "string", "Sold-to customer name", source="Customer master"),
            f("customerPo", "string", "Customer purchase order number (X12 BEG03)"),
            f("shipToId", "string", "Ship-to location id", source="Customer master"),
            f("erpSalesOrderId", "string", "D365 SalesOrderNumber once created", source="D365"),
            f("requestedDeliveryDate", "date", "Requested delivery date (DTM*002)"),
            f("receivedAt", "timestamp", "When the order was captured by the channel", nullable=False),
            f("updatedAt", "timestamp", "Last change to the order"),
            f("totalAmount", "decimal(14,2)", "Order grand total"),
            f("currency", "string", "ISO 4217 currency"),
            f("openExceptionCount", "integer", "Open data-quality / ERP exceptions"),
            f("touchless", "boolean", "True when no manual touch was needed (ROI tag)"),
            f("region", "enum", "Fulfilling operating region (WEST, SOUTHWEST, CENTRAL, SOUTHEAST, NORTHEAST)"),
            f("shipFromWarehouse", "string", "Processing site / D365 warehouse that ships the order"),
            f("accountScope", "enum", "NATIONAL (served from several regions) or REGIONAL account"),
            f("crossRegion", "boolean", "Sourced outside the ship-to's home region"),
            f("version", "integer", "Optimistic concurrency version"),
        ],
        "lineage": _fulfilment_lineage("Sales Orders"),
        "consumption": {
            "rest": [{"method": "GET", "path": "/process/v1/orders", "description": "Search / page orders"},
                     {"method": "GET", "path": "/process/v1/orders/{orderId}", "description": "Canonical order (CDM v1)"},
                     {"method": "GET", "path": "/process/v1/orders/{orderId}/status", "description": "Status view"}],
            "events": {"topic": "sales-order-events", "type": "tfso.salesorder.OrderStatusChanged", "eventHub": "order-status-stream"},
            "lakehouse": {"table": "sales_order.silver.sales_orders", "sharing": "Delta Sharing share `sales_order_share`"},
            "schemaRef": "cdm/sales-order.v1.schema.json",
        },
        "consumers": ["D365 F&O", "CRM", "Lakehouse (Databricks)", "Customer portal", "Power BI order-to-cash"],
    },
    {
        "id": "sales-order-lines", "name": "Sales Order Lines", "object": "Sales Order Line", "domain": "Sales",
        "type": "Data product - output port", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["order-to-cash", "demand", "fill-rate", "certified"], "certified": True,
        "summary": "One row per order line with ordered, confirmed, backordered, shipped and invoiced quantities and pricing.",
        "description": ("Line-level view of the canonical order: item (Taylor Farms number plus the customer's buyer item and "
                        "GTIN), quantities across the lifecycle, unit price with its source (customer agreement, list, PO) "
                        "and line status. Used for demand planning, fill-rate and pricing-compliance analytics."),
        "owner": OWNER, "steward": STEWARD,
        "refresh": {"mode": "Event-driven (real time)", "frequency": "Continuous", "freshnessSlaSeconds": 60,
                    "detail": "Derived from the canonical order on read; reflects every confirmation, shipment and invoice."},
        "retention": "7 years", "grain": "One row per order line", "primaryKey": ["orderId", "lineNumber"],
        "statsKey": "orders", "qualityRules": ["DQ-003", "DQ-004", "DQ-005", "DQ-008", "DQ-010"], "filters": ORDER_FILTERS,
        "schema": [
            f("orderId", "string", "Order id", key=True), f("lineNumber", "integer", "Line number", key=True),
            f("customerId", "string", "Sold-to customer"), f("customerPo", "string", "Customer PO"),
            f("orderStatus", "enum", "Order status"),
            f("itemNumber", "string", "Taylor Farms item number", source="Item master"),
            f("buyerItemNumber", "string", "Customer's item number (PO1 IN)"),
            f("gtin", "string", "GS1 GTIN-14 case code", source="Item master"),
            f("description", "string", "Item description", source="Item master"),
            f("uom", "string", "Unit of measure (CA, EA …)"),
            f("ordered", "decimal", "Ordered quantity"), f("confirmed", "decimal", "Confirmed by D365"),
            f("backordered", "decimal", "Backordered"), f("shipped", "decimal", "Shipped"),
            f("delivered", "decimal", "Delivered"), f("invoiced", "decimal", "Invoiced"),
            f("unitPrice", "decimal", "Unit price applied"),
            f("priceSource", "enum", "CUSTOMER_AGREEMENT, LIST, PURCHASE_ORDER, MANUAL_OVERRIDE"),
            f("agreementId", "string", "Trade agreement applied", source="D365 pricing"),
            f("lineAmount", "decimal", "Quantity × unit price"), f("lineStatus", "enum", "Line status"),
            f("requestedDeliveryDate", "date", "Requested delivery date"),
            f("region", "enum", "Fulfilling region"), f("shipFromWarehouse", "string", "Ship-from warehouse"),
        ],
        "lineage": _fulfilment_lineage("Sales Order Lines"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/orders?full=true", "description": "Orders with canonical documents"}],
                        "lakehouse": {"table": "sales_order.silver.sales_order_lines"}, "schemaRef": "cdm/sales-order.v1.schema.json#/$defs/OrderLine"},
        "consumers": ["Demand planning", "Lakehouse (Databricks)"],
    },
    {
        "id": "order-lifecycle-events", "name": "Order Lifecycle History", "object": "Order History Event", "domain": "Sales",
        "type": "Data product - output port", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["history", "audit", "events", "cycle-time", "certified"], "certified": True,
        "summary": "Every lifecycle event for every order: who/what/when, from→to status, tier and source system.",
        "description": ("The append-only order history (event store). Each entry records the event type, status "
                        "transition, tier (capture / integrate / fulfil), source system (EDI gateway, Process API, D365, "
                        "WMS, TMS …), actor, timing and the diff of what changed. Use it for cycle-time, SLA, "
                        "chargeback and audit analysis across one order or groups of orders."),
        "owner": OWNER, "steward": STEWARD,
        "refresh": {"mode": "Event-driven (real time)", "frequency": "Continuous", "freshnessSlaSeconds": 60,
                    "detail": "Appended in the same transaction as the order change; also streamed as CloudEvents."},
        "retention": "7 years (immutable)", "grain": "One row per lifecycle event", "primaryKey": ["eventId"],
        "statsKey": "historyEvents", "qualityRules": [],
        "filters": SCOPE_FILTERS + [{"name": "eventType", "label": "Event type", "type": "string", "help": "e.g. SHIPPED,INVOICED"},
                    {"name": "sourceSystem", "label": "Source system", "type": "enum", "values": ["PROCESS-API", "D365", "WMS", "TMS", "EDI-GATEWAY", "CUSTOMER-PORTAL", "NOTIFICATION"]},
                    {"name": "orderId", "label": "Order id", "type": "string"},
                    {"name": "since", "label": "Recorded since", "type": "datetime"}],
        "schema": [
            f("eventId", "string", "Unique event id", key=True), f("orderId", "string", "Order id"),
            f("orderGroupId", "string", "Order group"), f("sequence", "integer", "Monotonic sequence within the order"),
            f("eventType", "enum", "ORDER_RECEIVED, VALIDATION_PASSED, ERP_ACCEPTED, SHIPPED, INVOICED …"),
            f("fromStatus", "enum", "Status before"), f("toStatus", "enum", "Status after"),
            f("statusCategory", "enum", "Category after the event"),
            f("tier", "enum", "TIER1 capture, TIER2 integrate, TIER3 fulfil"),
            f("sourceSystem", "string", "System that produced the event"),
            f("actorType", "enum", "SYSTEM, USER, CUSTOMER, PARTNER"), f("actorId", "string", "User / service / partner id", pii=True),
            f("occurredAt", "timestamp", "When it happened in the source system"),
            f("recordedAt", "timestamp", "When the data product recorded it"),
            f("elapsedSincePreviousSeconds", "decimal", "Seconds since the previous event of the order"),
            f("reasonCode", "string", "Reason code (exceptions, holds, cancellations)"),
            f("message", "string", "Human-readable description"),
            f("changeCount", "integer", "Number of JSON-Patch changes recorded"),
        ],
        "lineage": _fulfilment_lineage("Order Lifecycle History"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/order-events", "description": "Cross-order event feed (paged)"},
                                 {"method": "GET", "path": "/process/v1/orders/{orderId}/history", "description": "One order"},
                                 {"method": "GET", "path": "/process/v1/order-groups/{groupId}/history", "description": "Group of orders"}],
                        "events": {"topic": "sales-order-events", "type": "tfso.salesorder.OrderStatusChanged"},
                        "lakehouse": {"table": "sales_order.silver.order_history"},
                        "schemaRef": "cdm/order-history-entry.v1.schema.json"},
        "consumers": ["Lakehouse (Databricks)", "Finance order-to-cash", "Audit"],
    },
    {
        "id": "order-exceptions", "name": "Order Exceptions", "object": "Order Exception", "domain": "Sales",
        "type": "Operational dataset", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["data-quality", "exceptions", "customer-service"],
        "summary": "Open exceptions blocking orders: rule, severity, message, line and age.",
        "description": ("The exception queue: orders held because a quality rule failed (DQ-001…DQ-011), D365 rejected "
                        "them, or a credit hold was placed. Customer service works this queue; analytics use it to "
                        "find the most common error reasons by channel and customer."),
        "owner": OWNER, "steward": STEWARD,
        "refresh": {"mode": "Event-driven (real time)", "frequency": "Continuous", "freshnessSlaSeconds": 60, "detail": "Changes as orders fail validation or are corrected."},
        "retention": "Current state (history in Order Lifecycle History)", "grain": "One row per open exception",
        "primaryKey": ["exceptionId"], "statsKey": "openExceptionOrders", "qualityRules": DQ_ORDER, "filters": SCOPE_FILTERS + [{"name": "customerId", "label": "Customer id", "type": "string"},
                                                                                                                {"name": "code", "label": "Exception code", "type": "string"}],
        "schema": [
            f("exceptionId", "string", "Exception id", key=True), f("orderId", "string", "Order id"),
            f("customerId", "string", "Customer"), f("customerName", "string", "Customer name"),
            f("customerPo", "string", "Customer PO"), f("sourceChannel", "enum", "Channel"),
            f("code", "string", "Reason code, e.g. PRICE_MISMATCH"), f("ruleId", "string", "DQ rule id"),
            f("severity", "enum", "ERROR / WARNING"), f("message", "string", "What failed"),
            f("lineNumber", "integer", "Affected line"), f("field", "string", "Affected element"),
            f("detectedAt", "timestamp", "When detected"), f("ageMinutes", "decimal", "Minutes since the order was received"),
            f("region", "enum", "Fulfilling region"),
        ],
        "lineage": _fulfilment_lineage("Order Exceptions"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/exceptions", "description": "Exception queue"}]},
        "consumers": ["Customer service", "Data quality reporting"],
    },
    {
        "id": "shipments", "name": "Shipments & Traceability", "object": "Shipment", "domain": "Fulfilment",
        "type": "Data product - output port", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["fulfilment", "asn", "fsma-204", "traceability"],
        "summary": "Shipments with carrier, tracking, BOL, ship/delivery times and FSMA 204 lot / traceability lot codes.",
        "description": ("Shipments confirmed by the warehouse and delivered by transport, attached to their orders. "
                        "Each row carries carrier (SCAC), tracking, bill of lading, ship and delivery timestamps, "
                        "proof of delivery, and the lots shipped with their Traceability Lot Codes for FSMA 204."),
        "owner": OWNER, "steward": {"name": "Logistics data steward", "team": "Distribution & Transportation", "email": "logistics-data@taylorfarms.example"},
        "refresh": {"mode": "Event-driven (real time)", "frequency": "Continuous", "freshnessSlaSeconds": 120, "detail": "From WMS ship confirmations and TMS proof of delivery."},
        "retention": "7 years (FSMA 204 requires 2)", "grain": "One row per shipment line lot", "primaryKey": ["shipmentId", "lineNumber", "lotNumber"],
        "statsKey": "orders", "qualityRules": [], "filters": ORDER_FILTERS,
        "schema": [
            f("shipmentId", "string", "Shipment id", key=True), f("lineNumber", "integer", "Order line", key=True),
            f("lotNumber", "string", "Lot shipped", key=True), f("orderId", "string", "Order id"),
            f("customerId", "string", "Customer"), f("customerPo", "string", "Customer PO"),
            f("asnNumber", "string", "856 BSN02"), f("carrierScac", "string", "Carrier SCAC"), f("carrierName", "string", "Carrier"),
            f("trackingNumber", "string", "Tracking / PRO"), f("billOfLading", "string", "BOL"),
            f("shippedAt", "timestamp", "Ship confirmation time"), f("deliveredAt", "timestamp", "Proof-of-delivery time"),
            f("shipmentStatus", "enum", "IN_TRANSIT, DELIVERED, EXCEPTION"), f("itemNumber", "string", "Item"),
            f("quantity", "decimal", "Quantity of this lot"), f("uom", "string", "UOM"),
            f("traceabilityLotCode", "string", "FSMA 204 Traceability Lot Code"), f("packDate", "date", "Pack date"),
            f("bestByDate", "date", "Best-by date"), f("countryOfOrigin", "string", "Country of origin"),
            f("region", "enum", "Fulfilling region"), f("shipFromWarehouse", "string", "Ship-from warehouse"),
        ],
        "lineage": _fulfilment_lineage("Shipments & Traceability"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/orders/{orderId}/status", "description": "Shipments per order"}],
                        "edi": "X12 856 ship notice to EDI partners", "lakehouse": {"table": "sales_order.silver.shipments"}},
        "consumers": ["Food safety / FSMA 204", "Customer service", "Transportation analytics"],
    },
    {
        "id": "invoices", "name": "Invoices", "object": "Invoice", "domain": "Finance",
        "type": "Data product - output port", "version": "1.0.0", "classification": "Confidential", "accessPolicy": "approval",
        "tags": ["order-to-cash", "finance", "ar"],
        "summary": "Invoices posted in D365 against each order, with amounts and due dates.",
        "description": ("Invoices posted in D365 for shipped quantities, linked back to the order and customer PO. "
                        "Confidential: requires an approved access request (commercial terms)."),
        "owner": OWNER, "steward": {"name": "Finance data steward", "team": "Accounts Receivable", "email": "ar-data@taylorfarms.example"},
        "refresh": {"mode": "Event-driven (real time)", "frequency": "Continuous", "freshnessSlaSeconds": 300, "detail": "D365 invoice-posted business events."},
        "retention": "7 years", "grain": "One row per invoice line", "primaryKey": ["invoiceId", "lineNumber"],
        "statsKey": "orders", "qualityRules": [], "filters": ORDER_FILTERS, "sensitiveFields": ["amount", "unitPrice", "lineAmount"],
        "schema": [
            f("invoiceId", "string", "D365 invoice id", key=True), f("lineNumber", "integer", "Order line", key=True),
            f("orderId", "string", "Order id"), f("erpSalesOrderId", "string", "D365 sales order"),
            f("customerId", "string", "Customer"), f("customerPo", "string", "Customer PO"),
            f("invoiceDate", "date", "Invoice date"), f("dueDate", "date", "Due date"),
            f("quantity", "decimal", "Invoiced quantity"), f("unitPrice", "decimal", "Unit price"),
            f("lineAmount", "decimal", "Line amount"), f("amount", "decimal", "Invoice total"),
            f("currency", "string", "Currency"), f("invoiceStatus", "enum", "POSTED, SENT, PAID, CREDITED"),
            f("region", "enum", "Fulfilling region"),
        ],
        "lineage": _fulfilment_lineage("Invoices"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/orders/{orderId}", "description": "invoices[] on the canonical order"}],
                        "edi": "X12 810 invoice to EDI partners", "lakehouse": {"table": "sales_order.silver.invoices"}},
        "consumers": ["Finance", "Lakehouse (Databricks)"],
    },
    {
        "id": "order-kpis", "name": "Order KPIs by Channel", "object": "KPI", "domain": "Analytics",
        "type": "Metric", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["kpi", "touchless", "cycle-time", "roi"],
        "summary": "Touchless rate, first-pass validation, receipt-to-D365 cycle time and open exceptions per channel.",
        "description": ("The data product's measures (deck slide 13) computed from the ROI tags on each order: touchless "
                        "order rate, first-pass validation rate, receipt-to-D365 and receipt-to-ship cycle times, "
                        "open exceptions - by source channel."),
        "owner": OWNER, "steward": STEWARD,
        "refresh": {"mode": "Computed on request", "frequency": "On demand (gold mart refreshed hourly)", "freshnessSlaSeconds": 3600,
                    "detail": "API computes live; Databricks gold mart gold_touchless_rate refreshes hourly."},
        "retention": "n/a (derived)", "grain": "One row per channel", "primaryKey": ["channel"], "statsKey": "orders",
        "qualityRules": [], "filters": [],
        "schema": [
            f("channel", "enum", "Source channel", key=True), f("orders", "integer", "Orders received"),
            f("touchlessRate", "decimal", "Share of orders reaching D365 with no manual touch (0-1)"),
            f("firstPassValidationRate", "decimal", "Share passing all rules first time (0-1)"),
            f("avgReceiptToErpMinutes", "decimal", "Average minutes from receipt to D365 creation"),
            f("avgReceiptToShipHours", "decimal", "Average hours from receipt to ship"),
            f("openExceptions", "integer", "Orders with open exceptions"),
        ],
        "lineage": _order_lineage("Order KPIs by Channel"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/metrics/kpis", "description": "KPIs and ROI"}],
                        "lakehouse": {"table": "sales_order.gold.gold_touchless_rate"}},
        "consumers": ["Product owner", "Leadership dashboard"],
    },
    {
        "id": "customers", "name": "Customers", "object": "Customer", "domain": "Customer",
        "type": "Master data", "version": "1.0.0", "classification": "Confidential", "accessPolicy": "approval",
        "tags": ["master-data", "customer", "pii"],
        "summary": "Customer accounts: D365 account, segment, channels, payment terms, credit limit and contacts.",
        "description": ("Customer master used to validate (DQ-001) and enrich every order. Contains contact names, "
                        "e-mails and phone numbers (PII) and credit information - requires an approved access request; "
                        "previews mask personal data."),
        "owner": MD_OWNER, "steward": {"name": "Customer master steward", "team": "Enterprise Data Management", "email": "customer-mdm@taylorfarms.example"},
        "refresh": {"mode": "Scheduled cache", "frequency": "Every 15 minutes from D365", "freshnessSlaSeconds": 900,
                    "detail": "System API caches D365 CustomersV3 for 15 minutes."},
        "retention": "Life of the relationship + 7 years", "grain": "One row per customer account", "primaryKey": ["customerId"],
        "statsKey": None, "qualityRules": ["DQ-001"], "filters": [{"name": "active", "label": "Active only", "type": "boolean"}],
        "sensitiveFields": ["contactName", "contactEmail", "contactPhone", "creditLimit", "openAr"],
        "schema": [
            f("customerId", "string", "Customer id", key=True), f("name", "string", "Legal / trading name"),
            f("active", "boolean", "Active for sales"), f("erpAccount", "string", "D365 customer account"),
            f("invoiceAccount", "string", "D365 invoice account"), f("segment", "enum", "RETAIL, FOODSERVICE, QSR, MEAL_KIT"),
            f("channels", "array<string>", "Order channels used"), f("currency", "string", "Currency"),
            f("paymentTermsCode", "string", "Payment terms"), f("priceGroup", "string", "Customer price group"),
            f("creditLimit", "decimal", "Credit limit"), f("openAr", "decimal", "Open receivables"),
            f("gln", "string", "GS1 GLN"), f("duns", "string", "DUNS"),
            f("contactName", "string", "Primary contact name", pii=True), f("contactEmail", "string", "Primary contact e-mail", pii=True),
            f("contactPhone", "string", "Primary contact phone", pii=True),
        ],
        "lineage": _master_lineage("Customers", "CustomersV3"),
        "consumption": {"rest": [{"method": "GET", "path": "/system/v1/masterdata/customers", "description": "Customer master"}]},
        "consumers": ["Order intake", "Customer portal", "CRM"],
    },
    {
        "id": "ship-tos", "name": "Ship-to Locations", "object": "Ship-to", "domain": "Customer",
        "type": "Master data", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["master-data", "customer", "logistics"],
        "summary": "Delivery locations with EDI location codes, GLNs, transit days, receiving days and shipping site.",
        "description": "Customer delivery addresses used to resolve EDI N1*ST codes, validate DQ-002/DQ-007 and plan shipping.",
        "owner": MD_OWNER, "steward": {"name": "Customer master steward", "team": "Enterprise Data Management", "email": "customer-mdm@taylorfarms.example"},
        "refresh": {"mode": "Scheduled cache", "frequency": "Every 15 minutes from D365", "freshnessSlaSeconds": 900, "detail": "D365 delivery addresses."},
        "retention": "Life of the location", "grain": "One row per ship-to", "primaryKey": ["shipToId"], "statsKey": None,
        "qualityRules": ["DQ-002", "DQ-007"], "filters": [{"name": "customerId", "label": "Customer id", "type": "string"}],
        "schema": [
            f("shipToId", "string", "Ship-to id", key=True), f("customerId", "string", "Owning customer"),
            f("name", "string", "Location name"), f("active", "boolean", "Active"),
            f("erpLocationId", "string", "D365 delivery address location id"), f("ediLocationCode", "string", "Buyer location code (N1*ST*92)"),
            f("gln", "string", "GS1 GLN"), f("line1", "string", "Street"), f("city", "string", "City"),
            f("stateProvince", "string", "State"), f("postalCode", "string", "Postal code"),
            f("transitDays", "integer", "Transit days from shipping site"), f("deliveryDays", "array<string>", "Receiving days"),
            f("receivingHours", "string", "Receiving window"), f("site", "string", "Shipping site"), f("warehouse", "string", "Shipping warehouse"),
            f("temperatureZone", "enum", "Receiving temperature zone"),
        ],
        "lineage": _master_lineage("Ship-to Locations", "CustomerPostalAddresses"),
        "consumption": {"rest": [{"method": "GET", "path": "/system/v1/masterdata/ship-tos", "description": "Ship-to master"}]},
        "consumers": ["Order intake", "EDI translation", "Transportation planning"],
    },
    {
        "id": "items", "name": "Items", "object": "Item", "domain": "Product",
        "type": "Master data", "version": "1.0.0", "classification": "Public", "accessPolicy": "auto",
        "tags": ["master-data", "product", "gs1"],
        "summary": "Sellable items with GTIN, pack size, UOMs, list price, lead time, shelf life and temperature zone.",
        "description": "Product master used to validate items and units of measure (DQ-003/004/008) and enrich order lines.",
        "owner": MD_OWNER, "steward": {"name": "Product master steward", "team": "Product Data", "email": "product-mdm@taylorfarms.example"},
        "refresh": {"mode": "Scheduled cache", "frequency": "Every 15 minutes from D365", "freshnessSlaSeconds": 900, "detail": "D365 ReleasedProductsV2."},
        "retention": "Life of the item + 7 years", "grain": "One row per item", "primaryKey": ["itemNumber"], "statsKey": None,
        "qualityRules": ["DQ-003", "DQ-004", "DQ-008"], "filters": [{"name": "active", "label": "Active only", "type": "boolean"}],
        "schema": [
            f("itemNumber", "string", "Taylor Farms item number", key=True), f("description", "string", "Description"),
            f("brand", "string", "Brand"), f("packSize", "string", "Pack size, e.g. 6x2 LB"), f("gtin", "string", "GTIN-14"),
            f("upc", "string", "UPC"), f("inventoryUom", "string", "Inventory UOM"), f("salesUoms", "array<string>", "Sellable UOMs"),
            f("listPrice", "decimal", "List price per case"), f("leadTimeDays", "integer", "Lead time"),
            f("orderIncrement", "integer", "Order multiple"), f("minOrderQty", "integer", "Minimum order"),
            f("shelfLifeDays", "integer", "Shelf life"), f("temperatureZone", "enum", "FROZEN, REFRIGERATED, AMBIENT"),
            f("commodityCode", "string", "Commodity / FSMA FTL category"), f("ftlCovered", "boolean", "On the FDA Food Traceability List"),
            f("caseWeightLb", "decimal", "Case weight"), f("active", "boolean", "Sellable"),
        ],
        "lineage": _master_lineage("Items", "ReleasedProductsV2"),
        "consumption": {"rest": [{"method": "GET", "path": "/system/v1/masterdata/items", "description": "Item master"}]},
        "consumers": ["Order intake", "Customer portal", "Planning"],
    },
    {
        "id": "price-agreements", "name": "Customer Price Agreements", "object": "Trade Agreement", "domain": "Pricing",
        "type": "Master data", "version": "1.0.0", "classification": "Confidential", "accessPolicy": "approval",
        "tags": ["master-data", "pricing", "commercial"],
        "summary": "Contract prices per customer and item with validity dates (D365 trade agreements).",
        "description": "Customer-specific prices used for DQ-005 price validation and to price lines that arrive without a price. Commercially sensitive.",
        "owner": MD_OWNER, "steward": {"name": "Pricing steward", "team": "Revenue Management", "email": "pricing@taylorfarms.example"},
        "refresh": {"mode": "Scheduled cache", "frequency": "Every 15 minutes from D365", "freshnessSlaSeconds": 900, "detail": "D365 SalesPriceAgreements."},
        "retention": "Agreement validity + 7 years", "grain": "One row per customer × item × validity", "primaryKey": ["agreementId", "customerId", "itemNumber"],
        "statsKey": None, "qualityRules": ["DQ-005"], "filters": [{"name": "customerId", "label": "Customer id", "type": "string"}],
        "sensitiveFields": ["price"],
        "schema": [
            f("agreementId", "string", "Trade agreement journal", key=True), f("customerId", "string", "Customer", key=True),
            f("itemNumber", "string", "Item", key=True), f("price", "decimal", "Contract price"), f("uom", "string", "Price UOM"),
            f("validFrom", "date", "Valid from"), f("validTo", "date", "Valid to"),
        ],
        "lineage": _master_lineage("Customer Price Agreements", "SalesPriceAgreements"),
        "consumption": {"rest": [{"method": "GET", "path": "/system/v1/masterdata/price-agreements", "description": "Agreements"}]},
        "consumers": ["Order intake (DQ-005)", "Revenue management"],
    },
    {
        "id": "trading-partners", "name": "EDI Trading Partners", "object": "Trading Partner", "domain": "Integration",
        "type": "Reference data", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["edi", "x12", "reference"],
        "summary": "EDI partner agreements: ISA id, customer, X12 version, which acknowledgements are sent and their SLAs.",
        "description": "Trading partner configuration used by the EDI experience API to map interchanges to customers and decide 997/855/856/810.",
        "owner": {"name": "Integration platform owner", "team": "IT Integration", "email": "integration@taylorfarms.example"},
        "steward": {"name": "EDI coordinator", "team": "IT Integration", "email": "edi@taylorfarms.example"},
        "refresh": {"mode": "On change", "frequency": "When an agreement changes", "freshnessSlaSeconds": None, "detail": "Integration Account agreements."},
        "retention": "Life of the agreement", "grain": "One row per partner", "primaryKey": ["isaId"], "statsKey": None,
        "qualityRules": [], "filters": [],
        "schema": [
            f("isaId", "string", "ISA06 sender id", key=True), f("isaQualifier", "string", "ISA05 qualifier"),
            f("customerId", "string", "Customer"), f("name", "string", "Partner name"), f("x12Version", "string", "X12 version"),
            f("send997", "boolean", "Sends 997"), f("send855", "boolean", "Sends 855"), f("send856", "boolean", "Sends 856"),
            f("send810", "boolean", "Sends 810"), f("ackSla855Minutes", "integer", "855 SLA"), f("ackSla856Minutes", "integer", "856 SLA"),
        ],
        "lineage": _master_lineage("EDI Trading Partners", "Integration Account agreements"),
        "consumption": {"rest": [{"method": "GET", "path": "/system/v1/masterdata/trading-partners", "description": "Partners"}]},
        "consumers": ["EDI experience API", "Chargeback analysis"],
    },
]

OPS_OWNER = {"name": "Network operations owner", "team": "Supply Chain & Regional Operations", "email": "network-ops@taylorfarms.example"}

DATASETS += [
    {
        "id": "regional-performance", "name": "Regional & Site Performance", "object": "Regional KPI", "domain": "Operations",
        "type": "Metric", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["regional", "national", "otif", "fill-rate", "kpi", "certified"], "certified": True,
        "summary": "OTIF, fill rate, revenue, touchless rate, cycle time, exceptions and orders at risk by region and site.",
        "description": ("National roll-up of the order service levels by operating region and processing site: on-time "
                        "in-full (OTIF), fill rate, revenue, touchless and first-pass rates, receipt-to-D365 cycle time, "
                        "open exceptions, orders and value at risk, and cross-region sourcing. Filter by account scope to "
                        "compare national accounts with regional accounts."),
        "owner": OPS_OWNER, "steward": STEWARD,
        "refresh": {"mode": "Computed on request", "frequency": "On demand (gold mart refreshed hourly)", "freshnessSlaSeconds": 3600,
                    "detail": "Process API computes live from the ROI and service tags on each order."},
        "retention": "n/a (derived)", "grain": "One row per region, or per site within a region",
        "primaryKey": ["level", "key"], "statsKey": "orders", "qualityRules": [],
        "filters": [{"name": "level", "label": "Level", "type": "enum", "values": ["REGION", "SITE"]}] + SCOPE_FILTERS,
        "schema": [
            f("level", "enum", "REGION or SITE", key=True), f("key", "string", "Region id or warehouse", key=True),
            f("name", "string", "Region / site name"), f("region", "enum", "Region"),
            f("orders", "integer", "Orders"), f("revenue", "decimal", "Order value (excl. cancelled)"),
            f("otifRate", "decimal", "On-time in-full rate of delivered orders (0-1)"),
            f("onTimeDeliveryRate", "decimal", "Delivered on/before requested date (0-1)"),
            f("inFullRate", "decimal", "Delivered complete (0-1)"), f("fillRate", "decimal", "Shipped ÷ ordered quantity (0-1)"),
            f("touchlessRate", "decimal", "Touchless order rate (0-1)"),
            f("firstPassValidationRate", "decimal", "First-pass validation rate (0-1)"),
            f("avgReceiptToErpMinutes", "decimal", "Average receipt → D365 minutes"),
            f("openExceptions", "integer", "Orders with open exceptions"), f("ordersAtRisk", "integer", "Open orders at risk"),
            f("valueAtRisk", "decimal", "Value of orders at risk"), f("crossRegionOrders", "integer", "Orders sourced cross-region"),
        ],
        "lineage": _fulfilment_lineage("Regional & Site Performance"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/metrics/kpis?region={region}", "description": "byRegion / bySite"}],
                        "lakehouse": {"table": "sales_order.gold.gold_regional_performance"}},
        "consumers": ["Regional GMs", "National accounts team", "Executive dashboard"],
    },
    {
        "id": "orders-at-risk", "name": "Orders at Risk", "object": "At-risk Order", "domain": "Operations",
        "type": "Operational dataset", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["regional", "service-level", "chargeback", "proactive"],
        "summary": "Open orders likely to miss the customer promise: holds, late promises, missed dates, backorders, late 855s.",
        "description": ("Proactive service-recovery list. Each open order is evaluated against rules - action required "
                        "(exception, credit hold, ERP rejection, manual hold), D365 promise later than requested, requested "
                        "ship/delivery date passed, backorders, and EDI 855 not sent within 4 hours (chargeback risk) - "
                        "with severity and value at risk."),
        "owner": OPS_OWNER, "steward": STEWARD,
        "refresh": {"mode": "Computed on request", "frequency": "Continuous", "freshnessSlaSeconds": 60,
                    "detail": "Evaluated live against the current state of every open order."},
        "retention": "Current state", "grain": "One row per at-risk order and reason", "primaryKey": ["orderId", "reasonCode"],
        "statsKey": None, "qualityRules": [], "filters": SCOPE_FILTERS,
        "schema": [
            f("orderId", "string", "Order id", key=True), f("reasonCode", "enum", "ACTION_REQUIRED, LATE_PROMISE, SHIP_DATE_MISSED, DELIVERY_OVERDUE, BACKORDER, ACK_855_LATE", key=True),
            f("severity", "enum", "HIGH / MEDIUM"), f("reason", "string", "Explanation"),
            f("customerId", "string", "Customer"), f("customerName", "string", "Customer name"), f("customerPo", "string", "PO"),
            f("region", "enum", "Fulfilling region"), f("shipFromWarehouse", "string", "Site"), f("accountScope", "enum", "Account scope"),
            f("status", "enum", "Order status"), f("requestedDeliveryDate", "date", "Requested delivery"),
            f("promisedShipDate", "date", "D365 promised ship date"), f("totalAmount", "decimal", "Order value"),
        ],
        "lineage": _fulfilment_lineage("Orders at Risk"),
        "consumption": {"rest": [{"method": "GET", "path": "/process/v1/orders/at-risk", "description": "At-risk orders"}]},
        "consumers": ["Customer service", "Regional operations", "National account managers"],
    },
    {
        "id": "sites-inventory", "name": "Sites & Available Inventory", "object": "Site Inventory", "domain": "Operations",
        "type": "Master data", "version": "1.0.0", "classification": "Internal", "accessPolicy": "auto",
        "tags": ["regional", "inventory", "sourcing", "network"],
        "summary": "Processing sites by region with on-hand available cases per item - the input to network sourcing.",
        "description": ("Every processing site / warehouse in the regional network with its region and the cases available "
                        "per item (D365 on-hand). Used by order sourcing to choose the home site, an alternate site in "
                        "the region, or a cross-region site."),
        "owner": OPS_OWNER, "steward": {"name": "Inventory steward", "team": "Supply Planning", "email": "inventory@taylorfarms.example"},
        "refresh": {"mode": "Scheduled cache", "frequency": "Near real time (2 s cache over D365 on-hand)", "freshnessSlaSeconds": 300,
                    "detail": "System API reads D365 on-hand; reservations reduce availability immediately."},
        "retention": "Current state", "grain": "One row per warehouse × item", "primaryKey": ["warehouse", "itemNumber"],
        "statsKey": None, "qualityRules": [], "filters": [SCOPE_FILTERS[0], {"name": "itemNumber", "label": "Item", "type": "string"}],
        "schema": [
            f("warehouse", "string", "D365 warehouse", key=True), f("itemNumber", "string", "Item", key=True),
            f("siteId", "string", "Site"), f("siteName", "string", "Site name"), f("region", "enum", "Region"),
            f("regionName", "string", "Region name"), f("available", "decimal", "Available cases"),
        ],
        "lineage": _master_lineage("Sites & Available Inventory", "InventOnHandV2"),
        "consumption": {"rest": [{"method": "GET", "path": "/system/v1/masterdata/inventory", "description": "On-hand by site"},
                                 {"method": "GET", "path": "/system/v1/masterdata/regions", "description": "Regions & sites"}]},
        "consumers": ["Order sourcing", "Supply planning"],
    },
]


# ======================================================================================= enterprise data mart
# The Sales Order data product is one producer in the Taylor Farms enterprise data mart. The other domains are
# published from the lakehouse gold layer through the System API (`/v1/mart/{dataset}`). In this repository their
# rows are ILLUSTRATIVE synthetic data (src/tfso/system/seed/mart.json) - the contract, not the numbers, is real.
VENDOR_OWNER = {"name": "Vendor management owner", "team": "Procurement & Supplier Quality", "email": "vendor-data@taylorfarms.example"}
PROC_OWNER = {"name": "Procurement data owner", "team": "Procurement", "email": "procurement-data@taylorfarms.example"}
FIN_OWNER = {"name": "Finance data owner", "team": "Finance & Accounting", "email": "finance-data@taylorfarms.example"}
LOG_OWNER = {"name": "Logistics data owner", "team": "Transportation", "email": "logistics-data@taylorfarms.example"}
FIN_STEWARD = {"name": "Finance data steward", "team": "Financial Controls", "email": "fin-steward@taylorfarms.example"}


def _mart_lineage(dataset_label: str, entity: str, gold_table: str) -> dict[str, Any]:
    ds = {"id": "dataset", "label": dataset_label, "kind": "dataset", "stage": 4, "detail": "This dataset"}
    nodes = [{**D365, "detail": f"{entity} (OData / export to Data Lake)"},
             {"id": "bronze", "label": "Lakehouse bronze", "kind": "store", "stage": 1, "detail": "ADLS Gen2 raw extracts"},
             {"id": "silver", "label": "Lakehouse silver", "kind": "integration", "stage": 2, "detail": "Conformed, de-duplicated (DLT)"},
             {"id": "gold", "label": "Gold mart", "kind": "store", "stage": 3, "detail": gold_table},
             ds,
             {"id": "pbi", "label": "Power BI / Delta Sharing", "kind": "consumer", "stage": 5, "detail": "Analytics & partners"},
             {"id": "portal", "label": "Data mart portal & APIs", "kind": "consumer", "stage": 5, "detail": "Experience API (row-scoped)"}]
    edges = [{"from": "d365", "to": "bronze", "label": "daily"}, {"from": "bronze", "to": "silver", "label": "DLT"},
             {"from": "silver", "to": "gold", "label": "model"}, {"from": "gold", "to": "dataset", "label": "System API"},
             {"from": "dataset", "to": "pbi"}, {"from": "dataset", "to": "portal"}]
    return {"nodes": nodes, "edges": edges}


def _mart(id_: str, name: str, obj: str, domain_id: str, summary: str, description: str, schema: list[dict], *,
          owner: dict, steward: dict | None = None, classification: str = "Internal", policy: str = "auto",
          grain: str, pk: list[str], tags: list[str], entity: str, filters: list[dict] | None = None,
          audiences: list[str] | None = None, row_scope: dict | None = None, sensitive: list[str] | None = None,
          frequency: str = "Daily 05:00 PT", consumers: list[str] | None = None) -> dict[str, Any]:
    gold = f"enterprise.gold.{id_.replace('-', '_')}"
    return {
        "id": id_, "name": name, "object": obj, "domain": DOMAIN_NAMES[domain_id], "domainId": domain_id,
        "type": "Data mart - gold", "version": "1.0.0", "classification": classification, "accessPolicy": policy,
        "tags": tags + ["illustrative-data"], "summary": summary,
        "description": description + " Rows in this environment are illustrative synthetic data.",
        "owner": owner, "steward": steward or owner,
        "refresh": {"mode": "Scheduled batch", "frequency": frequency, "freshnessSlaSeconds": 26 * 3600,
                    "detail": "Lakehouse gold job; published to the mart after data-quality gates pass."},
        "retention": "7 years (finance) / 3 years (operational)", "grain": grain, "primaryKey": pk,
        "statsKey": None, "qualityRules": [], "filters": filters or [], "schema": schema,
        "sensitiveFields": sensitive or [], "martDataset": id_,
        "audiences": audiences or ["internal"], "rowScope": row_scope or {},
        "lineage": _mart_lineage(name, entity, gold),
        "consumption": {"rest": [{"method": "GET", "path": f"/system/v1/mart/{id_}", "description": "Rows (filter by any field)"}],
                        "lakehouse": {"table": gold}},
        "consumers": consumers or [],
    }


DOMAIN_NAMES = {"sales-order": "Sales Order", "customer": "Customer", "product": "Product", "logistics": "Logistics",
                "vendor": "Vendor", "procurement": "Procurement", "finance": "Finance"}
REGION_F = {"name": "region", "label": "Region", "type": "enum", "values": REGION_IDS}
VENDOR_F = {"name": "vendorId", "label": "Vendor id", "type": "string"}
COMMODITIES = ["LEAFY_GREENS", "VEGETABLES", "FRUIT", "PROTEIN", "DAIRY", "PACKAGING"]

DATASETS += [
    _mart("vendors", "Vendors", "Vendor", "vendor",
          "Approved suppliers of produce, protein, dairy and packaging with certification and risk tier.",
          "Vendor master from D365 (VendVendorV2) enriched with food-safety certification (GFSI) and supplier risk tier.",
          [f("vendorId", "string", "Vendor id", key=True, example="V-1001"), f("name", "string", "Vendor name"),
           f("category", "enum", "Primary commodity supplied"), f("homeRegion", "enum", "Region the vendor mainly supplies"),
           f("country", "string", "Country (ISO 3166)"), f("status", "enum", "ACTIVE / ON_HOLD / INACTIVE"),
           f("paymentTerms", "string", "Payment terms"), f("certification", "string", "Food-safety certification scheme"),
           f("gfsiCertified", "boolean", "GFSI-benchmarked certification held"), f("riskTier", "enum", "LOW / MEDIUM / HIGH"),
           f("onboardedDate", "date", "Approved supplier since"), f("d365VendorAccount", "string", "D365 vendor account"),
           f("contactName", "string", "Sales contact", pii=True), f("contactEmail", "string", "Contact e-mail", pii=True),
           f("contactPhone", "string", "Contact phone", pii=True)],
          owner=VENDOR_OWNER, grain="One row per vendor", pk=["vendorId"], tags=["supplier", "food-safety", "master-data"],
          entity="VendVendorV2", filters=[{"name": "category", "label": "Commodity", "type": "enum", "values": COMMODITIES},
                                          {"name": "homeRegion", "label": "Region", "type": "enum", "values": REGION_IDS},
                                          {"name": "riskTier", "label": "Risk tier", "type": "enum", "values": ["LOW", "MEDIUM", "HIGH"]}],
          audiences=["internal", "external-vendor"], row_scope={"vendor": "vendorId"},
          consumers=["Procurement", "Supplier quality", "Vendors (own record)"]),
    _mart("vendor-scorecard", "Vendor Scorecard", "Vendor Performance", "vendor",
          "Monthly supplier performance: on-time, fill rate, quality acceptance, lead time and grade.",
          "Computed from purchase orders and goods receipts per vendor and month. Shared with each vendor for their own rows.",
          [f("month", "string", "Month (YYYY-MM)", key=True), f("vendorId", "string", "Vendor id", key=True),
           f("vendorName", "string", "Vendor name"), f("category", "enum", "Commodity"), f("poCount", "integer", "Purchase orders"),
           f("spend", "decimal", "Spend (USD)"), f("onTimeRate", "decimal", "Receipts on or before expected date (0-1)"),
           f("fillRate", "decimal", "Received ÷ ordered quantity (0-1)"), f("qualityAcceptRate", "decimal", "Receipts passing inspection (0-1)"),
           f("avgLeadTimeDays", "decimal", "Average order → receipt days"), f("grade", "enum", "A / B / C / D")],
          owner=VENDOR_OWNER, classification="Confidential", policy="approval", grain="One row per vendor × month",
          pk=["month", "vendorId"], tags=["supplier", "performance", "kpi"], entity="Purchase receipts",
          filters=[VENDOR_F, {"name": "month", "label": "Month", "type": "string"}],
          audiences=["internal", "external-vendor"], row_scope={"vendor": "vendorId"}, sensitive=["spend"],
          consumers=["Procurement", "Quarterly business reviews", "Vendors (own scorecard)"]),
    _mart("purchase-orders", "Purchase Orders", "Purchase Order", "procurement",
          "Raw-material and packaging purchase orders by vendor, site and region with receipt progress.",
          "Purchase order headers from D365 (PurchPurchaseOrderHeaderV2) with received value rolled up from goods receipts.",
          [f("poNumber", "string", "Purchase order number", key=True), f("vendorId", "string", "Vendor id"),
           f("vendorName", "string", "Vendor name"), f("category", "enum", "Commodity"), f("region", "enum", "Receiving region"),
           f("warehouse", "string", "Receiving warehouse"), f("siteName", "string", "Receiving site"),
           f("buyer", "string", "Buyer", pii=True), f("orderDate", "date", "Order date"), f("expectedDate", "date", "Expected receipt"),
           f("status", "enum", "OPEN / PARTIALLY_RECEIVED / RECEIVED / CANCELLED"), f("lineCount", "integer", "Lines"),
           f("totalAmount", "decimal", "PO value"), f("receivedAmount", "decimal", "Value received"),
           f("currency", "string", "Currency"), f("d365PurchaseOrder", "string", "D365 purchase order")],
          owner=PROC_OWNER, classification="Confidential", policy="approval", grain="One row per purchase order", pk=["poNumber"],
          tags=["procure-to-pay", "regional", "supply"], entity="PurchPurchaseOrderHeaderV2",
          filters=[REGION_F, VENDOR_F, {"name": "status", "label": "Status", "type": "string"},
                   {"name": "category", "label": "Commodity", "type": "enum", "values": COMMODITIES}],
          audiences=["internal", "external-vendor"], row_scope={"vendor": "vendorId"},
          consumers=["Buyers", "Supply planning", "Vendors (own POs)"]),
    _mart("purchase-order-lines", "Purchase Order Lines", "Purchase Order Line", "procurement",
          "Material-level PO lines: ordered, received and rejected quantities and unit cost.",
          "Purchase order lines from D365 (PurchPurchaseOrderLineV2) with receipt quantities.",
          [f("poNumber", "string", "Purchase order", key=True), f("lineNumber", "integer", "Line", key=True),
           f("vendorId", "string", "Vendor id"), f("materialId", "string", "Raw material / packaging item"),
           f("description", "string", "Material description"), f("commodity", "enum", "Commodity"), f("uom", "string", "Unit"),
           f("orderedQty", "decimal", "Ordered"), f("receivedQty", "decimal", "Received"), f("rejectedQty", "decimal", "Rejected at inspection"),
           f("unitCost", "decimal", "Unit cost"), f("lineAmount", "decimal", "Line amount"), f("expectedDate", "date", "Expected"),
           f("status", "enum", "Line status"), f("region", "enum", "Receiving region"), f("warehouse", "string", "Warehouse")],
          owner=PROC_OWNER, classification="Confidential", policy="approval", grain="One row per PO line",
          pk=["poNumber", "lineNumber"], tags=["procure-to-pay", "materials"], entity="PurchPurchaseOrderLineV2",
          filters=[REGION_F, VENDOR_F, {"name": "poNumber", "label": "PO", "type": "string"}],
          audiences=["internal", "external-vendor"], row_scope={"vendor": "vendorId"}, sensitive=["unitCost"],
          consumers=["Buyers", "Vendors (own lines)"]),
    _mart("goods-receipts", "Goods Receipts & Inspection", "Goods Receipt", "procurement",
          "Inbound receipts with lot, pulp temperature and quality inspection result - the food-safety receiving record.",
          "Product receipts from D365 WMS joined to QA inspection results (pulp temperature, accept / hold / reject).",
          [f("receiptId", "string", "Receipt id", key=True), f("poNumber", "string", "Purchase order"), f("lineNumber", "integer", "PO line"),
           f("vendorId", "string", "Vendor id"), f("materialId", "string", "Material"), f("warehouse", "string", "Warehouse"),
           f("region", "enum", "Region"), f("receivedDate", "date", "Received"), f("quantity", "decimal", "Quantity"),
           f("rejectedQty", "decimal", "Rejected"), f("uom", "string", "Unit"), f("lotNumber", "string", "Vendor lot (traceability)"),
           f("pulpTempF", "decimal", "Pulp temperature at receipt (°F)"), f("inspectionResult", "enum", "ACCEPTED / HOLD / REJECTED"),
           f("onTime", "boolean", "Received by expected date")],
          owner=PROC_OWNER, steward={"name": "Supplier quality steward", "team": "Food Safety & QA", "email": "qa-data@taylorfarms.example"},
          grain="One row per receipt", pk=["receiptId"], tags=["food-safety", "traceability", "fsma-204", "receiving"],
          entity="WHSProductReceipt", filters=[REGION_F, VENDOR_F, {"name": "inspectionResult", "label": "Inspection", "type": "enum",
                                                                    "values": ["ACCEPTED", "HOLD", "REJECTED"]}],
          audiences=["internal", "external-vendor"], row_scope={"vendor": "vendorId"}, frequency="Hourly",
          consumers=["Food safety & QA", "Receiving", "Vendors (own receipts)"]),
    _mart("ap-invoices", "Vendor Invoices (AP)", "Vendor Invoice", "finance",
          "Accounts-payable invoices with three-way match result, due and paid dates.",
          "Vendor invoices from D365 (VendInvoiceJour) with PO / receipt / invoice three-way match status and variance.",
          [f("apInvoiceId", "string", "AP invoice id", key=True), f("vendorId", "string", "Vendor id"), f("vendorName", "string", "Vendor"),
           f("vendorInvoiceNumber", "string", "Vendor's invoice number"), f("poNumber", "string", "Purchase order"),
           f("region", "enum", "Region"), f("invoiceDate", "date", "Invoice date"), f("dueDate", "date", "Due date"),
           f("amount", "decimal", "Invoice amount"), f("receivedValue", "decimal", "Value received against the PO"),
           f("variance", "decimal", "Invoice − received value"), f("currency", "string", "Currency"),
           f("matchStatus", "enum", "MATCHED / PRICE_VARIANCE / QTY_VARIANCE"), f("status", "enum", "OPEN / APPROVED / PAID / ON_HOLD"),
           f("paidDate", "date", "Paid date")],
          owner=FIN_OWNER, steward=FIN_STEWARD, classification="Confidential", policy="approval", grain="One row per vendor invoice",
          pk=["apInvoiceId"], tags=["procure-to-pay", "accounts-payable", "three-way-match"], entity="VendInvoiceJour",
          filters=[REGION_F, VENDOR_F, {"name": "status", "label": "Status", "type": "string"},
                   {"name": "matchStatus", "label": "Match", "type": "enum", "values": ["MATCHED", "PRICE_VARIANCE", "QTY_VARIANCE"]}],
          audiences=["internal", "external-vendor"], row_scope={"vendor": "vendorId"},
          consumers=["Accounts payable", "Controllers", "Vendors (own remittance status)"]),
    _mart("spend-by-category", "Spend by Category", "Spend Cube", "finance",
          "Monthly purchase spend by region and commodity with vendor count and average unit cost.",
          "Aggregated from purchase order lines (gold spend cube). Used for commodity strategy and budget variance.",
          [f("month", "string", "Month", key=True), f("region", "enum", "Region", key=True), f("commodity", "enum", "Commodity", key=True),
           f("spend", "decimal", "Spend (USD)"), f("poCount", "integer", "Purchase orders"), f("vendorCount", "integer", "Vendors"),
           f("avgUnitCost", "decimal", "Average unit cost")],
          owner=FIN_OWNER, steward=FIN_STEWARD, classification="Confidential", policy="approval",
          grain="One row per month × region × commodity", pk=["month", "region", "commodity"], tags=["spend", "regional", "kpi"],
          entity="Purchase lines", filters=[REGION_F, {"name": "commodity", "label": "Commodity", "type": "enum", "values": COMMODITIES}],
          consumers=["Finance", "Category managers", "Executive dashboard"]),
    _mart("carriers", "Carriers", "Carrier", "logistics",
          "Contracted reefer and LTL carriers with service regions, insurance and 90-day performance.",
          "Carrier master from D365 TMS enriched with SmartWay status and rolling 90-day loads, on-time rate and cost per mile.",
          [f("scac", "string", "SCAC", key=True), f("name", "string", "Carrier"), f("mode", "enum", "REEFER_TL / REEFER_LTL / DEDICATED"),
           f("regionsServed", "array<string>", "Regions served"), f("status", "enum", "Status"),
           f("baseCostPerMile", "decimal", "Contract base rate per mile"), f("insuranceExpires", "date", "Insurance certificate expiry"),
           f("smartWayCertified", "boolean", "EPA SmartWay partner"), f("loads90d", "integer", "Loads (90 days)"),
           f("onTimeRate", "decimal", "On-time delivery (0-1)"), f("avgCostPerMile", "decimal", "Actual cost per mile"),
           f("temperatureExcursions", "integer", "Reefer temperature excursions (90 days)")],
          owner=LOG_OWNER, grain="One row per carrier", pk=["scac"], tags=["transportation", "cold-chain"], entity="TMSCarrier",
          sensitive=["baseCostPerMile"], consumers=["Transportation", "Regional operations"]),
    _mart("carrier-loads", "Carrier Loads", "Load", "logistics",
          "Outbound loads by carrier and lane with cost, miles, on-time and temperature excursions.",
          "Loads from D365 TMS with freight cost and telematics temperature excursion flags.",
          [f("loadId", "string", "Load id", key=True), f("scac", "string", "Carrier SCAC"), f("carrierName", "string", "Carrier"),
           f("mode", "enum", "Mode"), f("originWarehouse", "string", "Origin warehouse"), f("originRegion", "enum", "Origin region"),
           f("destinationRegion", "enum", "Destination region"), f("shipDate", "date", "Ship date"), f("deliveryDate", "date", "Delivery date"),
           f("status", "enum", "Status"), f("stops", "integer", "Stops"), f("pallets", "integer", "Pallets"), f("miles", "integer", "Miles"),
           f("freightCost", "decimal", "Freight cost"), f("costPerMile", "decimal", "Cost per mile"),
           f("onTime", "boolean", "Delivered on time"), f("temperatureExcursion", "boolean", "Reefer excursion recorded")],
          owner=LOG_OWNER, grain="One row per load", pk=["loadId"], tags=["transportation", "regional", "freight"], entity="TMSLoad",
          filters=[{"name": "originRegion", "label": "Origin region", "type": "enum", "values": REGION_IDS},
                   {"name": "scac", "label": "Carrier", "type": "string"}],
          consumers=["Transportation", "Freight audit", "Regional GMs"]),
    _mart("item-costs", "Item Standard Costs & Margin", "Item Cost", "product",
          "Standard cost build-up (raw material, packaging, labour, overhead) and margin per finished item.",
          "Cost roll-up from D365 cost management for the active costing version. Highly confidential.",
          [f("itemNumber", "string", "Item", key=True), f("description", "string", "Description"), f("listPrice", "decimal", "List price / case"),
           f("standardCost", "decimal", "Standard cost / case"), f("rawMaterialCost", "decimal", "Raw material"),
           f("packagingCost", "decimal", "Packaging"), f("laborCost", "decimal", "Labour"), f("overheadCost", "decimal", "Overhead"),
           f("marginPct", "decimal", "Margin on list (0-1)"), f("costVersion", "string", "Costing version"),
           f("costEffectiveDate", "date", "Effective"), f("active", "boolean", "Active")],
          owner=FIN_OWNER, steward=FIN_STEWARD, classification="Confidential", policy="approval", grain="One row per item",
          pk=["itemNumber"], tags=["cost", "margin", "pricing"], entity="InventItemPrice",
          sensitive=["standardCost", "rawMaterialCost", "packagingCost", "laborCost", "overheadCost", "marginPct"],
          frequency="Monthly (cost version release)", consumers=["Finance", "Pricing", "Product management"]),
]

# ------------------------------------------------------------- domain, audience and row scope of the SO datasets
_DOMAIN_OF = {"sales-orders": "sales-order", "sales-order-lines": "sales-order", "order-lifecycle-events": "sales-order",
              "order-exceptions": "sales-order", "orders-at-risk": "sales-order", "order-kpis": "sales-order",
              "regional-performance": "sales-order", "price-agreements": "sales-order", "customers": "customer",
              "ship-tos": "customer", "trading-partners": "customer", "items": "product", "sites-inventory": "product",
              "shipments": "logistics", "invoices": "finance"}
_EXTERNAL_CUSTOMER = {"sales-orders", "sales-order-lines", "shipments", "invoices", "items"}
for _d in DATASETS:
    if _d["id"] in _DOMAIN_OF:
        _d["domainId"] = _DOMAIN_OF[_d["id"]]
        _d["domain"] = DOMAIN_NAMES[_d["domainId"]]
        _d["audiences"] = ["internal"] + (["external-customer"] if _d["id"] in _EXTERNAL_CUSTOMER else [])
        _d["rowScope"] = {"customer": "customerId"} if _d["id"] in _EXTERNAL_CUSTOMER - {"items"} else {}
    _d.setdefault("audiences", ["internal"])
    _d.setdefault("rowScope", {})
    _d["public"] = _d["id"] == "items"  # product catalogue: no customer row scope

BY_ID = {d["id"]: d for d in DATASETS}
DOMAINS = sorted({d["domain"] for d in DATASETS})
