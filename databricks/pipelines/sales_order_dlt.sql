-- =====================================================================================
-- Sales Order data product – Delta Live Tables pipeline (Databricks SQL)
--
-- Source : CloudEvents written by the System API lakehouse adapter to ADLS Gen2
--          abfss://bronze@<lake>.dfs.core.windows.net/sales-order-events/date=YYYY-MM-DD/*.jsonl
--          (locally: ./data/lakehouse/bronze/sales-order-events/date=YYYY-MM-DD/*.jsonl)
-- Target : Unity Catalog catalog `sales_order` (see ../unity_catalog/setup.sql)
--
-- Pipeline configuration (Settings -> Advanced -> Configuration):
--   tfso.bronze_path = abfss://bronze@<lake>.dfs.core.windows.net/sales-order-events/
--   tfso.labor_minutes_per_touch   = 6
--   tfso.loaded_labor_rate_per_hr  = 38
--   tfso.avg_cost_per_error        = 145
--   tfso.cost_of_capital_annual    = 0.08
-- Medallion: bronze = as received, silver = Sales Order data product, gold = marts.
-- =====================================================================================

-- ------------------------------------------------------------------ BRONZE
CREATE OR REFRESH STREAMING TABLE bronze_order_events
COMMENT 'Raw order status events (CloudEvents 1.0) as received from the Process API outbox via the System API lakehouse adapter.'
TBLPROPERTIES ('quality' = 'bronze', 'pipelines.autoOptimize.zOrderCols' = 'subject')
AS SELECT
  *,
  _metadata.file_path            AS _source_file,
  _metadata.file_modification_time AS _source_file_ts,
  current_timestamp()            AS _ingested_at
FROM STREAM cloud_files(
  '${tfso.bronze_path}',
  'json',
  map(
    'cloudFiles.inferColumnTypes', 'true',
    'cloudFiles.schemaEvolutionMode', 'addNewColumns',
    'cloudFiles.schemaHints',
      'id STRING, specversion STRING, source STRING, type STRING, subject STRING, time TIMESTAMP, correlationid STRING, data STRUCT<orderId: STRING, orderGroupId: STRING, sequence: BIGINT, eventType: STRING, fromStatus: STRING, toStatus: STRING, statusCategory: STRING, occurredAt: TIMESTAMP, recordedAt: TIMESTAMP, sourceSystem: STRING, sourceChannel: STRING, customerId: STRING, customerPo: STRING, erpSalesOrderId: STRING, totalAmount: STRING, currency: STRING, roi: STRUCT<touchless: BOOLEAN, touchCount: INT, exceptionReasonCodes: ARRAY<STRING>, stageTimestamps: MAP<STRING, TIMESTAMP>, acknowledgmentTimestamps: MAP<STRING, TIMESTAMP>, cycleTimes: MAP<STRING, DOUBLE>, chargebackRisk: ARRAY<STRING>>, historyEntry: STRING, snapshot: STRING>'
  )
);

-- ------------------------------------------------------------------ SILVER: history (append-only)
CREATE OR REFRESH STREAMING TABLE silver_order_history (
  CONSTRAINT valid_event_id      EXPECT (event_id IS NOT NULL)                  ON VIOLATION DROP ROW,
  CONSTRAINT valid_order_id      EXPECT (order_id IS NOT NULL)                  ON VIOLATION DROP ROW,
  CONSTRAINT valid_occurred_at   EXPECT (occurred_at IS NOT NULL)               ON VIOLATION DROP ROW,
  CONSTRAINT known_status        EXPECT (to_status IN ('RECEIVED','INGESTED','EXCEPTION','VALIDATED','ENRICHED','PUBLISHED',
                                                        'SUBMITTED_TO_ERP','ERP_REJECTED','CREDIT_HOLD','ACCEPTED','ON_HOLD',
                                                        'RELEASED','PICKED','PACKED','PARTIALLY_SHIPPED','SHIPPED','DELIVERED',
                                                        'INVOICED','CLOSED','CANCELLED','REJECTED') OR to_status IS NULL),
  CONSTRAINT known_channel       EXPECT (source_channel IN ('EDI','EMAIL','PORTAL','MANUAL','PARTNER_API'))
)
COMMENT 'Append-only lifecycle history of every sales order (one row per status event). Delivery is at-least-once; consumers dedupe on event_id (APPLY CHANGES below is idempotent).'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT
  id                               AS event_id,
  data.orderId                     AS order_id,
  data.orderGroupId                AS order_group_id,
  data.sequence                    AS sequence,
  data.eventType                   AS event_type,
  data.fromStatus                  AS from_status,
  data.toStatus                    AS to_status,
  data.statusCategory              AS status_category,
  coalesce(data.occurredAt, time)  AS occurred_at,
  data.recordedAt                  AS recorded_at,
  data.sourceSystem                AS source_system,
  data.sourceChannel               AS source_channel,
  data.customerId                  AS customer_id,
  data.customerPo                  AS customer_po,
  data.erpSalesOrderId             AS erp_sales_order_id,
  CAST(data.totalAmount AS DECIMAL(18,2)) AS total_amount,
  data.currency                    AS currency,
  data.roi.touchless               AS touchless,
  data.roi.touchCount              AS touch_count,
  data.roi.exceptionReasonCodes    AS exception_reason_codes,
  data.roi.stageTimestamps         AS stage_timestamps,
  data.roi.acknowledgmentTimestamps AS ack_timestamps,
  data.roi.cycleTimes              AS cycle_times,
  data.roi.chargebackRisk          AS chargeback_risk,
  data.historyEntry                AS history_entry_json,
  data.snapshot                    AS snapshot_json,
  correlationid                    AS correlation_id,
  source                           AS event_source,
  _ingested_at
FROM STREAM(LIVE.bronze_order_events)
WHERE type = 'tfso.salesorder.OrderStatusChanged';

-- ------------------------------------------------------------------ SILVER: current state (SCD1)
CREATE OR REFRESH STREAMING TABLE silver_sales_orders (
  -- Mirror of the data-product quality contract (DQ-xxx are enforced upstream in the Process API;
  -- here we assert the published record is complete and fail loudly if the contract is broken)
  CONSTRAINT dq009_required_customer EXPECT (customer_id IS NOT NULL),
  CONSTRAINT dq009_required_po       EXPECT (customer_po IS NOT NULL),
  CONSTRAINT positive_total          EXPECT (total_amount IS NULL OR total_amount >= 0)
)
COMMENT 'Sales Order data product – latest status per order (keyed on order_id, sequenced by event sequence).'
TBLPROPERTIES ('quality' = 'silver', 'delta.enableChangeDataFeed' = 'true');

APPLY CHANGES INTO LIVE.silver_sales_orders
FROM STREAM(LIVE.silver_order_history)
KEYS (order_id)
SEQUENCE BY struct(sequence, occurred_at)
COLUMNS * EXCEPT (_ingested_at, event_source, history_entry_json, snapshot_json)
STORED AS SCD TYPE 1;

-- ------------------------------------------------------------------ SILVER: canonical order documents
-- Only status-changing events carry `snapshot` (the full canonical SalesOrder minus orderHistory, CDM v1).
-- Parsed with from_json against a reduced schema; the full JSON string is kept for schema-on-read.
CREATE OR REFRESH STREAMING TABLE silver_order_snapshots
COMMENT 'Canonical SalesOrder documents (CDM v1) captured at every status change.'
TBLPROPERTIES ('quality' = 'silver')
AS SELECT
  order_id,
  sequence,
  occurred_at,
  to_status,
  from_json(snapshot_json,
    'schemaVersion STRING, orderId STRING, orderGroupId STRING, version INT, orderType STRING, status STRING,
     purchaseOrder STRUCT<poNumber: STRING, poDate: STRING>,
     erp STRUCT<salesOrderId: STRING, company: STRING, warehouseId: STRING>,
     parties STRUCT<soldTo: STRUCT<partyId: STRING, name: STRING>, shipTo: STRUCT<partyId: STRING, name: STRING, gln: STRING>>,
     dates STRUCT<requestedDeliveryDate: STRING, requestedShipDate: STRING, promisedShipDate: STRING>,
     lines ARRAY<STRUCT<lineNumber: INT, item: STRUCT<itemNumber: STRING, gtin: STRING, description: STRING>,
                        quantity: STRUCT<value: STRING, uom: STRING>, status: STRING,
                        quantities: MAP<STRING, STRING>,
                        lots: ARRAY<STRUCT<lotNumber: STRING, quantity: STRING, traceabilityLotCode: STRING>>>>,
     totals STRUCT<grandTotal: STRING, currency: STRING>') AS order_doc,
  snapshot_json
FROM STREAM(LIVE.silver_order_history)
WHERE snapshot_json IS NOT NULL;

-- ------------------------------------------------------------------ GOLD marts
CREATE OR REFRESH MATERIALIZED VIEW gold_order_status_daily
COMMENT 'Orders by day, channel and current status category (customer service and planning view).'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  to_date(stage_timestamps['received'])          AS order_date,
  source_channel,
  status_category,
  to_status                                      AS status,
  count(*)                                       AS orders,
  sum(total_amount)                              AS order_value
FROM LIVE.silver_sales_orders
GROUP BY ALL;

CREATE OR REFRESH MATERIALIZED VIEW gold_order_cycle_time_by_channel
COMMENT 'Cycle times by channel: receipt -> D365 creation (deck KPI), receipt -> ship, receipt -> invoice.'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  source_channel,
  to_date(stage_timestamps['received'])                                                        AS order_date,
  count(*)                                                                                       AS orders,
  avg((unix_timestamp(stage_timestamps['erpCreated']) - unix_timestamp(stage_timestamps['received'])) / 60.0)  AS avg_receipt_to_erp_minutes,
  percentile_approx((unix_timestamp(stage_timestamps['erpCreated']) - unix_timestamp(stage_timestamps['received'])) / 60.0, 0.9) AS p90_receipt_to_erp_minutes,
  avg((unix_timestamp(stage_timestamps['shipped'])  - unix_timestamp(stage_timestamps['received'])) / 3600.0) AS avg_receipt_to_ship_hours,
  avg((unix_timestamp(stage_timestamps['invoiced']) - unix_timestamp(stage_timestamps['received'])) / 3600.0) AS avg_receipt_to_invoice_hours
FROM LIVE.silver_sales_orders
WHERE stage_timestamps['received'] IS NOT NULL
GROUP BY ALL;

CREATE OR REFRESH MATERIALIZED VIEW gold_touchless_rate
COMMENT 'Touchless order rate and first-pass validation by channel and day (deck slide 13 measures).'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  source_channel,
  to_date(stage_timestamps['received'])                                     AS order_date,
  count(*)                                                                   AS orders,
  sum(CASE WHEN touchless THEN 1 ELSE 0 END)                                 AS touchless_orders,
  round(sum(CASE WHEN touchless THEN 1 ELSE 0 END) / count(*), 4)           AS touchless_rate,
  sum(CASE WHEN size(coalesce(exception_reason_codes, array())) = 0 THEN 1 ELSE 0 END) AS first_pass_orders,
  round(sum(CASE WHEN size(coalesce(exception_reason_codes, array())) = 0 THEN 1 ELSE 0 END) / count(*), 4) AS first_pass_rate
FROM LIVE.silver_sales_orders
GROUP BY ALL;

CREATE OR REFRESH MATERIALIZED VIEW gold_exception_reasons
COMMENT 'Exception reason codes (DQ-xxx) by channel and customer – drives the errors-avoided ROI driver.'
TBLPROPERTIES ('quality' = 'gold')
AS SELECT
  source_channel,
  customer_id,
  reason_code,
  count(DISTINCT order_id) AS orders
FROM (SELECT *, explode(exception_reason_codes) AS reason_code FROM LIVE.silver_sales_orders)
GROUP BY ALL;

CREATE OR REFRESH MATERIALIZED VIEW gold_roi_drivers
COMMENT 'ROI drivers tagged on every order (deck slide 4): labor saved, errors avoided, chargeback exposure, cash sooner.'
TBLPROPERTIES ('quality' = 'gold')
AS WITH base AS (
  SELECT
    source_channel,
    customer_id,
    date_trunc('MONTH', stage_timestamps['received'])                                   AS month,
    touchless,
    coalesce(touch_count, 0)                                                             AS touch_count,
    size(coalesce(exception_reason_codes, array()))                                      AS exception_count,
    total_amount,
    (unix_timestamp(stage_timestamps['invoiced']) - unix_timestamp(stage_timestamps['received'])) / 86400.0 AS receipt_to_invoice_days,
    CASE WHEN ack_timestamps['poAck855'] IS NULL AND source_channel = 'EDI' AND to_status NOT IN ('CANCELLED','REJECTED') THEN 1 ELSE 0 END AS missing_855,
    CASE WHEN stage_timestamps['shipped'] IS NOT NULL AND ack_timestamps['shipNotice856'] IS NULL AND source_channel = 'EDI' THEN 1 ELSE 0 END AS missing_856
  FROM LIVE.silver_sales_orders
)
SELECT
  month,
  source_channel,
  customer_id,
  count(*)                                                                         AS orders,
  sum(CASE WHEN touchless THEN 1 ELSE 0 END)                                       AS touchless_orders,
  sum(touch_count)                                                                 AS manual_touches,
  -- Labor saved = touches avoided x minutes per touch x loaded labor rate (touchless orders avoided one touch each)
  round(sum(CASE WHEN touchless THEN 1 ELSE 0 END) * ${tfso.labor_minutes_per_touch} / 60.0 * ${tfso.loaded_labor_rate_per_hr}, 2) AS labor_saved_usd,
  -- Errors caught before they shipped (each exception resolved upstream avoids a credit/reshipment)
  sum(exception_count)                                                             AS errors_caught,
  round(sum(exception_count) * ${tfso.avg_cost_per_error}, 2)                      AS errors_avoided_usd,
  -- Chargeback exposure: missing EDI acknowledgments / ship notices
  sum(missing_855)                                                                 AS missing_855_acks,
  sum(missing_856)                                                                 AS missing_856_asns,
  avg(receipt_to_invoice_days)                                                     AS avg_receipt_to_invoice_days,
  sum(total_amount)                                                                AS order_value,
  -- Cash sooner is computed against the pre-go-live baseline in the BI layer:
  --   (baseline_days - avg_receipt_to_invoice_days) x daily revenue x cost_of_capital / 365
  ${tfso.cost_of_capital_annual}                                                   AS cost_of_capital_annual
FROM base
GROUP BY ALL;
