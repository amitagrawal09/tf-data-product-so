# Solution Blueprint – Sales Order Data Product / Order Status API

This is the binding implementation contract for every artifact in this repository
(code, IaC, front end, documents). If any document disagrees with this file, this
file wins and the other document is a bug.

## 1. Repository layout

```
deploy.sh                      single end-to-end deploy script (local | docker | azure | test | destroy)
docker-compose.yml             local container topology (mirrors Azure)
docker/Dockerfile              one image, SERVICE env selects the app
pyproject.toml / requirements.txt
src/tfso/
  common/                      shared library (CDM, state machine, db, bus, auth, telemetry, x12)
  experience/                  Tier-2 Experience APIs (portal, partner, EDI, email)        :8001
  process/                     Tier-2 Process APIs (intake, fulfillment orchestration, status) :8002
  system/                      Tier-2 System APIs (D365, WMS, TMS, CRM, Lakehouse) + simulator :8003
  gateway/                     local stand-in for Azure API Management + static UI host      :8080
frontend/                      Order Operations Console (static SPA, no build step)
cdm/                           JSON Schemas generated from the Pydantic CDM + examples + X12 samples
infra/                         Bicep (main.bicep + modules) + APIM policies
databricks/                    Bronze/Silver/Gold Delta Live Tables pipeline + Unity Catalog DDL
tests/                         pytest end-to-end tests (in-process)
docs/                          01 requirements, 02 technical design, 03 CDM, 04 reference architecture
```

## 2. Runtime topology

| Service | ASGI app | Local port | Azure resource | APIM path |
|---|---|---|---|---|
| Experience API | `tfso.experience.app:app` | 8001 | Container App `ca-<prefix>-exp` | `/exp` |
| Process API (Order Status API lives here) | `tfso.process.app:app` | 8002 | Container App `ca-<prefix>-process` | `/process` |
| System API | `tfso.system.app:app` | 8003 | Container App `ca-<prefix>-system` (internal ingress) | `/system` (non-prod only, simulator + master data) |
| Gateway (local only) | `tfso.gateway.app:app` | 8080 | API Management `apim-<prefix>` | – |
| Front end | `frontend/` | served by gateway at `/` | Storage static website `$web` | – |

The front end always calls `${API_BASE}/exp/...`, `${API_BASE}/process/...`, `${API_BASE}/system/...`.
`frontend/config.js` sets `window.TFSO_CONFIG = { apiBase: "/api", subscriptionKey: "", apiKey: "" }`.
Locally `apiBase` is `/api` on the gateway (`/api/exp/*` → :8001, `/api/process/*` → :8002, `/api/system/*` → :8003).
In Azure `apiBase` is `https://apim-<prefix>.azure-api.net` and the key goes in `Ocp-Apim-Subscription-Key`.

## 3. Environment variables (all services)

| Variable | Default | Meaning |
|---|---|---|
| `TFSO_ENV` | `local` | `local`, `dev`, `test`, `prod` |
| `TFSO_DB_URL` | `sqlite:///./data/<service>.db` | SQLAlchemy URL. Azure: `postgresql+psycopg://...` |
| `TFSO_BUS_MODE` | `http` | `http` (local), `servicebus` (Azure), `inproc` (tests) |
| `TFSO_SERVICEBUS_FQDN` | – | `<ns>.servicebus.windows.net` (Managed Identity auth) |
| `TFSO_SERVICEBUS_CONNECTION` | – | optional connection string alternative |
| `TFSO_EVENTHUB_FQDN` / `TFSO_EVENTHUB_NAME` | – / `order-status-stream` | status event stream for Databricks |
| `TFSO_PROCESS_URL` | `http://localhost:8002` | used by Experience + System APIs |
| `TFSO_SYSTEM_URL` | `http://localhost:8003` | used by Process + Experience APIs |
| `TFSO_EXPERIENCE_URL` | `http://localhost:8001` | used by gateway |
| `TFSO_AUTH_MODE` | `apikey` | `none`, `apikey`, `entra` |
| `TFSO_API_KEYS` | `local-dev-key` | comma separated keys accepted in `x-api-key` (apikey mode) |
| `TFSO_ENTRA_TENANT_ID` / `TFSO_ENTRA_AUDIENCE` | – | JWT validation (entra mode) |
| `TFSO_D365_MODE` | `simulator` | `simulator` or `live` |
| `TFSO_D365_URL` / `TFSO_D365_TENANT_ID` / `TFSO_D365_CLIENT_ID` / `TFSO_D365_CLIENT_SECRET` / `TFSO_D365_COMPANY` | – / – / – / – / `usmf` | D365 F&O OData (live mode; secret from Key Vault) |
| `TFSO_SIM_AUTOPROGRESS` | `false` | simulator walks orders through fulfillment automatically |
| `TFSO_SIM_STEP_SECONDS` | `5` | delay between simulated fulfillment steps |
| `TFSO_LAKEHOUSE_MODE` | `file` | `file` (./data/lakehouse) or `adls` |
| `TFSO_LAKEHOUSE_ACCOUNT_URL` / `TFSO_LAKEHOUSE_CONTAINER` | – / `bronze` | ADLS Gen2 target |
| `TFSO_DOCINTEL_ENDPOINT` / `TFSO_DOCINTEL_MODEL_ID` | – / `prebuilt-layout` | Azure AI Document Intelligence for emailed PDFs |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | – | enables Azure Monitor OpenTelemetry |
| `TFSO_ISA_SENDER_ID` | `TAYLORFARMS` | our X12 interchange id for outbound 997/855/856/810 |

## 4. Messaging (Service Bus topics; HTTP fan-out locally)

| Topic | Producer | Subscriptions (consumer) | Message types |
|---|---|---|---|
| `sales-order-commands` | Process API | `system-api` | `CreateErpSalesOrder`, `CancelErpSalesOrder` |
| `system-status-events` | System API | `process-api` | `StatusEvent` (from D365/WMS/TMS) |
| `sales-order-events` | Process API | `system-lakehouse`, `system-crm`, `process-webhooks` | `OrderStatusChanged` (CloudEvents 1.0 envelope) |

All messages use a CloudEvents 1.0 envelope: `{specversion, id, source, type, subject(orderId), time, datacontenttype, correlationid, data}`.
Process API writes to a transactional **outbox** table in the same DB transaction as the order change; a dispatcher
publishes the outbox (at-least-once). Every consumer is idempotent by `id`.
Local HTTP bus: `POST {service}/v1/bus/{topic}` with the envelope as body.

## 5. Order status state machine (order level)

Status (`status`) → customer-facing category (`statusCategory`):

| Status | Category | Rank | Meaning |
|---|---|---|---|
| RECEIVED | RECEIVED | 10 | Captured by a Tier-1 channel |
| INGESTED | RECEIVED | 20 | Parsed into working format, orderId assigned |
| EXCEPTION | ACTION_REQUIRED | 25 | Failed a quality rule, in exception queue |
| VALIDATED | RECEIVED | 30 | All quality rules passed |
| ENRICHED | RECEIVED | 40 | Master data applied |
| PUBLISHED | RECEIVED | 50 | Canonical order published under contract |
| SUBMITTED_TO_ERP | RECEIVED | 60 | Create command sent to D365 |
| ERP_REJECTED | ACTION_REQUIRED | 65 | D365 refused the order |
| CREDIT_HOLD | ACTION_REQUIRED | 68 | D365 credit check hold |
| ACCEPTED | CONFIRMED | 70 | Sales order created in D365, inventory reserved |
| ON_HOLD | ACTION_REQUIRED | 72 | Manual hold |
| RELEASED | IN_FULFILLMENT | 80 | Released to warehouse |
| PICKED | IN_FULFILLMENT | 85 | |
| PACKED | IN_FULFILLMENT | 88 | |
| PARTIALLY_SHIPPED | SHIPPED | 90 | Some lines/qty shipped |
| SHIPPED | SHIPPED | 92 | |
| DELIVERED | DELIVERED | 95 | Proof of delivery from TMS |
| INVOICED | INVOICED | 98 | Invoice posted |
| CLOSED | CLOSED | 100 | Terminal |
| CANCELLED | CANCELLED | 100 | Terminal |
| REJECTED | CANCELLED | 100 | Terminal (non-correctable) |

Line status: `OPEN, CONFIRMED, BACKORDERED, ALLOCATED, RELEASED, PICKED, PACKED, SHIPPED, DELIVERED, INVOICED, CANCELLED`.

## 6. History event types (`orderHistory[].eventType`)

`ORDER_RECEIVED, ORDER_INGESTED, VALIDATION_PASSED, VALIDATION_FAILED, ORDER_ENRICHED, ORDER_PUBLISHED,
ORDER_CORRECTED, ORDER_RESUBMITTED, SUBMITTED_TO_ERP, ERP_ACCEPTED, ERP_REJECTED, CREDIT_HOLD_PLACED,
HOLD_PLACED, HOLD_RELEASED, INVENTORY_RESERVED, LINE_BACKORDERED, RELEASED_TO_WAREHOUSE, PICKED, PACKED,
SHIPPED, DELIVERED, INVOICED, CLOSED, CANCELLED, REJECTED, DOCUMENT_SENT, NOTIFICATION_SENT, LATE_EVENT_IGNORED,
ORDER_SPLIT, ORDER_LINKED`

Every history entry: `eventId, sequence, eventType, occurredAt, recordedAt, fromStatus, toStatus, tier, sourceSystem,
actor{type,id,name}, correlationId, causationId, externalEventId, reasonCode, message, lineChanges[], changes[] (JSON-Patch), documentRef, elapsedSincePreviousSeconds`.

## 7. Data quality rules

| Code | Rule | Severity |
|---|---|---|
| DQ-001 | Sold-to customer exists and is active | ERROR |
| DQ-002 | Ship-to exists, belongs to customer, is active | ERROR |
| DQ-003 | Item exists and is active/sellable | ERROR |
| DQ-004 | Unit of measure valid for item | ERROR |
| DQ-005 | Price matches customer agreement (±0.5%) | ERROR |
| DQ-006 | No duplicate open order (customer + customer PO) | ERROR |
| DQ-007 | Requested delivery date achievable (lead time, cut-off, delivery days) | ERROR |
| DQ-008 | Quantity > 0, multiple of order increment, ≥ minimum | ERROR |
| DQ-009 | Required elements present (PO, ship-to, ≥1 line, delivery date) | ERROR |
| DQ-010 | Shelf life / temperature compatible with ship-to | WARNING |
| DQ-011 | Extraction confidence ≥ 0.80 (email / OCR orders) | ERROR |

## 8. API surface

### Experience API (`/exp`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness/readiness |
| GET | `/portal/v1/catalog/customers` | customers (for portal pickers) |
| GET | `/portal/v1/catalog/customers/{customerId}/ship-tos` | ship-tos |
| GET | `/portal/v1/catalog/items?customerId=` | items with contract price for the customer |
| POST | `/portal/v1/orders` | portal / manual entry order (body `PortalOrderRequest`) → 202 `OrderAccepted` |
| GET | `/portal/v1/orders/{orderId}` | customer-facing status view |
| GET | `/portal/v1/customers/{customerId}/orders` | customer order list |
| POST | `/partner/v1/orders` | partner system-to-system order in canonical draft format → 202 |
| GET | `/partner/v1/orders/{orderId}/status` | partner status |
| POST | `/edi/v1/x12/850` | raw X12 850 interchange (text/plain) → 202 `{orders[], ack997}` |
| GET | `/edi/v1/orders/{orderId}/documents` | inbound/outbound EDI documents |
| POST | `/email/v1/orders` | email message (JSON with base64 attachments CSV/XLSX/PDF/TXT) → 202 |

`PortalOrderRequest`: `{channel: "PORTAL"|"MANUAL", customerId, shipToId, customerPo, requestedDeliveryDate, orderType?, currency?, notes?, contact{name,email,phone}?, enteredBy?, lines:[{itemNumber, quantity, uom, unitPrice?, notes?}]}`
`OrderAccepted`: `{orderId, orderGroupId, status, statusCategory, exceptions[], statusUrl, historyUrl, receivedAt, correlationId}`

### Process API (`/process`) – Order Intake, Fulfillment orchestration, **Order Status API**
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | |
| POST | `/v1/orders/intake` | channel-neutral intake (validate, enrich, canonicalize, publish, submit to ERP) |
| GET | `/v1/orders` | search: `status, statusCategory, customerId, channel, customerPo, erpSalesOrderId, fromDate, toDate, hasExceptions, q, page, pageSize, sort` |
| GET | `/v1/orders/{orderId}` | full canonical SalesOrder (ETag = version) |
| GET | `/v1/orders/{orderId}/status` | `OrderStatusView` (status, category, milestones, lines, shipments, invoices, nextExpected) |
| GET | `/v1/orders/{orderId}/history` | ordered lifecycle history (`eventType`, `since` filters) |
| GET | `/v1/orders/{orderId}/documents` | EDI/email/JSON documents (inbound+outbound) |
| GET | `/v1/orders/{orderId}/documents/{documentId}` | document content |
| GET | `/v1/orders/{orderId}/related` | orders in same `orderGroupId` + related order links |
| GET | `/v1/order-groups/{orderGroupId}/history` | merged lifecycle of a group of orders |
| GET | `/v1/orders/by-po/{customerPo}?customerId=` | lookup by customer PO |
| PATCH | `/v1/orders/{orderId}` | correction (EXCEPTION / ERP_REJECTED / ON_HOLD), `If-Match` version; body `{changes, reason, actor}` |
| POST | `/v1/orders/{orderId}/resubmit` | re-enter at validation |
| POST | `/v1/orders/{orderId}/hold` / `/release-hold` | manual hold |
| POST | `/v1/orders/{orderId}/cancel` | cancel (before SHIPPED) |
| POST | `/v1/events` | status events from systems (also consumed from `system-status-events`) |
| GET | `/v1/exceptions` | exception queue |
| GET | `/v1/status/summary` | counts by status / category / channel |
| GET | `/v1/metrics/kpis` | touchless rate, first-pass validation, cycle times by channel, freshness |
| GET | `/v1/reference/statuses` | state machine (statuses, transitions, categories) |
| GET/POST/DELETE | `/v1/subscriptions[/{id}]` | status webhooks (HMAC-SHA256 `x-tfso-signature`) |
| POST | `/v1/bus/{topic}` | local HTTP bus delivery |

### System API (`/system`)
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | includes adapter modes |
| GET | `/v1/masterdata/customers[/{id}]`, `/v1/masterdata/customers/{id}/ship-tos`, `/v1/masterdata/items[/{itemNumber}]`, `/v1/masterdata/price-agreements?customerId=&itemNumber=`, `/v1/masterdata/trading-partners/{isaId}`, `/v1/masterdata/item-xref?customerId=&buyerItem=&gtin=` | master data (seed or D365) |
| POST | `/v1/d365/sales-orders` | create sales order (sync use; normally via command topic) |
| GET | `/v1/d365/sales-orders/{erpSalesOrderId}` | ERP order |
| POST | `/v1/d365/business-events` | D365 business event webhook → StatusEvent |
| POST | `/v1/wms/events`, `/v1/tms/events` | WMS ship confirmation / TMS delivery webhooks → StatusEvent |
| POST | `/v1/sim/orders/{orderId}/advance` | simulator: body `{to: RELEASED|PICKED|PACKED|SHIPPED|DELIVERED|INVOICED|CLOSED, lines?:[{lineNumber, quantity}], carrier?}` |
| POST | `/v1/sim/orders/{orderId}/release-credit-hold` | simulator |
| GET/PUT | `/v1/sim/config` | `{autoProgress, stepSeconds, creditCheck, inventoryCheck}` |
| GET | `/v1/lakehouse/files` | list bronze files written (local) |
| POST | `/v1/bus/{topic}` | local HTTP bus delivery |

Errors: RFC 7807 `application/problem+json` `{type,title,status,detail,instance,correlationId,errors[]}`.
Headers: `x-correlation-id` (echoed/generated), `Idempotency-Key` on POSTs that create orders, `ETag`/`If-Match`.
