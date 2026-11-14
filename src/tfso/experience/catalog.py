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

ORDER_FILTERS = [
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
        "filters": [{"name": "eventType", "label": "Event type", "type": "string", "help": "e.g. SHIPPED,INVOICED"},
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
        "primaryKey": ["exceptionId"], "statsKey": "openExceptionOrders", "qualityRules": DQ_ORDER, "filters": [{"name": "customerId", "label": "Customer id", "type": "string"},
                                                                                                                {"name": "code", "label": "Exception code", "type": "string"}],
        "schema": [
            f("exceptionId", "string", "Exception id", key=True), f("orderId", "string", "Order id"),
            f("customerId", "string", "Customer"), f("customerName", "string", "Customer name"),
            f("customerPo", "string", "Customer PO"), f("sourceChannel", "enum", "Channel"),
            f("code", "string", "Reason code, e.g. PRICE_MISMATCH"), f("ruleId", "string", "DQ rule id"),
            f("severity", "enum", "ERROR / WARNING"), f("message", "string", "What failed"),
            f("lineNumber", "integer", "Affected line"), f("field", "string", "Affected element"),
            f("detectedAt", "timestamp", "When detected"), f("ageMinutes", "decimal", "Minutes since the order was received"),
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

BY_ID = {d["id"]: d for d in DATASETS}
DOMAINS = sorted({d["domain"] for d in DATASETS})
