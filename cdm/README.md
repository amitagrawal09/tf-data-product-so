# Canonical Data Model – Sales Order (v1)

The contract of the Sales Order data product. The **source of truth is code**: `src/tfso/common/cdm.py`
(Pydantic v2). The JSON Schemas in this folder are generated from it, and every order is validated against the
`SalesOrder` model before it is published, so code, schemas and data stay in step.

Full documentation (element dictionary, order history, mappings, versioning policy):
[`docs/03-canonical-data-model.md`](../docs/03-canonical-data-model.md).

## Schemas (JSON Schema 2020-12)

| File | Model | Used for |
|---|---|---|
| `sales-order.v1.schema.json` | `SalesOrder` | Canonical order (`GET /process/v1/orders/{id}`, event `snapshot`, Delta `silver_order_snapshots`) |
| `order-history-entry.v1.schema.json` | `OrderHistoryEntry` | One lifecycle entry (`/history`, `orderHistory[]`, event `historyEntry`) |
| `order-group.v1.schema.json` | `OrderGroup` | Lifecycle of a group of orders (`GET /process/v1/order-groups/{id}/history`) |
| `order-status-changed.v1.schema.json` | `OrderStatusChanged` | `data` of CloudEvent `tfso.salesorder.OrderStatusChanged` (topic `sales-order-events`, Event Hubs `order-status-stream`, webhooks) |
| `status-event.v1.schema.json` | `StatusEvent` | Inbound Tier-3 status events (`system-status-events`, `POST /process/v1/events`) |

## Examples (captured from a live end-to-end run)

| File | Content |
|---|---|
| `examples/sales-order.edi-invoiced.example.json` | EDI order from receipt to invoice, with all 18 `orderHistory` entries |
| `examples/order-status-view.example.json` | `GET /process/v1/orders/{id}/status` response with milestones |
| `examples/order-group-history.example.json` | Two purchase orders from one X12 interchange and their merged history |
| `examples/order-status-changed.cloudevent.example.json` | A `SHIPPED` OrderStatusChanged CloudEvent (snapshot removed for brevity) |
| `examples/x12-850.edi` | Inbound purchase order transaction set |
| `examples/x12-997.edi`, `x12-855.edi`, `x12-856.edi`, `x12-810.edi` | Generated functional ack, PO ack, ship notice, invoice |

## Regenerate

```bash
python -m tfso.tools.export_schema --out cdm
```

Change the model in `cdm.py`, regenerate, and follow the versioning policy (additive = minor, breaking = new
major `v2` files side by side with a deprecation window).
