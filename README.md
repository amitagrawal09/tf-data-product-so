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
| – | One-click Azure deployment guide | [`docs/06-azure-one-click-deployment.md`](docs/06-azure-one-click-deployment.md), [`infra/oneclick.bicep`](infra/oneclick.bicep), [`.github/workflows/deploy-azure.yml`](.github/workflows/deploy-azure.yml) |
| – | Product strategy (CPO view): north star, regional operating model, roadmap | [`docs/05-product-strategy.md`](docs/05-product-strategy.md) |
| – | Binding solution blueprint (contracts, routes, env vars) | [`docs/00-solution-blueprint.md`](docs/00-solution-blueprint.md) |
| – | Enterprise Data Mart access contract (roles, personas, domains, API) | [`docs/07-data-mart-access-contract.md`](docs/07-data-mart-access-contract.md) |
| – | Connect Entra ID (Azure AD) from Settings, step by step | [`docs/09-connect-entra-id.md`](docs/09-connect-entra-id.md) |
| – | Single sign-on (Entra ID, External ID, B2B) and access control guide | [`docs/08-sso-and-access-control.md`](docs/08-sso-and-access-control.md), [`scripts/entra-setup.sh`](scripts/entra-setup.sh) |

## Deploy to Azure (one click)

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Famitagrawal09%2Ftf-data-product-so%2Fclaude%2Fdreamy-shannon-1j1v3d%2Finfra%2Fazuredeploy.json)

Each option deploys the platform, the image, the apps, API Management and the console, then optionally runs the smoke test and loads demo data:

- **GitHub Actions:** run `scripts/azure-github-setup.sh` once in Azure Cloud Shell, then go to **Actions → Deploy to Azure → Run workflow**. This is the recommended option: it uses OIDC with no stored secrets, and test and prod can require approvals.
- **Azure portal:** publish once with `scripts/publish-template-spec.sh`, then go to **Template specs → tfso-sales-order → Deploy**. This also works for private repositories.
- **The button above:** needs a public repository and the generated `infra/azuredeploy.json`.

See [`docs/06-azure-one-click-deployment.md`](docs/06-azure-one-click-deployment.md) for prerequisites, timings, costs and troubleshooting.

## Quick start (local, about 1 minute)

```bash
./deploy.sh local
```

This command:

- creates `.venv`;
- starts the System (:8003), Process (:8002) and Experience (:8001) APIs, plus a gateway (:8080) that stands in for API Management and hosts the console;
- runs the end-to-end smoke test.

Then open **http://localhost:8080/**. To load realistic activity across every region, channel and account type, run `./deploy.sh demo-data` (Windows: `.\deploy.ps1 demo-data`). The API key is `local-dev-key`.

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

### Windows

Use the PowerShell script from the repo folder. It needs Python 3.11+ from python.org, with "Add python.exe to PATH" ticked.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

```powershell
.\deploy.ps1 local
```

`.\deploy.ps1 local-stop`, `test`, `smoke` and `docker` work the same way. `.\deploy.ps1 azure` hands over to `deploy.sh` through WSL or Git Bash, because the Azure flow is bash-only. `deploy.sh` itself also runs in Git Bash or WSL.

Interactive API docs (OpenAPI): http://localhost:8002/docs (Process / Order Status API), `:8001/docs` (Experience) and `:8003/docs` (System).

## Try it in the console

**Sign in.** Locally the console runs in **dev** mode: open http://localhost:8080 and pick a test persona on the sign-in page. Each persona has different roles and data scope, so you can see access control at work:

| Persona | Sees |
|---|---|
| Avery Admin (`admin`) | Everything, including **Access & roles** |
| Sam Analyst (`sales.analyst`) | Sales Order, Customer, Product and Logistics domains; can export; personal data masked |
| Casey Service (`cs.agent`) | Order operations; personal data visible |
| Morgan West (`regional.gm.west`) | West region rows only |
| Pat Buyer (`procurement.buyer`) | Procurement, Vendor and Product domains |
| Frankie Finance (`finance.controller`) | Finance and related domains; approves access requests |
| Pat Rivera, FreshMart (`ext.customer.freshmart`) | External customer portal: FreshMart's own orders, shipments and invoices |
| Jordan Greens, Salinas Valley Greens (`ext.vendor.salinas`) | External vendor portal: own purchase orders, receipts and AP invoices |

In Azure, production signs in with **Microsoft Entra ID**:

- employees through the Taylor Farms tenant;
- customers and vendors through Entra External ID or as B2B guests.

**Connect Entra ID from the console:** an administrator opens **Settings → Single sign-on**, enters the tenant and two app IDs, checks the connection, does a test sign-in, then enables SSO for everyone. No redeploy is needed. Follow [docs/09-connect-entra-id.md](docs/09-connect-entra-id.md) step by step. Locked out? Run `./deploy.sh sso-reset`.

Alternatively, run `scripts/entra-setup.sh` once, then deploy. See [docs/08](docs/08-sso-and-access-control.md) for the setup and [docs/07](docs/07-data-mart-access-contract.md) for the roles and rules.

**Regional and national operations.** Use the **Scope** selector in the top bar to choose National (all regions), a region (West, Southwest, Central, Southeast, Northeast) or a single processing site, and **Accounts** to choose national or regional accounts. The dashboard, orders, exceptions, orders at risk and marketplace all follow that scope.

Each order is sourced to the site that can fill it, and the order page explains why. The dashboard shows:

- the **perfect order rate** (touchless + on time + in full), OTIF, fill rate and revenue;
- a regional comparison table that drills down to sites;
- the orders-at-risk list.

The region and site network in the seed data is illustrative. Replace it with the real facility list before a pilot.

1. **New order → Customer portal.** Submit an order. It runs RECEIVED → INGESTED → VALIDATED → ENRICHED → PUBLISHED → SUBMITTED_TO_ERP, then D365 accepts it.
2. **Order detail → Fulfillment simulator.** Release, pick, pack, ship (in full or in part), deliver, invoice. The milestone stepper and the **History** tab update from the real status events.
3. **New order → EDI X12 850.** Pick "2 – multi-order interchange". You get two orders in one *order group* and the 997. The **Documents** tab later shows the 855, 856 and 810.
4. **New order → bad price / unknown item.** The order goes to the **Exceptions** queue. Use **Correct & resubmit**, which sends a `PATCH` with `If-Match`, then resubmits.
5. **Data marketplace.** Browse the 12 governed datasets: Sales Orders, lines, lifecycle history, exceptions, shipments/traceability, invoices, KPIs, customers, ship-tos, items, price agreements and EDI partners. Each dataset page shows:
   - its data definition, lineage, refresh frequency and freshness SLA, owner/steward and live record count;
   - a data preview;
   - export to CSV, JSON, NDJSON or Excel;
   - access requests, which confidential datasets (customers, invoices, prices) need a data owner to approve, with PII masked until then.

   Every request is fulfilled by the Process and System APIs, and the page shows exactly which calls were made. Every export is audited.
6. **Dashboard.** Shows the touchless rate, first-pass validation rate, receipt-to-D365 cycle time by channel, data freshness and ROI estimates.

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
