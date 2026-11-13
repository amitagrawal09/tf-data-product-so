# Sales Order Data Product: Order Status API (3-tier, API-led, Azure)

This repository implements the Sales Order data product described in the *Taylor Farms Sales Order Data Product* deck. It is a working end-to-end system with these parts:

- **Tier 1, Capture.** Orders come in through EDI X12 850, e-mail (body, CSV, Excel or PDF), the customer portal, customer-service manual entry, and a partner REST API.
- **Tier 2, Integrate.** The API-led *Experience → Process → System* layers ingest, validate (DQ-001…DQ-011), enrich, canonicalize, publish and orchestrate each order.
- **Tier 3, Fulfill.** D365 F&O, WMS, TMS, CRM and the lakehouse. The repo includes a functional D365/WMS/TMS simulator and a live D365 OData adapter.
- **Order Status API.** One status and one complete **lifecycle history** for every order, and for every group of orders, whatever channel it came from.
- **Customer acknowledgements.** 997, 855, 856 and 810 for EDI partners; e-mail or portal notifications for everyone else; signed webhooks for subscribers.

## Deliverables

| # | Artifact | Where |
|---|---|---|
| 1 | Requirements document | [`docs/01-requirements.md`](docs/01-requirements.md) |
| 2 | Technical design document | [`docs/02-technical-design.md`](docs/02-technical-design.md) |
| 3 | Canonical Data Model (CDM) for Order, including order history | [`docs/03-canonical-data-model.md`](docs/03-canonical-data-model.md), [`cdm/`](cdm/) (JSON Schema 2020-12, examples), source of truth [`src/tfso/common/cdm.py`](src/tfso/common/cdm.py) |
| 4 | Azure reference architecture | [`docs/04-azure-reference-architecture.md`](docs/04-azure-reference-architecture.md), IaC in [`infra/`](infra/), Databricks in [`databricks/`](databricks/) |
| 5 | Single end-to-end deploy script | [`deploy.sh`](deploy.sh) (`local`, `docker`, `azure`, `test`, `destroy`) |
| 6 | Front end for testing order processing | [`frontend/`](frontend/) (Order Operations Console) |
| – | Binding solution blueprint (contracts, routes, env vars) | [`docs/00-solution-blueprint.md`](docs/00-solution-blueprint.md) |

## Quick start (local, about 1 minute)

```bash
./deploy.sh local
```

This command:

- creates `.venv`;
- starts the System (:8003), Process (:8002) and Experience (:8001) APIs, plus a gateway (:8080) that stands in for API Management and hosts the console;
- runs the end-to-end smoke test.

Then open **http://localhost:8080/**. The API key is `local-dev-key`.

```bash
./deploy.sh test
```

Runs the automated test suite. The suite runs all three APIs in-process and covers the lifecycle, EDI, exceptions, credit hold, backorders and partial shipment, idempotency and out-of-order events.

```bash
./deploy.sh docker
```

Runs the same stack on PostgreSQL in containers.

```bash
./deploy.sh azure
```

Provisions everything in Azure with Bicep, builds the image in ACR, rolls out Container Apps, publishes the console behind APIM, and runs the smoke test. Settings are `PREFIX`, `LOCATION`, `ENVIRONMENT` and `APIM_PUBLISHER_EMAIL`; to target a real D365 instead of the simulator, also set `D365_URL`, `D365_TENANT_ID`, `D365_CLIENT_ID` and `D365_CLIENT_SECRET`. Run `./deploy.sh --help` for details.

```bash
./deploy.sh local-stop
```

Stops the local services.

Interactive API docs (OpenAPI): http://localhost:8002/docs (Process / Order Status API), `:8001/docs` (Experience) and `:8003/docs` (System).

## Try it in the console

1. **New order → Customer portal.** Submit an order. It runs RECEIVED → INGESTED → VALIDATED → ENRICHED → PUBLISHED → SUBMITTED_TO_ERP, then D365 accepts it.
2. **Order detail → Fulfillment simulator.** Release, pick, pack, ship (in full or in part), deliver, invoice. The milestone stepper and the **History** tab update from the real status events.
3. **New order → EDI X12 850.** Pick "2 – multi-order interchange". You get two orders in one *order group* and the 997. The **Documents** tab later shows the 855, 856 and 810.
4. **New order → bad price / unknown item.** The order goes to the **Exceptions** queue. Use **Correct & resubmit**, which sends a `PATCH` with `If-Match`, then resubmits.
5. **Dashboard.** Shows the touchless rate, first-pass validation rate, receipt-to-D365 cycle time by channel, data freshness and ROI estimates.

## Order Status API (Process API) in brief

```
GET  /v1/orders?status=&statusCategory=&customerId=&channel=&customerPo=&fromDate=&toDate=&hasExceptions=&q=&page=&sort=
GET  /v1/orders/{orderId}                  canonical order (CDM v1) + orderHistory, ETag
GET  /v1/orders/{orderId}/status           status, category, milestones, lines, shipments, invoices, next expected
GET  /v1/orders/{orderId}/history          ordered lifecycle (sequence, tier, source system, actor, diffs)
GET  /v1/orders/{orderId}/documents[/{id}] X12 850/997/855/856/810, e-mail, JSON
GET  /v1/orders/{orderId}/related          orders in the same group
GET  /v1/order-groups/{groupId}/history    merged lifecycle of a group of orders
GET  /v1/orders/by-po/{customerPo}
GET  /v1/exceptions · /v1/status/summary · /v1/metrics/kpis · /v1/reference/statuses
PATCH /v1/orders/{id} · POST /v1/orders/{id}/resubmit|hold|release-hold|cancel
POST /v1/events · /v1/subscriptions (HMAC-signed webhooks)
```

## Repository layout

```
src/tfso/common      CDM (Pydantic), state machine, bus (Service Bus / HTTP / in-proc), outbox+inbox, X12, web/auth
src/tfso/experience  Experience APIs: portal, partner, EDI, e-mail (+ CSV/XLSX/PDF extraction)
src/tfso/process     Process APIs: intake pipeline, quality rules, enrichment, status events, Order Status API, KPIs
src/tfso/system      System APIs: master data, D365 (simulator + live OData), WMS/TMS webhooks, CRM, lakehouse
src/tfso/gateway     Local APIM stand-in + console host
frontend/            Order Operations Console (static SPA, no build step)
infra/               Bicep: Container Apps, APIM, Service Bus, Event Hubs, PostgreSQL, ADLS, Key Vault, Doc Intelligence…
databricks/          DLT bronze → silver → gold pipeline, Unity Catalog setup
cdm/                 JSON Schemas + real examples (canonical order, status view, group history, CloudEvent, X12)
tests/               pytest suite (in-process end-to-end + unit)
```
