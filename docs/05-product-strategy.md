# Sales Order Data Product: Product Strategy (Chief Product Officer view)

| | |
|---|---|
| Owner | Chief Product Officer |
| Audience | Executive team, regional GMs, national account leads, IT and data platform |
| Status | Proposed. The capabilities marked **Built** are working in this repository today |
| Related | [01 Requirements](01-requirements.md) · [02 Technical design](02-technical-design.md) · [03 Data model](03-canonical-data-model.md) · [04 Azure architecture](04-azure-reference-architecture.md) |

## 1. The problem worth solving

Taylor Farms sells perishable product. The window from order to delivery is measured in hours, not weeks. Every order that is rekeyed, mispriced, shipped short or acknowledged late costs money three times:

1. Labor to fix it.
2. Credits or chargebacks to the customer.
3. Product that loses shelf life while the problem is being solved.

The original release (deck slides 2–14) proved the integration pattern: any channel, one canonical order, one status. Executives run the business **by region and by national account**, though, and a single national view hides the facts that matter:

- A facility that is short on inventory.
- A region whose exception queue is growing.
- A national account served badly in one region and well in another.

## 2. Product vision

> Every Taylor Farms order is captured once, sourced from the right facility, and delivered on time and in full. Any team (customer service, regional operations, national accounts, finance, analytics) can see and use the same trusted order data for its own scope, from the whole country down to one site.

### North-star metric: Perfect Order Rate

**Perfect order** = delivered **touchless** (no manual correction) **and on time** (on or before the requested delivery date) **and in full** (every line delivered complete).

It combines the two outcomes the deck cares about:

- **Cost to serve:** the touchless rate.
- **Customer promise:** OTIF.

It moves only when both improve. It is computed live (`GET /process/v1/metrics/kpis` → `perfectOrderRate`) at every scope: national, region, site and account scope.

### Metric tree

```
Perfect order rate (north star)
├── Touchless rate ........ first-pass validation · exception reasons by channel · manual touch minutes
├── On-time delivery ...... receipt→D365 cycle time · late promises · missed ship dates · 855 latency
└── In-full delivery ...... fill rate · backorders · cross-region sourcing rate · site inventory
Guardrails: revenue, orders at risk ($ value), chargeback-risk flags, data freshness (< 60 s)
```

## 3. Who we serve and the job they hire the product for

| Persona | Job to be done | What they get in this release |
|---|---|---|
| Customer service rep (regional) | "Fix my region's broken orders before they ship late" | Exception queue and orders-at-risk list filtered to their region; correct-and-resubmit |
| Regional GM / operations lead | "Is my region keeping the customer promise, and which site is struggling?" | Regional command center: OTIF, fill rate, revenue, at-risk value, site drill-down |
| National account manager | "How are we serving FreshMart across the whole country?" | Account-scope filter (national accounts) with a per-region comparison |
| Supply planning | "Where is the demand landing and where are we short?" | Sourcing decisions on every order; cross-region rate; Sites & Inventory dataset |
| Finance | "Order to cash, by region" | Revenue and invoices by region in the marketplace, with audited exports |
| Analytics / data science | "Trusted, governed order data I can build on" | Data marketplace: 15 datasets, lineage, SLAs, approvals, Databricks gold marts |
| Executive team | "One page that tells me if we're winning" | National command center with the north-star metric and drill-down |

## 4. What this release adds and why

### 4.1 Regional and national operating model (Built)

Operating regions (West, Southwest, Central, Southeast, Northeast) and their processing sites are now reference data (`GET /system/v1/masterdata/regions`). Every order is tied to the network:

- **Demand region.** As soon as the ship-to is validated, the order belongs to the ship-to's region. A regional team therefore sees its exceptions even when the order never reached sourcing.
- **Fulfilling region and site.** Set by network sourcing (below) and stored on the canonical order (`fulfillment`, contract **v1.1.0**, an additive change under the versioning policy).
- **Account scope.** A **national account** is served from more than one region; a **regional account** from one. This is derived from the customer's active ship-tos.

The same scope filters work across the whole product, from the top-bar selector and through the APIs:

- `region` (one region or a comma-separated list)
- `site` (warehouse)
- `accountScope` (`NATIONAL` / `REGIONAL`)

They apply to:

- the order search, exception queue, status summary, KPIs, orders-at-risk and the lifecycle event feed;
- every order dataset in the marketplace.

**National** is the roll-up with no filter. The command center shows a regional comparison table and drills down to sites.

> The region and site list in the seed data is **illustrative**, a plausible network for demonstration. Replace it with the actual facility network and D365 warehouse codes before pilot (decision D-7 below).

### 4.2 Network-aware sourcing (Built)

Each order is sourced to the processing site that can actually fill it:

1. The home region's primary site, then the region's other sites.
2. Adjacent regions, then the rest of the network.
3. The first site that can fill every line from on-hand stock wins. If none can, the best fill rate wins, with the home site preferred on ties, and the remainder is backordered.

The decision is recorded on the order (`fulfillment.sourcing`: rule, reason, every candidate's fill rate) and in its history (`ORDER_SOURCED`). This answers "why did Dallas ship a New Jersey order?". D365 then creates the sales order against that warehouse.

**Why it matters:** an order is often short simply because it went to the wrong facility. Sourcing on real availability raises in-full delivery. The **cross-region sourcing rate** shows when the network is out of balance, which is a planning signal rather than a customer-service one.

### 4.3 Service-level metrics customers actually buy on (Built)

- **OTIF, on-time rate, in-full rate:** for delivered orders.
- **Fill rate:** shipped ÷ ordered quantity.
- **Revenue and average order value:** by region, site, account scope and channel.

These sit next to the deck's original measures: touchless rate, first-pass validation, cycle time and freshness.

### 4.4 Orders at risk: from reporting to prevention (Built)

`GET /process/v1/orders/at-risk` evaluates every open order against six rules:

1. **Action required:** exception, credit hold, ERP rejection or manual hold.
2. **Late promise:** D365 promised a ship date later than requested.
3. **Ship date missed.**
4. **Delivery overdue.**
5. **Backorder.**
6. **EDI 855 not sent within 4 hours:** a chargeback risk.

Each order gets a severity and the dollar value at risk. This is the daily work list for customer service and regional operations, scoped to their region.

### 4.5 The data marketplace for the regional model (Built)

All order datasets accept `region` and `accountScope` filters, and three new Operations datasets were added:

- **Regional & Site Performance**
- **Orders at Risk**
- **Sites & Available Inventory**

The Databricks pipeline gains `gold_order_latest` and `gold_regional_performance`. The latter uses grouping sets, so national, regional, site and account-scope roll-ups come from one table.

## 5. Roadmap

| Horizon | Item | Why | Success measure |
|---|---|---|---|
| **Now (pilot, 0–3 months)** | Replace the illustrative network with real sites and D365 warehouses; connect live D365 on-hand inventory to sourcing | Sourcing is only as good as its inventory signal | Sourcing decisions match planner choice ≥ 95% |
| | Pilot EDI for 2–3 national accounts in one region, with the regional command center live | Proves value where volume and chargebacks are highest | Baseline vs pilot: touchless +20 pts, 855 within SLA ≥ 98% |
| | Regional role-based access (Entra ID groups → region claim → APIM injects `region` scope) | Regional teams should see only their scope by default | 100% of console users scoped |
| **Next (3–9 months)** | Change orders (X12 860/865) and backorder splits as linked orders in the same group | Customers change orders constantly; the history must show it | Change orders processed touchlessly ≥ 70% |
| | Shelf-life-aware sourcing: pick the site and lot that meets the customer's minimum remaining shelf life | Freshness is the product; shrink and rejections fall | Shelf-life rejections −50% |
| | Delivery ETA from TMS telematics; proactive customer notification when at risk | Turns "where is my order" calls into pushes | Order-status calls −40% |
| | Freight-cost-aware sourcing (cost vs service trade-off) | Cross-region sourcing has a real cost | Freight cost per case within target |
| **Later (9–18 months)** | Demand sensing from the order stream into regional planning (Databricks ML) | Orders arrive before forecasts update | Forecast error −10% at site level |
| | Customer-facing status API and portal for national accounts (Delta Sharing for their analysts) | Customers self-serve; fewer EDI 870/status calls | Adoption by top 10 accounts |
| | Chargeback recovery: match deductions to the order history evidence (855/856 timestamps, PODs) | The history already proves compliance | Recovered deductions $/quarter |

## 6. Operating model for the product

- **Product owner** (business) for the Sales Order data product, plus a **data steward per region** who owns exception-queue health and the region's reference data.
- **Monthly business review per region:** perfect order rate, OTIF, at-risk value, top exception reasons. The marketplace exports are the evidence pack.
- **Contract governance:**
  - Additive changes are minor versions (as v1.1.0 did for `fulfillment`).
  - Breaking changes are a new major version, announced to consumers registered in the marketplace.

## 7. Decisions needed

| # | Decision | Recommendation |
|---|---|---|
| D-7 | Confirm the regional structure and site list (and the D365 warehouse mapping) | Use the operations org chart; one region per general manager |
| D-8 | Definition of "on time": requested delivery date vs appointment window | Start with the requested date; move to the appointment window once TMS supplies it |
| D-9 | Who can approve cross-region sourcing automatically vs with a planner's sign-off | Automatic below a freight-cost threshold; planner sign-off above it |
| D-10 | Region visibility rules: can regional users see national accounts' other regions? | Yes, read-only, for national account managers only |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Inventory signal is stale, so sourcing chooses a site that can't ship | 2-second cache over D365 on-hand; D365 remains authoritative and backorders the difference; monitor sourcing-vs-actual |
| Regional metrics are gamed (e.g. pushing orders to other regions) | Report cross-region rate alongside OTIF; the north star is national |
| Too many "at risk" flags, so people stop looking | Severity and value ranking; tune the rule thresholds with customer service during the pilot |
| Seed and demo data mistaken for real network data | Marked illustrative in the seed file and in this document; replace before pilot |

## 9. How to see it

```bash
./deploy.sh local
```

```bash
./deploy.sh demo-data
```

On Windows, use `.\deploy.ps1 local` then `.\deploy.ps1 demo-data`.

Open the console and use the **Scope** selector in the top bar: National → a region → a site. Then switch **Accounts** to "National accounts" to compare how national customers are served in each region.
