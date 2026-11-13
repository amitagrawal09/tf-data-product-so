# Databricks – Sales Order data product hub

Azure Databricks is the data hub (deck slide 11). The System API's lakehouse adapter writes every
`OrderStatusChanged` CloudEvent from the `sales-order-events` topic to ADLS Gen2, and the Process API
also streams the same events to Event Hubs (`order-status-stream`, consumer group `databricks`) for
low-latency consumers.

```
Process API outbox ──► Service Bus sales-order-events ──► System API (system-lakehouse sub)
                                                          └─► abfss://bronze@<lake>/sales-order-events/date=YYYY-MM-DD/*.jsonl
Process API ──► Event Hubs order-status-stream (optional streaming source)

bronze_order_events ─► silver_order_history (append-only lifecycle)
                     └► silver_sales_orders (APPLY CHANGES, latest state per order)
                          ├► gold_order_status_daily
                          ├► gold_order_cycle_time_by_channel
                          ├► gold_touchless_rate
                          ├► gold_exception_reasons
                          └► gold_roi_drivers
```

## Files

| File | Purpose |
|---|---|
| `unity_catalog/setup.sql` | Storage credential, external locations, `sales_order` catalog, bronze/silver/gold schemas, tags, grants, Delta Share |
| `pipelines/sales_order_dlt.sql` | Delta Live Tables pipeline (Auto Loader → silver → gold marts) |

## Event envelope (bronze)

CloudEvents 1.0 – `{specversion, id, source, type, subject, time, datacontenttype, correlationid, data}` with
`type = "tfso.salesorder.OrderStatusChanged"`, `source = "/process/sales-orders"`, `subject = orderId` and
`data = {orderId, orderGroupId, sequence, eventType, fromStatus, toStatus, statusCategory, occurredAt, recordedAt,
sourceSystem, sourceChannel, customerId, customerPo, erpSalesOrderId, totalAmount (string decimal), currency,
roi{touchless, touchCount, exceptionReasonCodes[],
    stageTimestamps{received, ingested, validated, enriched, published, submittedToErp, erpCreated, released,
                    picked, packed, shipped, delivered, invoiced, closed, cancelled},
    acknowledgmentTimestamps{functionalAck997, poAck855, shipNotice856, invoice810},
    cycleTimes{receiptToErpMinutes, receiptToShipHours, receiptToInvoiceHours, shipToAsnMinutes},
    chargebackRisk[]},
historyEntry{...one orderHistory entry...},
snapshot{...full canonical SalesOrder minus orderHistory; status-changing events only...}}`.
See `cdm/examples/order-status-changed.cloudevent.example.json` and `cdm/order-status-changed.v1.schema.json`.

Tables: `bronze_order_events` → `silver_order_history` (append-only, one row per history entry) →
`silver_sales_orders` (APPLY CHANGES, latest state keyed on `order_id`, sequenced by `sequence`) and
`silver_order_snapshots` (parsed canonical documents) → gold marts.

Locally the same files land in `./data/lakehouse/bronze/sales-order-events/date=YYYY-MM-DD/*.jsonl`, so the
pipeline can be tested by uploading that folder to a Volume and pointing `tfso.bronze_path` at it.

## Deploy

1. Create an Azure Databricks workspace (Premium) with Unity Catalog and an **Access Connector**; grant the
   connector *Storage Blob Data Contributor* on the lake account (Bicep output `lakehouseStorageAccount`).
2. Run `unity_catalog/setup.sql` in a SQL warehouse after replacing `<lake>` and `<access-connector-id>`.
3. Create a DLT pipeline: source `pipelines/sales_order_dlt.sql`, catalog `sales_order`, target schema `silver`
   (gold views can be moved to `gold` with `CREATE ... AS` in a second pipeline, or use the
   multi-schema publishing mode), mode *Triggered* (every 5 min) or *Continuous* for near-real-time freshness,
   and configuration:

   | Key | Example |
   |---|---|
   | `tfso.bronze_path` | `abfss://bronze@<lake>.dfs.core.windows.net/sales-order-events/` |
   | `tfso.labor_minutes_per_touch` | `6` |
   | `tfso.loaded_labor_rate_per_hr` | `38` |
   | `tfso.avg_cost_per_error` | `145` |
   | `tfso.cost_of_capital_annual` | `0.08` |

   Baseline each ROI driver for a quarter before go-live (deck slide 4) and keep the baseline in a
   `gold.roi_baseline` table that Power BI joins to `gold_roi_drivers`.
4. Serve: Databricks SQL / Power BI on gold, Delta Sharing (`sales_order_status` share) for partners.
   The *Order status for analytics* flow (deck slide 10) can also read the Process API directly via the
   APIM "Order Status API" product.
