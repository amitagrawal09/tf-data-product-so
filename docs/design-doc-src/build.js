// Builds the GetOrderStatus API Solution Design Document (.docx)
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, Header, Footer,
  AlignmentType, HeadingLevel, LevelFormat, BorderStyle, WidthType, ShadingType, PageNumber,
  TableOfContents, PageBreak, TabStopType,
} = require("docx");

const DIR = process.argv[2];
const OUT = process.argv[3];
const FONT = "Arial";
const MONO = "Consolas";
const GREEN = "2B7A3D";
const CONTENT = 9360; // US Letter, 1" margins

/* ------------------------------------------------------------------ helpers */
// inline markup: **bold**, `code`
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(new TextRun({ text: text.slice(last, m.index), ...base }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ text: t.slice(2, -2), bold: true, ...base }));
    else out.push(new TextRun({ text: t.slice(1, -1), font: MONO, size: (base.size || 21) - 2, color: "1F4E2C", ...base, bold: base.bold }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ text: text.slice(last), ...base }));
  return out;
}
const P = (text, opts = {}) => new Paragraph({ children: runs(text, opts.run || {}), spacing: { after: 120, line: 276 }, ...opts.para });
const H1 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)], pageBreakBefore: true });
const H1n = (t) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(t)] });
const H2 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(t)] });
const H3 = (t) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(t)] });
const B = (t, level = 0) => new Paragraph({ numbering: { reference: "bullets", level }, children: runs(t), spacing: { after: 60, line: 264 } });
let listInstance = 0;
const NEWLIST = () => { listInstance++; };
const N = (t) => new Paragraph({ numbering: { reference: "numbers", level: 0, instance: listInstance }, children: runs(t), spacing: { after: 60, line: 264 } });
const NOTE = (t) => new Paragraph({
  children: runs(t, { size: 20 }), spacing: { before: 60, after: 160 },
  shading: { type: ShadingType.CLEAR, color: "auto", fill: "EEF6F0" },
  border: { left: { style: BorderStyle.SINGLE, size: 18, color: GREEN, space: 8 } }, indent: { left: 120 },
});
function CODE(lines) {
  const arr = Array.isArray(lines) ? lines : lines.split("\n");
  return arr.map((l, i) => new Paragraph({
    children: [new TextRun({ text: l.length ? l : " ", font: MONO, size: 16, color: "1F2D24" })],
    shading: { type: ShadingType.CLEAR, color: "auto", fill: "F3F5F4" },
    spacing: { before: i === 0 ? 80 : 0, after: i === arr.length - 1 ? 160 : 0, line: 240 },
    indent: { left: 120, right: 120 },
  }));
}
const border = { style: BorderStyle.SINGLE, size: 4, color: "C9D2CC" };
const borders = { top: border, bottom: border, left: border, right: border };
function TABLE(headers, rows, widths, opts = {}) {
  const total = widths.reduce((a, b) => a + b, 0);
  if (total !== CONTENT) throw new Error(`table widths ${total} != ${CONTENT} for ${headers.join("|")}`);
  const size = opts.size || 18;
  const cell = (text, w, header, shade) => new TableCell({
    borders, width: { size: w, type: WidthType.DXA },
    shading: header ? { fill: GREEN, type: ShadingType.CLEAR, color: "auto" } : (shade ? { fill: "F6F8F7", type: ShadingType.CLEAR, color: "auto" } : undefined),
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: String(text).split("\n").map(line => new Paragraph({
      children: header ? [new TextRun({ text: line, bold: true, color: "FFFFFF", size })] : runs(line, { size }),
      spacing: { after: 20, line: 252 },
    })),
  });
  return new Table({
    width: { size: CONTENT, type: WidthType.DXA }, columnWidths: widths,
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, widths[i], true)) }),
      ...rows.map((r, ri) => new TableRow({ children: r.map((c, i) => cell(c, widths[i], false, ri % 2 === 1)) })),
    ],
  });
}
const SP = () => new Paragraph({ children: [], spacing: { after: 80 } });
let figNo = 0;
function FIG(file, w, h, caption) {
  figNo++;
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 60 },
      children: [new ImageRun({ type: "png", data: fs.readFileSync(path.join(DIR, file)), transformation: { width: w, height: h },
        altText: { title: caption, description: caption, name: file } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 200 },
      children: [new TextRun({ text: `Figure ${figNo}. ${caption}`, italics: true, size: 18, color: "4B5A51" })] }),
  ];
}
let tblNo = 0;
const CAP = (t) => { tblNo++; return new Paragraph({ spacing: { before: 160, after: 80 }, keepNext: true, children: [new TextRun({ text: `Table ${tblNo}. ${t}`, bold: true, size: 18, color: "4B5A51" })] }); };

/* ------------------------------------------------------------------ content */
const c = [];

// ---------- title page
c.push(new Paragraph({ spacing: { before: 2200, after: 200 }, children: [new TextRun({ text: "TAYLOR FARMS · SALES ORDER DATA PRODUCT", bold: true, color: GREEN, size: 22 })] }));
c.push(new Paragraph({ spacing: { after: 120 }, children: [new TextRun({ text: "GetOrderStatus API", bold: true, size: 64, font: FONT })] }));
c.push(new Paragraph({ spacing: { after: 480 }, children: [new TextRun({ text: "Solution Design Document", size: 40, color: "4B5A51" })] }));
c.push(new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: GREEN, space: 4 } }, children: [] }));
c.push(new Paragraph({ spacing: { before: 240, after: 80 }, children: runs("Three-tier API architecture · Order query patterns · Canonical Data Model · Azure reference architecture", { size: 22, color: "4B5A51" }) }));
c.push(SP());
c.push(TABLE(["Item", "Value"], [
  ["Document", "GetOrderStatus API: Solution Design Document"],
  ["Version", "1.1"],
  ["Date", "16 November 2026"],
  ["Status", "Draft for review"],
  ["Owner", "Sales Order data product owner (to be named)"],
  ["Classification", "Internal"],
], [2600, 6760]));

// ---------- document control
c.push(H1("Document control"));
c.push(H2("Revision history"));
c.push(TABLE(["Version", "Date", "Author", "Summary"], [
  ["0.1", "13 Nov 2026", "Solution architecture", "Initial API outline from the Sales Order data product deck (slides 5, 10, 11, 12)"],
  ["1.0", "15 Nov 2026", "Solution architecture", "Complete design: query patterns, operation specifications, status model, CDM 1.1.0, regional scope filters, Azure reference architecture, security and operations"],
  ["1.1", "16 Nov 2026", "Solution architecture", "Operational data store changed from Azure Database for PostgreSQL to Databricks Lakebase (Postgres-compatible OLTP in Azure Databricks, governed by Unity Catalog). Updated architecture, diagrams, components, request path, resilience, decisions and risks; added §6.3 Why Databricks Lakebase"],
], [1100, 1500, 2000, 4760]));
c.push(H2("Approvals"));
c.push(TABLE(["Role", "Name", "Signature / decision", "Date"], [
  ["Sales Order data product owner", "", "", ""],
  ["Enterprise / solution architect", "", "", ""],
  ["IT integration lead (Azure)", "", "", ""],
  ["D365 platform owner", "", "", ""],
  ["Information security", "", "", ""],
], [3100, 2200, 2400, 1660]));
c.push(H2("Related documents"));
c.push(TABLE(["Reference", "Document", "Location"], [
  ["[R1]", "Requirements specification v2.0", "docs/01-requirements.md"],
  ["[R2]", "Technical specification v2.0", "docs/02-technical-design.md"],
  ["[R3]", "Canonical data model (element dictionary)", "docs/03-canonical-data-model.md, cdm/*.schema.json"],
  ["[R4]", "Azure reference architecture", "docs/04-azure-reference-architecture.md"],
  ["[R5]", "Product strategy", "docs/05-product-strategy.md"],
  ["[R6]", "One-click Azure deployment guide", "docs/06-azure-one-click-deployment.md"],
  ["[R7]", "Taylor Farms Sales Order Data Product deck", "Source presentation (14 slides)"],
], [1100, 4300, 3960]));

c.push(H1n("Table of contents"));
c.push(new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }));
c.push(NOTE("If the table of contents is empty, right-click it in Word and choose **Update Field**, or accept Word's prompt to update fields when the document opens."));

// ---------- 1 Introduction
c.push(H1("1. Introduction"));
c.push(H2("1.1 Purpose"));
c.push(P("This document is the solution design for **GetOrderStatus**: the Order Status API of the Taylor Farms Sales Order data product. It specifies how customer service, customers, partners, regional operations and analytics ask \"where is my order?\". They can query by order, by purchase order, by D365 sales order, by customer, by status, by date, by region or site, by lifecycle event and in aggregate."));
c.push(P("It also documents the three-tier, API-led architecture the API belongs to and the Canonical Data Model (CDM) it returns. Finally, it covers the Azure reference architecture that hosts it."));
c.push(H2("1.2 Scope"));
c.push(TABLE(["In scope", "Out of scope"], [
  ["All read (query) operations of the Order Status API in the Process layer and the customer-facing views in the Experience layer\nPush delivery of status changes (webhooks, event stream)\nStatus and milestone model, lifecycle history, order groups\nCDM 1.1.0 definition and explanation\nThree-tier API architecture and Azure reference architecture\nSecurity, performance, availability, observability, versioning",
   "Order capture and intake design (covered in [R2])\nCustomer-service write actions (correct, resubmit, hold, cancel), which are referenced only where they affect status\nD365 configuration, WMS/TMS internals\nDetailed Databricks pipeline design (see [R4])"],
], [4680, 4680]));
c.push(H2("1.3 Audience"));
c.push(B("**Product owner and business stakeholders:** what the API answers and the guarantees it gives."));
c.push(B("**Integration and application developers:** the operations, parameters, payloads and error contract."));
c.push(B("**Architects, platform and security teams:** the architecture, hosting, identity and operations."));
c.push(B("**Data and analytics teams:** the CDM, the history model and the event stream."));
c.push(H2("1.4 Definitions and acronyms"));
c.push(TABLE(["Term", "Meaning"], [
  ["GetOrderStatus", "The family of read operations that return the status and lifecycle of sales orders (the Order Status API)"],
  ["API-led connectivity", "Experience APIs (one per channel or consumer) over Process APIs (business orchestration) over System APIs (one per system of record)"],
  ["CDM", "Canonical Data Model: the single, versioned contract for a sales order (contract version 1.1.0)"],
  ["Status / status category", "Detailed lifecycle state (e.g. PICKED) and its customer-facing grouping (e.g. IN_FULFILLMENT)"],
  ["Milestone", "Customer-facing step (Received → Validated → Published → Confirmed → Released → Picked → Shipped → Delivered → Invoiced)"],
  ["Order group", "Orders that belong together: the POs of one EDI interchange or e-mail, splits, backorders, replacements"],
  ["Scope", "Operating scope filter: National (no filter), a region, a site (warehouse) and/or account scope (NATIONAL or REGIONAL accounts)"],
  ["APIM", "Azure API Management, the single front door for all APIs"],
  ["D365 F&O", "Microsoft Dynamics 365 Finance & Operations (ERP, system of record for fulfilment)"],
  ["OTIF", "On-time in-full: delivered on or before the requested date with every line complete"],
  ["RFC 7807", "Standard JSON error format (application/problem+json)"],
  ["ETag", "Entity tag. Here it is the order version, used for caching and optimistic concurrency"],
  ["X12 850/855/856/810/997", "EDI purchase order, PO acknowledgement, ship notice, invoice, functional acknowledgement"],
], [2600, 6760]));

// ---------- 2 Business context
c.push(H1("2. Business context and requirements"));
c.push(H2("2.1 Problem"));
c.push(P("Before the data product, order status was scattered across EDI logs, inboxes, the portal, D365, the warehouse and transport systems. Answering \"where is my order?\" meant checking several systems, and a status rarely explained why an order was stuck. Regional and national views did not exist, so a facility with an inventory shortfall or a growing exception queue stayed invisible until customers complained."));
c.push(H2("2.2 Objectives for GetOrderStatus")); NEWLIST();
c.push(N("**One answer for every order, whatever the channel:** EDI, e-mail, portal, manual entry and partner API all return the same status model."));
c.push(N("**Any way the business asks:** query by order, PO, D365 number, customer, status, date, region, site or account type, and see the full lifecycle of one order or a group of orders."));
c.push(N("**Explainable status:** milestones, the next expected step, open exceptions and the history of who and what changed the order."));
c.push(N("**Proactive service:** orders at risk, service-level KPIs, and push notifications so consumers do not have to poll."));
c.push(N("**Governed and secure:** read-only products, least privilege, customer scoping and an auditable contract."));
c.push(H2("2.3 Key functional requirements"));
c.push(CAP("Functional requirements addressed by this design (see [R1] for the full list)"));
c.push(TABLE(["ID", "Requirement", "Design reference"], [
  ["GOS-01", "Return the current status, category, milestones, line quantities, shipments and invoices for an order id", "§4.4 Q1"],
  ["GOS-02", "Return the full canonical order (CDM) with its lifecycle history", "§4.4 Q2"],
  ["GOS-03", "Search orders by status, category, customer, channel, PO, D365 number, date range, open exceptions and free text, with paging and sorting", "§4.4 Q3"],
  ["GOS-04", "Find orders by customer purchase order number (optionally per customer)", "§4.4 Q4"],
  ["GOS-05", "Find an order by D365 sales order number", "§4.4 Q5"],
  ["GOS-06", "Customer- and partner-facing status views that hide internal data", "§4.4 Q6"],
  ["GOS-07", "Return the ordered lifecycle history of an order, filterable by event type and time", "§4.4 Q7"],
  ["GOS-08", "Return related orders and the merged history of an order group", "§4.4 Q8"],
  ["GOS-09", "Provide a cross-order event feed for analytics and integration", "§4.4 Q9"],
  ["GOS-10", "List the exception queue and the orders at risk of missing the customer promise", "§4.4 Q10–Q11"],
  ["GOS-11", "Provide status summaries and service KPIs (perfect order, OTIF, fill rate, touchless, cycle time)", "§4.4 Q12"],
  ["GOS-12", "Filter every list, summary and KPI by region, site and account scope; National = no filter", "§4.3, §4.4"],
  ["GOS-13", "Return the documents exchanged for an order (850, 997, 855, 856, 810, e-mails)", "§4.4 Q13"],
  ["GOS-14", "Push status changes to subscribers (signed webhooks, event stream)", "§4.4 Q15"],
], [900, 6060, 2400]));
c.push(H2("2.4 Key non-functional requirements"));
c.push(TABLE(["Quality", "Target"], [
  ["Latency", "p95 < 300 ms for single-order queries; p95 < 800 ms for searches of one page"],
  ["Throughput", "200 requests/s sustained per environment; APIM rate limit 600 calls/min per subscription key"],
  ["Availability", "99.9% monthly for the Order Status API in production"],
  ["Freshness", "Status visible within 60 s of the source event (D365, WMS, TMS)"],
  ["Security", "TLS 1.2+, subscription keys or Entra ID tokens at APIM, managed identity behind it, read-only product for status consumers"],
  ["Auditability", "Every status change recorded as an immutable history entry; 7-year retention"],
  ["Compatibility", "Additive changes only within a major version; breaking changes require a new major version with notice"],
], [2200, 7160]));

// ---------- 3 Architecture
c.push(H1("3. Three-tier API architecture"));
c.push(H2("3.1 Overview"));
c.push(P("The solution follows the three tiers in the Sales Order data product deck. **Tier 1** captures orders from every channel. **Tier 2** integrates and governs them. **Tier 3** fulfils them in D365 and the warehouse, transport and CRM systems."));
c.push(P("Inside Tier 2, APIs are organised in API-led layers. **Experience** APIs serve one channel or consumer. **Process** APIs orchestrate the order and own the Order Status API. **System** APIs unlock each system of record. Azure API Management is the single front door."));
c.push(...FIG("d1.png", 624, 363, "Three-tier, API-led architecture. The green path is GetOrderStatus."));
c.push(H2("3.2 Tier responsibilities"));
c.push(TABLE(["Tier", "Responsibility", "Components"], [
  ["Tier 1 · Capture", "Receive orders in any format; present status to people and systems", "EDI trading partners (AS2/VAN), e-mail inbox, customer portal and CS console, partner systems, status consumers, data marketplace users"],
  ["Tier 2 · Integrate", "Validate, enrich, source, canonicalize and publish orders; orchestrate fulfilment; keep one status; serve queries and events", "API Management; Experience, Process and System APIs (Azure Container Apps); Databricks Lakebase data product store; Service Bus; Event Hubs; Key Vault"],
  ["Tier 3 · Fulfil", "Create, reserve, release, ship, deliver and invoice; analytics", "D365 F&O, WMS, TMS, CRM, Azure Databricks (bronze, silver, gold)"],
], [1800, 3600, 3960]));
c.push(H2("3.3 API-led layers and where GetOrderStatus lives"));
c.push(TABLE(["Layer", "Purpose", "GetOrderStatus role", "Base path"], [
  ["Experience", "One API per channel or audience; shapes data for that consumer; enforces customer scoping", "Customer and partner status views; customer order lists; marketplace datasets built on status data", "/exp"],
  ["Process", "Business orchestration over the canonical order; owns the data product", "**Owns GetOrderStatus**: all query operations, history, groups, event feed, at-risk, KPIs, webhooks", "/process"],
  ["System", "One API per system of record; no business logic", "Not called at query time. Feeds status in (D365/WMS/TMS events) and serves master data (regions, sites)", "/system"],
], [1400, 2900, 3800, 1260]));
c.push(H2("3.4 Use only the layers a flow needs"));
c.push(TABLE(["Consumer", "Path", "Why"], [
  ["Customer on the portal", "APIM → Experience (/exp/portal/v1/orders/{id}) → Process", "Needs a customer-safe view and customer scoping"],
  ["Partner system", "APIM → Experience (/exp/partner/v1/orders/{id}/status) → Process", "Partner contract isolated from internal changes"],
  ["Customer service console, regional operations", "APIM → Process (/process/v1/...)", "Internal users need the full status, exceptions and history"],
  ["Databricks / Power BI", "APIM → Process (/v1/order-events, /v1/metrics/kpis) or the event stream", "No experience shaping needed (deck slide 10)"],
  ["Daily bulk history to the lakehouse", "Events → Event Hubs / bronze", "No business logic, so no Process call per order"],
], [2700, 4060, 2600]));
c.push(H2("3.5 How status stays current (write side vs read side)"));
c.push(P("GetOrderStatus is a **read model**. It never calls D365 or the warehouse at query time, which keeps it fast and available even when a system of record is slow."));
c.push(B("**Write side:** order intake and status events from D365, WMS and TMS (through the System API and the `system-status-events` topic) are applied by the Process API. Every change updates the canonical order and appends an immutable history entry in one database transaction, which also queues an `OrderStatusChanged` event (transactional outbox)."));
c.push(B("**Read side:** GetOrderStatus reads the canonical order document and indexed projection columns (status, customer, PO, region, site and so on) from Databricks Lakebase, a Postgres-compatible operational database inside Azure Databricks."));
c.push(B("**Push:** the outbox publishes `OrderStatusChanged` CloudEvents to Service Bus (`sales-order-events`) and Event Hubs. Subscribers include the lakehouse, CRM and signed customer webhooks."));
c.push(H2("3.6 Key design decisions"));
c.push(TABLE(["ID", "Decision", "Rationale", "Alternatives considered"], [
  ["AD-01", "GetOrderStatus lives in the Process layer; Experience APIs only shape it", "One status for every channel; customer views cannot drift", "Status per channel API (rejected: duplicated logic)"],
  ["AD-02", "Query a persisted read model, never the ERP at request time", "Latency, availability and cost independent of D365", "Live D365 OData calls (rejected: slow, couples uptime)"],
  ["AD-03", "Canonical document in Databricks Lakebase (Postgres-compatible, JSONB) plus indexed projection columns", "Millisecond point reads and transactions for the live API; governed in Unity Catalog with the Delta lakehouse, so operational and analytical order data share one Databricks platform", "Azure Database for PostgreSQL (current implementation; drop-in fallback). Delta tables with a Databricks SQL warehouse (rejected for the live API: latency in seconds, costly row updates). Cosmos DB"],
  ["AD-04", "Append-only history table and CloudEvents via a transactional outbox", "Audit, replay and exactly-once effects over at-least-once messaging", "Dual writes (rejected: lost or duplicated events)"],
  ["AD-05", "APIM as the single front door with a read-only Order Status product", "Central security, quotas, analytics and versioning", "Direct ingress to Container Apps (rejected)"],
  ["AD-06", "Region, site and account scope are first-class query filters", "The business runs by region and by national account", "Client-side filtering (rejected: paging and security)"],
], [800, 2900, 3100, 2560]));

// ---------- 4 API design
c.push(H1("4. GetOrderStatus API design"));
c.push(H2("4.1 API overview"));
c.push(TABLE(["Attribute", "Value"], [
  ["API name", "Order Status API (GetOrderStatus), part of the Sales Order Process API"],
  ["Style / format", "REST over HTTPS, JSON (camelCase), UTF-8; errors as RFC 7807 application/problem+json"],
  ["Base URL (Azure)", "https://<apim-name>.azure-api.net/process  (internal), /exp (customer and partner views)"],
  ["Base URL (local)", "http://localhost:8080/api/process  (gateway that mirrors APIM)"],
  ["Version", "v1 in the path (/v1/...); CDM contract 1.1.0 in schemaVersion"],
  ["Authentication", "APIM subscription key (Ocp-Apim-Subscription-Key) or Entra ID bearer token at the gateway; backend x-api-key injected by APIM from Key Vault"],
  ["APIM products", "Order Status API (read-only GET, quota 100,000 calls/day); Sales Order Data Product (full); Order Operations Console"],
  ["Machine-readable spec", "OpenAPI 3 at /process/openapi.json and interactive docs at /docs on each service"],
  ["Owner", "Sales Order data product team"],
], [2400, 6960]));
c.push(H2("4.2 Resource model"));
c.push(TABLE(["Resource", "Description", "Identifier"], [
  ["Order", "The canonical sales order (CDM SalesOrder), including history on request", "orderId, e.g. SO-20261113-63B9D3E7"],
  ["Order status view", "A concise, computed view: status, category, milestones, lines, shipments, invoices, next expected step", "orderId"],
  ["History entry", "One immutable lifecycle event of an order", "eventId; ordered by sequence"],
  ["Order group", "Orders that arrived or belong together, with merged history", "orderGroupId, e.g. OG-20261113-D69EA874"],
  ["Document", "An inbound or outbound document (X12, e-mail, JSON)", "documentId"],
  ["Subscription", "A webhook registration for push notifications", "subscriptionId"],
], [2100, 4860, 2400]));

c.push(H2("4.3 Ways to query orders"));
c.push(P("GetOrderStatus supports the following query patterns. Every list-style query accepts the **operating scope** filters: `region` (one or more of WEST, SOUTHWEST, CENTRAL, SOUTHEAST, NORTHEAST), `site` (warehouse such as WH-SAL) and `accountScope` (NATIONAL or REGIONAL). Omitting them returns the national view."));
c.push(CAP("Query patterns"));
c.push(TABLE(["#", "Business question", "Operation", "Example"], [
  ["Q1", "What is the status of this order?", "GET /v1/orders/{orderId}/status", "/v1/orders/SO-20261113-63B9D3E7/status"],
  ["Q2", "Give me everything about this order", "GET /v1/orders/{orderId}", "/v1/orders/SO-…?includeHistory=true"],
  ["Q3", "Which orders match these criteria?", "GET /v1/orders", "/v1/orders?status=SHIPPED&region=WEST&fromDate=2026-11-01"],
  ["Q4", "Where is my PO?", "GET /v1/orders/by-po/{customerPo}", "/v1/orders/by-po/FM590128A?customerId=C-1001"],
  ["Q5", "Which order is this D365 sales order?", "GET /v1/orders?erpSalesOrderId=", "/v1/orders?erpSalesOrderId=SO-00100002"],
  ["Q6", "What can my customer or partner see?", "GET /exp/portal/v1/orders/{id}\nGET /exp/partner/v1/orders/{id}/status\nGET /exp/portal/v1/customers/{id}/orders", "/exp/portal/v1/customers/C-1001/orders?status=SHIPPED"],
  ["Q7", "What happened to this order, and when?", "GET /v1/orders/{orderId}/history", "/v1/orders/SO-…/history?eventType=SHIPPED,INVOICED"],
  ["Q8", "Which orders arrived together, and their combined lifecycle?", "GET /v1/orders/{orderId}/related\nGET /v1/order-groups/{groupId}/history", "/v1/order-groups/OG-20261113-D69EA874/history"],
  ["Q9", "What changed across all orders since a given time?", "GET /v1/order-events", "/v1/order-events?since=2026-11-14T00:00:00Z&region=CENTRAL"],
  ["Q10", "Which orders need customer-service action?", "GET /v1/exceptions", "/v1/exceptions?code=PRICE_MISMATCH&region=WEST"],
  ["Q11", "Which orders may miss the promise?", "GET /v1/orders/at-risk", "/v1/orders/at-risk?accountScope=NATIONAL"],
  ["Q12", "How are we performing?", "GET /v1/status/summary\nGET /v1/metrics/kpis", "/v1/metrics/kpis?region=NORTHEAST"],
  ["Q13", "Show me the EDI documents for this order", "GET /v1/orders/{orderId}/documents[/{documentId}]", "/v1/orders/SO-…/documents"],
  ["Q14", "What statuses exist and how do they move?", "GET /v1/reference/statuses", "/v1/reference/statuses"],
  ["Q15", "Tell me when it changes (push, not poll)", "POST /v1/subscriptions (webhooks); Service Bus / Event Hubs", "eventTypes: [\"SHIPPED\",\"DELIVERED\"]"],
], [500, 2560, 3100, 3200], { size: 17 }));
c.push(NOTE("All Process paths in this section are relative to the Process API base (`/process` through APIM). Experience paths start with `/exp`."));

// ---------- 4.4 Operation specifications
c.push(H2("4.4 Operation specifications"));

// Q1
c.push(H3("Q1 · Get order status (GetOrderStatus)"));
c.push(TABLE(["Item", "Specification"], [
  ["Operation", "GET /v1/orders/{orderId}/status"],
  ["Purpose", "Concise, computed status of one order for people and systems"],
  ["Path parameters", "orderId (string, required): data product order id"],
  ["Query parameters", "view (enum internal | customer, default internal). The customer view replaces openExceptions and roi with a customer-safe openIssues list"],
  ["Response 200", "OrderStatusView (see below); header ETag = \"<version>\""],
  ["Errors", "401 unauthorised · 404 order not found · 429 rate limited"],
], [2200, 7160]));
c.push(CAP("OrderStatusView fields"));
c.push(TABLE(["Field", "Type", "Description"], [
  ["orderId, orderGroupId, version", "string, string, integer", "Identity, group and optimistic-concurrency version"],
  ["customerId, customerName, customerPo", "string", "Sold-to customer and their purchase order number"],
  ["shipToId, shipToName, sourceChannel", "string", "Delivery location and capture channel (EDI, EMAIL, PORTAL, MANUAL, PARTNER_API)"],
  ["status, statusCategory, statusDescription", "enum, enum, string", "Detailed status, customer-facing category and a plain-language description (§4.5)"],
  ["statusReason, statusUpdatedAt, terminal", "string, date-time, boolean", "Why the order is held or cancelled; last change; whether the lifecycle has ended"],
  ["erpSalesOrderId", "string", "D365 sales order number once created"],
  ["region, regionName, shipFromWarehouse, shipFromSite, sourcingRule", "string", "Regional fulfilment: fulfilling region and site, and the sourcing rule applied"],
  ["requestedDeliveryDate, promisedShipDate", "date", "Customer request and D365 promise"],
  ["milestones[]", "array", "milestone, label, status (DONE, CURRENT, PENDING, SKIPPED), at, blocked, blockedReason"],
  ["nextExpected", "object", "Next milestone and the date it is expected by"],
  ["lines[]", "array", "lineNumber, itemNumber, buyerItemNumber, description, uom, status, ordered, confirmed, backordered, shipped, delivered, invoiced, cancelled"],
  ["shipments[], invoices[], acknowledgments[]", "array", "Carrier, tracking, BOL and dates; invoice id, amount and due date; documents sent"],
  ["totals", "object", "lineCount, subtotal, grandTotal, currency"],
  ["lastEvent", "object", "eventType, occurredAt, sourceSystem of the latest history entry"],
  ["openExceptions[] / openIssues[]", "array", "Internal: full exceptions. Customer view: code and message only"],
  ["roi", "object", "Internal view only: touchless, touchCount, cycleTimes, chargebackRisk"],
  ["asOf", "date-time", "When the view was computed"],
], [3300, 1800, 4260], { size: 17 }));
c.push(P("**Example (abridged)**: `GET /process/v1/orders/SO-20261113-63B9D3E7/status`"));
c.push(...CODE(`HTTP/1.1 200 OK
ETag: "9"
x-correlation-id: 4f1c2a9e-7d3b-4a51-9a0e-1e2f3b4c5d6e
{
  "orderId": "SO-20261113-63B9D3E7", "orderGroupId": "OG-20261113-D69EA874", "version": 9,
  "customerId": "C-1001", "customerName": "FreshMart Grocery", "customerPo": "FM590128A",
  "shipToId": "ST-1001-01", "shipToName": "FreshMart DC 12", "sourceChannel": "EDI",
  "status": "INVOICED", "statusCategory": "INVOICED", "statusDescription": "Invoice posted in D365",
  "erpSalesOrderId": "SO-00100002", "region": "WEST", "regionName": "West",
  "shipFromWarehouse": "WH-SAL", "sourcingRule": "HOME_SITE",
  "requestedDeliveryDate": "2026-11-16", "promisedShipDate": "2026-11-15",
  "milestones": [
    {"milestone": "RECEIVED", "label": "Order received", "status": "DONE", "at": "2026-11-13T17:15:27Z"},
    {"milestone": "ACCEPTED", "label": "Confirmed in D365", "status": "DONE", "at": "2026-11-13T17:15:28Z"},
    {"milestone": "INVOICED", "label": "Invoiced", "status": "DONE", "at": "2026-11-13T17:15:28Z"}
  ],
  "nextExpected": null,
  "lines": [{"lineNumber": 1, "itemNumber": "TF-10001", "buyerItemNumber": "FM-55012",
             "description": "Chopped Romaine", "uom": "CA", "status": "INVOICED",
             "ordered": "40", "confirmed": "40", "shipped": "40", "delivered": "40", "invoiced": "40"}],
  "shipments": [{"shipmentId": "SHP-500002", "carrierName": "Cold Road Freight",
                 "trackingNumber": "CRSF500002966", "status": "DELIVERED"}],
  "invoices": [{"invoiceId": "INV-700002", "amount": "980.00", "currency": "USD"}],
  "asOf": "2026-11-13T17:16:02Z"
}`));

// Q2
c.push(H3("Q2 · Get canonical order"));
c.push(TABLE(["Item", "Specification"], [
  ["Operation", "GET /v1/orders/{orderId}"],
  ["Purpose", "The complete canonical SalesOrder (CDM 1.1.0): parties, lines, fulfilment and sourcing, shipments, invoices, acknowledgements, data quality, exceptions, ROI tags, related orders, audit and orderHistory"],
  ["Query parameters", "includeHistory (boolean, default true): include orderHistory[]"],
  ["Request headers", "If-None-Match: \"<version>\" returns 304 Not Modified when unchanged (cheap polling)"],
  ["Response 200", "SalesOrder document (§5); header ETag = \"<version>\""],
  ["Errors", "304 not modified · 401 · 404 · 429"],
], [2200, 7160]));

// Q3
c.push(H3("Q3 · Search orders"));
c.push(TABLE(["Item", "Specification"], [
  ["Operation", "GET /v1/orders"],
  ["Purpose", "Find and page through orders by any combination of criteria"],
  ["Response 200", "{ items: OrderSummary[], page, pageSize, total, totalPages }"],
  ["Errors", "400 invalid sort · 401 · 422 invalid parameter · 429"],
], [2200, 7160]));
c.push(CAP("Search parameters (all optional, combined with AND)"));
c.push(TABLE(["Parameter", "Type", "Behaviour"], [
  ["status", "enum list", "One or more detailed statuses, comma-separated (e.g. ACCEPTED,RELEASED)"],
  ["statusCategory", "enum list", "One or more categories (e.g. ACTION_REQUIRED, SHIPPED)"],
  ["customerId", "string", "Sold-to customer id"],
  ["channel", "enum list", "EDI, EMAIL, PORTAL, MANUAL, PARTNER_API"],
  ["customerPo", "string", "Exact customer purchase order number"],
  ["erpSalesOrderId", "string", "Exact D365 sales order number"],
  ["fromDate, toDate", "date", "Received-at range (inclusive, UTC days)"],
  ["hasExceptions", "boolean", "true: only orders with open exceptions; false: only without"],
  ["q", "string", "Free text, case-insensitive, matched against order id, customer PO, customer name, customer id and D365 number"],
  ["region", "enum list", "Fulfilling region(s): WEST, SOUTHWEST, CENTRAL, SOUTHEAST, NORTHEAST"],
  ["site", "string list", "Ship-from warehouse(s), e.g. WH-SAL,WH-DAL"],
  ["accountScope", "enum", "NATIONAL (customer served from several regions) or REGIONAL"],
  ["page, pageSize", "integer", "1-based page; pageSize 1–200 (default 25)"],
  ["sort", "string", "receivedAt (default -receivedAt), updatedAt, status, customerId, customerPo, totalAmount, requestedDeliveryDate, orderId; prefix '-' for descending"],
  ["full", "boolean", "Include the canonical document in each item (bulk extraction; use with modest pageSize)"],
], [2000, 1500, 5860], { size: 17 }));
c.push(P("Each OrderSummary item contains: orderId, orderGroupId, status, statusCategory, sourceChannel, customerId, customerName, customerPo, shipToId, erpSalesOrderId, requestedDeliveryDate, receivedAt, updatedAt, totalAmount, currency, openExceptionCount, touchless, version, region, regionName, shipFromWarehouse, shipFromSite, accountScope and crossRegion."));
c.push(...CODE(`GET /process/v1/orders?statusCategory=IN_FULFILLMENT,SHIPPED&region=CENTRAL&accountScope=NATIONAL&sort=-updatedAt&pageSize=50
GET /process/v1/orders?hasExceptions=true&channel=EDI&fromDate=2026-11-01&toDate=2026-11-15
GET /process/v1/orders?q=freshmart&site=WH-NJ`));

// Q4/Q5
c.push(H3("Q4 · Find by customer PO   ·   Q5 · Find by D365 sales order"));
c.push(TABLE(["Item", "Specification"], [
  ["Q4 operation", "GET /v1/orders/by-po/{customerPo}?customerId={customerId}"],
  ["Q4 behaviour", "Returns the same page structure as search (max 50). A PO number is only unique per customer, so pass customerId whenever it is known. 404 when no order matches"],
  ["Q5 operation", "GET /v1/orders?erpSalesOrderId={D365 SalesOrderNumber}"],
  ["Q5 behaviour", "Exact match on the D365 number stored when D365 accepted the order. Returns zero or one item"],
], [2200, 7160]));

// Q6
c.push(H3("Q6 · Customer- and partner-facing views (Experience layer)"));
c.push(TABLE(["Operation", "Purpose", "Notes"], [
  ["GET /exp/portal/v1/orders/{orderId}", "Customer portal order status", "Calls Process status with view=customer; enforces that the caller's customer matches the order"],
  ["GET /exp/portal/v1/customers/{customerId}/orders", "A customer's order list", "Parameters: status (status category), page, pageSize; customer scoping enforced"],
  ["GET /exp/partner/v1/orders/{orderId}/status", "Partner system status", "Customer-safe view for system-to-system partners"],
  ["GET /exp/edi/v1/orders/{orderId}/documents", "EDI documents for an order", "Optional includeContent=true returns the X12 text"],
], [3400, 2600, 3360]));
c.push(P("Customer scoping: when a caller's token carries a customer claim (`extension_customerId` from Entra External ID), Experience APIs return 403 for any other customer's data."));

// Q7
c.push(H3("Q7 · Order lifecycle history"));
c.push(TABLE(["Item", "Specification"], [
  ["Operation", "GET /v1/orders/{orderId}/history"],
  ["Query parameters", "eventType (comma-separated list, e.g. SHIPPED,INVOICED); since (date-time, recorded at or after)"],
  ["Response 200", "{ orderId, items: OrderHistoryEntry[] (ordered by sequence), count }"],
  ["Use", "Audit, SLA and cycle-time analysis, \"why is my order late\" investigations, chargeback evidence (855/856 timestamps)"],
], [2200, 7160]));
c.push(...CODE(`{"sequence": 14, "eventType": "SHIPPED", "fromStatus": "PACKED", "toStatus": "SHIPPED",
 "tier": "TIER3", "sourceSystem": "WMS", "actor": {"type": "SYSTEM", "id": "WMS"},
 "occurredAt": "2026-11-13T17:15:28.52Z", "recordedAt": "2026-11-13T17:15:28.55Z",
 "elapsedSincePreviousSeconds": 0.015, "message": "Shipment SHP-500002 via Cold Road Freight",
 "externalEventId": "evt-4b1…", "lineChanges": [{"lineNumber": 1, "field": "quantities.shipped",
 "fromValue": "0", "toValue": "40"}]}`));

// Q8
c.push(H3("Q8 · Related orders and order-group history"));
c.push(TABLE(["Operation", "Response"], [
  ["GET /v1/orders/{orderId}/related", "{ orderId, orderGroupId, orders[] (summary + relationType: SELF, SAME_INTERCHANGE, SAME_GROUP …), relatedOrders[] }"],
  ["GET /v1/order-groups/{orderGroupId}/history", "{ orderGroupId, origin (ISA control number or e-mail id), orderIds[], aggregateStatusCategory, orders[], createdAt, history[] merged across all orders by time }"],
], [3700, 5660]));
c.push(P("**Aggregate status category** for a group: ACTION_REQUIRED if any order needs action, otherwise the least advanced category in the group. This means \"the whole interchange is shipped\" only when every order is."));

// Q9
c.push(H3("Q9 · Cross-order event feed"));
c.push(TABLE(["Item", "Specification"], [
  ["Operation", "GET /v1/order-events"],
  ["Query parameters", "since, until (date-time); eventType (list); orderId; sourceSystem (e.g. D365, WMS, TMS); region, site, accountScope; page (1-based), pageSize (1–1000, default 200)"],
  ["Response 200", "{ items: OrderHistoryEntry[] oldest first, page, pageSize, total, totalPages, lastRecordedAt }"],
  ["Use", "Incremental extraction (store lastRecordedAt, ask for since=…), integration catch-up and audit exports. For streaming, prefer the event stream (Q15)"],
], [2200, 7160]));

// Q10/Q11
c.push(H3("Q10 · Exception queue   ·   Q11 · Orders at risk"));
c.push(TABLE(["Item", "Specification"], [
  ["Q10 operation", "GET /v1/exceptions?customerId=&code=&region=&site=&accountScope="],
  ["Q10 response", "{ items: [OrderSummary + exceptions[] (code, ruleId, severity, message, lineNumber, field, detectedAt) + ageMinutes], total, byCode{} }"],
  ["Q11 operation", "GET /v1/orders/at-risk?region=&site=&accountScope=&customerId="],
  ["Q11 response", "{ items: [OrderSummary + promisedShipDate + severity (HIGH, MEDIUM) + reasons[] {code, message}], total, valueAtRisk, byReason{}, bySeverity{} }, sorted HIGH first, then by value"],
], [2200, 7160]));
c.push(CAP("At-risk rules (evaluated on every open order)"));
c.push(TABLE(["Code", "Rule", "Severity"], [
  ["ACTION_REQUIRED", "Order is in EXCEPTION, ERP_REJECTED, CREDIT_HOLD or ON_HOLD", "HIGH"],
  ["SHIP_DATE_MISSED", "Requested ship date has passed and nothing has shipped", "HIGH"],
  ["DELIVERY_OVERDUE", "Requested delivery date has passed and the order is not delivered", "HIGH"],
  ["LATE_PROMISE", "D365 promised ship date is later than the requested ship date", "MEDIUM"],
  ["BACKORDER", "Backordered quantity on an order that has not shipped", "MEDIUM"],
  ["ACK_855_LATE", "EDI order with no 855 acknowledgement more than 4 hours after receipt (chargeback risk)", "MEDIUM"],
], [2200, 5560, 1600]));

// Q12
c.push(H3("Q12 · Status summary and service KPIs"));
c.push(TABLE(["Operation", "Returns"], [
  ["GET /v1/status/summary?region=&site=&accountScope=", "total, byStatus, byStatusCategory, byChannel, byRegion, bySite, byAccountScope, openExceptions, scope, asOf"],
  ["GET /v1/metrics/kpis?region=&site=&accountScope=&laborRatePerHour=", "Headline KPIs plus breakdowns byChannel[], byRegion[], bySite[], byAccountScope[] and the assumptions used"],
], [4200, 5160]));
c.push(CAP("KPI definitions"));
c.push(TABLE(["KPI", "Definition"], [
  ["perfectOrderRate (north star)", "Delivered orders that were touchless AND on time AND in full ÷ delivered orders"],
  ["otifRate", "Delivered orders delivered on or before the requested delivery date with every line delivered in full ÷ delivered orders"],
  ["onTimeDeliveryRate / inFullRate", "The two OTIF components separately"],
  ["fillRate", "Shipped quantity ÷ ordered quantity, for orders that have shipped"],
  ["touchlessRate", "Orders that reached D365 with no manual touch ÷ orders that reached D365"],
  ["firstPassValidationRate", "Orders passing all quality rules on the first attempt ÷ validated orders"],
  ["avgReceiptToErpMinutes / avgReceiptToShipHours", "Average cycle times from receipt"],
  ["revenue / avgOrderValue", "Order value excluding cancelled and rejected orders"],
  ["ordersAtRisk / valueAtRisk", "Count and value of orders flagged by the at-risk rules"],
  ["crossRegionSourcingRate", "Orders shipped from outside the ship-to's home region ÷ orders with a region"],
  ["dataFreshnessSeconds", "Average lag between a source event (D365, WMS, TMS) and its recording; future-dated events excluded"],
], [3300, 6060]));

// Q13/Q14
c.push(H3("Q13 · Documents   ·   Q14 · Reference data"));
c.push(TABLE(["Operation", "Returns"], [
  ["GET /v1/orders/{orderId}/documents", "{ orderId, items: [documentId, documentType (X12_850, X12_997, X12_855, X12_856, X12_810, EMAIL_NOTIFICATION, PORTAL_FORM, PARTNER_JSON …), direction (INBOUND or OUTBOUND), controlNumber, partner, contentType, createdAt, sizeBytes] }"],
  ["GET /v1/orders/{orderId}/documents/{documentId}", "Document metadata and content (e.g. the X12 text)"],
  ["GET /v1/reference/statuses", "statuses[] (status, category, rank, tier, description, terminal, correctable, allowedTransitions), categories, lineStatuses, historyEventTypes, milestones, systemEvents"],
], [3700, 5660]));

// Q15
c.push(H3("Q15 · Push notifications (subscribe instead of polling)"));
c.push(TABLE(["Mechanism", "Specification"], [
  ["Webhooks", "POST /v1/subscriptions {callbackUrl (HTTPS in prod), eventTypes[] (history event types or statuses; [\"*\"] = all), customerId (optional filter)} → 201 with subscriptionId and secret. GET /v1/subscriptions lists them; DELETE /v1/subscriptions/{id} removes one"],
  ["Webhook delivery", "POST to callbackUrl with a CloudEvent body; headers x-tfso-signature: sha256=<HMAC-SHA256 of the raw body with the secret>, x-tfso-event-id. Delivery counts, failures and last status are tracked per subscription"],
  ["Service Bus", "Topic sales-order-events; CloudEvent type tfso.salesorder.OrderStatusChanged; subject = orderId; one subscription per internal consumer (lakehouse, CRM, webhooks)"],
  ["Event Hubs", "order-status-stream (partitioned by orderId) for Databricks and high-volume consumers"],
], [2000, 7360]));
c.push(P("Consumers must verify the signature, de-duplicate by event id (delivery is at-least-once), and use `sequence` to order events of the same order."));

// 4.5 status model
c.push(H2("4.5 Status model"));
c.push(CAP("Order statuses and categories"));
c.push(TABLE(["Status", "Category", "Meaning"], [
  ["RECEIVED", "RECEIVED", "Captured by a Tier 1 channel"],
  ["INGESTED", "RECEIVED", "Parsed into the working format; order id assigned"],
  ["EXCEPTION", "ACTION_REQUIRED", "Failed a quality rule; held in the exception queue"],
  ["VALIDATED", "RECEIVED", "All quality rules passed"],
  ["ENRICHED", "RECEIVED", "Master data applied (and the order sourced to a site)"],
  ["PUBLISHED", "RECEIVED", "Canonical order published under the contract"],
  ["SUBMITTED_TO_ERP", "RECEIVED", "Create command sent to D365"],
  ["ERP_REJECTED", "ACTION_REQUIRED", "D365 refused the order"],
  ["CREDIT_HOLD", "ACTION_REQUIRED", "D365 credit check placed the order on hold"],
  ["ACCEPTED", "CONFIRMED", "Sales order created in D365 and inventory reserved"],
  ["ON_HOLD", "ACTION_REQUIRED", "Manual hold"],
  ["RELEASED", "IN_FULFILLMENT", "Released to the warehouse"],
  ["PICKED / PACKED", "IN_FULFILLMENT", "Picked; packed and staged"],
  ["PARTIALLY_SHIPPED", "SHIPPED", "Some lines or quantity shipped"],
  ["SHIPPED", "SHIPPED", "Shipped; ship notice sent"],
  ["DELIVERED", "DELIVERED", "Proof of delivery received from TMS"],
  ["INVOICED", "INVOICED", "Invoice posted in D365"],
  ["CLOSED", "CLOSED", "Order complete (terminal)"],
  ["CANCELLED / REJECTED", "CANCELLED", "Cancelled; rejected and not correctable (terminal)"],
], [2400, 2200, 4760], { size: 17 }));
c.push(P("**Transitions** move forward through fulfilment and may skip steps (for example WMS reports SHIPPED without PICKED); they never move backwards. Events that arrive late or out of order are recorded as `LATE_EVENT_IGNORED` in the history without regressing the status. The machine-readable state machine is returned by Q14."));
c.push(P("**Milestones** (customer view): Order received → Validated & enriched → Published → Confirmed in D365 → Released to warehouse → Picked & packed → Shipped → Delivered → Invoiced. Each is DONE (with timestamp), CURRENT (with blocked/blockedReason when action is required), PENDING or SKIPPED (cancelled or closed orders)."));
c.push(P("**Line statuses:** OPEN, CONFIRMED, BACKORDERED, ALLOCATED, RELEASED, PICKED, PACKED, SHIPPED, DELIVERED, INVOICED, CANCELLED."));

// 4.6 conventions
c.push(H2("4.6 Conventions and headers"));
c.push(TABLE(["Convention", "Rule"], [
  ["Correlation", "x-correlation-id is accepted or generated, returned on every response and propagated to every downstream call and log line"],
  ["Caching", "Single-order responses carry ETag = \"<version>\"; If-None-Match returns 304. APIM response caching (for example 30 s) is an option for very high-volume consumers; it is not enabled by default"],
  ["Paging", "page/pageSize with total and totalPages; maximum pageSize 200 (event feed 1000)"],
  ["Dates and times", "ISO 8601; dates YYYY-MM-DD; timestamps in UTC with Z"],
  ["Numbers", "Quantities and amounts are decimal strings (\"40\", \"980.00\") to avoid floating-point loss; rates are numbers 0–1"],
  ["Naming", "camelCase JSON; enumerations UPPER_SNAKE_CASE"],
  ["Nulls", "Optional fields may be omitted or null; consumers must tolerate additional fields (forward compatibility)"],
], [2000, 7360]));

// 4.7 errors
c.push(H2("4.7 Error handling"));
c.push(P("All errors use RFC 7807 problem details with the correlation id:"));
c.push(...CODE(`HTTP/1.1 404 Not Found
Content-Type: application/problem+json
{"type": "https://tfso.taylorfarms.example/problems/order-not-found", "title": "Order not found",
 "status": 404, "detail": "No order SO-20261113-XXXXXXXX", "instance": "/v1/orders/SO-20261113-XXXXXXXX/status",
 "correlationId": "4f1c2a9e-…", "errors": []}`));
c.push(TABLE(["Status", "When", "Consumer action"], [
  ["400", "Invalid sort or malformed parameter", "Fix the request"],
  ["401", "Missing or invalid subscription key, API key or token", "Authenticate"],
  ["403", "Customer scope violation (Experience views)", "Request only your own orders"],
  ["404", "Order, group, document or PO not found", "Check the identifier"],
  ["405", "Non-GET call through the read-only Order Status product", "Use the correct product"],
  ["422", "Parameter fails validation (e.g. pageSize > 200)", "Fix the parameter"],
  ["429", "Rate limit (600/min per key) or daily quota exceeded", "Back off; honour x-ratelimit-remaining"],
  ["500 / 502", "Unexpected error / backend unavailable", "Retry with exponential backoff; report correlationId"],
], [1000, 4560, 3800]));

// 4.8 security
c.push(H2("4.8 Security"));
c.push(B("**Gateway authentication:** APIM requires a subscription key on every product. For production, add Entra ID token validation (validate-jwt policy); the services already support Entra tokens (TFSO_AUTH_MODE=entra). Partners and the portal receive their own products and keys."));
c.push(B("**Read-only product:** the **Order Status API** product allows only GET and OPTIONS (other methods return 405) and enforces a quota of 100,000 calls per day per subscription."));
c.push(B("**Rate limiting:** 600 calls per minute per subscription key (or caller IP), with an x-ratelimit-remaining header."));
c.push(B("**Backend trust:** APIM injects the backend x-api-key from a Key Vault named value. Container Apps accept only that key, and services authenticate to Azure resources with a managed identity (no passwords in code)."));
c.push(B("**Customer scoping and data minimisation:** Experience views return customer-safe fields only (no ROI, internal exceptions or other customers' data). Contact details are personal data and are not part of the status view."));
c.push(B("**Transport and secrets:** TLS 1.2+, secrets in Key Vault, CORS restricted to the console origin, audit via APIM analytics and App Insights."));

// 4.9 performance
c.push(H2("4.9 Performance and scalability"));
c.push(B("**Projection columns with indexes:** status, status_category, customer_id, customer_po (plus a composite index with customer), erp_sales_order_id, received_at, region, ship_from_warehouse and account_scope serve the search filters without parsing JSON."));
c.push(B("**Single-order queries** are primary-key reads of one document plus the latest history row. History is indexed by (order_id, sequence)."));
c.push(B("**Horizontal scale:** the Process API runs 1–3 replicas in dev/test and 2–10 in production on Azure Container Apps; the Lakebase instance scales its compute capacity and can add readable secondaries for heavy read traffic; analytics query Delta tables, not the operational store."));
c.push(B("**Guidance for consumers:** use ETag/If-None-Match for polling, use webhooks or the event stream instead of tight polling loops, and keep pageSize ≤ 50 when requesting full documents."));

// 4.10 availability
c.push(H2("4.10 Availability and resilience"));
c.push(B("Queries do not depend on D365, WMS or TMS being available (read model)."));
c.push(B("Multiple replicas behind Container Apps ingress with health probes on /health; zone-redundant options in production."));
c.push(B("Messaging is at-least-once with idempotent consumers; outbox retries with exponential backoff preserve per-order ordering."));
c.push(B("Lakebase high availability and point-in-time restore; targets RPO 15 minutes, RTO 4 hours (confirm against Lakebase SLAs in the chosen region)."));

// 4.11 observability
c.push(H2("4.11 Observability"));
c.push(B("Structured JSON logs with correlation id for every request; Azure Monitor OpenTelemetry to Application Insights."));
c.push(B("APIM analytics per product and subscription (calls, latency, errors, throttling)."));
c.push(B("Health endpoints report database, outbox backlog (pending or retrying) and the event-stream configuration."));
c.push(B("Suggested alerts: p95 latency > 300 ms for 10 minutes; 5xx rate > 1%; outbox retrying > 0 for 5 minutes; data freshness > 60 s."));

// 4.12 versioning
c.push(H2("4.12 Versioning and compatibility"));
c.push(P("The URL carries the major API version (v1). Within v1, changes are additive only: new fields, operations and enumeration values. CDM 1.1.0 added `fulfillment` and the `ORDER_SOURCED` history event without breaking v1.0 consumers. Breaking changes ship as v2 alongside v1, with notice to the consumers registered in the data marketplace and a deprecation window of at least 6 months."));

// 4.13 testing
c.push(H2("4.13 Testing and verification"));
c.push(TABLE(["Level", "Coverage"], [
  ["Automated tests (pytest)", "31 tests running the Experience, Process and System APIs in-process: lifecycle end to end, EDI multi-PO groups, exceptions with optimistic concurrency, credit hold, backorders and partial shipment, idempotent and out-of-order events, scope filters, OTIF and at-risk, marketplace"],
  ["Smoke test", "26 checks through the gateway or APIM after every deployment (portal → D365 → ship → invoice, EDI 997/855/856/810, exception → correction, Order Status API reads and KPIs)"],
  ["Contract", "JSON Schema 2020-12 files generated from the CDM model (cdm/*.schema.json); every published order is validated against the model"],
  ["Performance (recommended)", "Load test of Q1/Q3 at 200 rps against a production-sized dataset before go-live"],
], [2600, 6760]));
c.push(...FIG("d2.png", 624, 244, "GetOrderStatus request flow through API Management to the Process API and Databricks Lakebase"));

// ---------- 5 CDM
c.push(H1("5. Canonical Data Model (CDM)"));
c.push(H2("5.1 Purpose and principles"));
c.push(P("The CDM is the published contract of the Sales Order data product: one structure for every order whatever the channel. GetOrderStatus returns it (Q2), derives the status view from it (Q1) and publishes it in events."));
c.push(B("**Single source of truth:** a typed Python (Pydantic) model in `src/tfso/common/cdm.py`; JSON Schema 2020-12 files are generated from it into `cdm/*.schema.json`."));
c.push(B("**Standards-aligned:** ANSI X12 4010 (850/855/856/810/997), GS1 identifiers (GTIN, GLN, SSCC), OAGIS SalesOrder concepts, D365 F&O data entities (SalesOrderHeadersV2, SalesOrderLines), FSMA 204 traceability (lots, Traceability Lot Codes)."));
c.push(B("**Lifecycle-complete:** quantities, shipments, invoices, acknowledgements, data quality, exceptions, ROI tags and the full order history live in one aggregate."));
c.push(B("**Versioned:** `schemaVersion` on every document (current 1.1.0); semantic versioning with additive minor releases."));
c.push(...FIG("d3.png", 624, 340, "CDM 1.1.0: the SalesOrder aggregate and its components"));
c.push(H2("5.2 Entity catalogue"));
c.push(TABLE(["Entity", "Cardinality", "Purpose"], [
  ["SalesOrder", "root", "Header, status, source, dates, commercial and shipping terms, totals, audit"],
  ["SourceInfo / SourceDocument", "1", "Channel, received time, raw document reference, X12 control numbers, extraction confidence"],
  ["PurchaseOrderReference", "1", "Customer PO number, date, release, contract, department, promotion"],
  ["ErpReference", "0..1", "D365 company, sales order number and status, accounts, site, warehouse"],
  ["Parties / Party", "1 / 2..8", "Sold-to, ship-to, bill-to, payer, buyer, supplier, ship-from, carrier"],
  ["OrderLine / ItemReference / LineQuantities / Price", "1..n", "What was ordered, its identifiers, prices and running quantities"],
  ["Fulfillment / SourcingDecision", "0..1", "Regional network: fulfilling region and site, account scope, sourcing rule and candidates (added in 1.1.0)"],
  ["Shipment / ShipmentLine / LotAllocation", "0..n", "Physical shipments with carrier, tracking, POD and FSMA lots"],
  ["Invoice / InvoiceLine", "0..n", "Invoices posted in D365"],
  ["Acknowledgment", "0..n", "Documents and notifications sent to the customer (997, 855, 856, 810, e-mail, portal, webhook)"],
  ["DataQuality / RuleResult / OrderException", "1 / 0..n", "Results of DQ-001…DQ-011 and the exception lifecycle"],
  ["RoiTags", "1", "Touchless flag, manual touches, stage timestamps, cycle times, chargeback risk"],
  ["OrderHistoryEntry", "1..n", "Immutable lifecycle events (the order history)"],
  ["RelatedOrder / OrderGroup", "0..n", "Links between orders and the group-level lifecycle"],
], [3200, 1400, 4760], { size: 17 }));

c.push(H2("5.3 Element definitions"));
c.push(H3("SalesOrder (header)"));
c.push(TABLE(["Element", "Type", "Req.", "Definition / source"], [
  ["schemaVersion", "string", "Y", "Contract version, e.g. 1.1.0"],
  ["orderId", "string", "Y", "Data product id SO-YYYYMMDD-XXXXXXXX (8 hex characters)"],
  ["orderGroupId", "string", "Y", "Group id OG-YYYYMMDD-XXXXXXXX (one interchange or e-mail, splits)"],
  ["version", "integer", "Y", "Increments on every change; used as the ETag"],
  ["orderType", "enum", "Y", "STANDARD, RUSH, STANDING, BLANKET_RELEASE, DROP_SHIP, SAMPLE, REPLACEMENT (X12 BEG02)"],
  ["purpose", "enum", "Y", "ORIGINAL, CHANGE, CANCELLATION, REPLACE, CONFIRMATION (X12 BEG01)"],
  ["status / statusCategory", "enum", "Y", "Current status and category (§4.5)"],
  ["statusReason / statusUpdatedAt", "string / date-time", "N / Y", "Reason for hold or cancellation; time of last status change"],
  ["source", "SourceInfo", "Y", "channel, channelDetail, receivedAt, sourceDocument (documentType, senderId, interchange, group and transaction-set control numbers), extraction (method, confidence)"],
  ["purchaseOrder", "object", "Y", "poNumber (X12 BEG03), poDate, releaseNumber, contractNumber, department, promotionDealNumber"],
  ["erp", "ErpReference", "N", "system D365FO, company (dataAreaId), salesOrderId (SalesOrderNumber), salesOrderStatus, customerAccount, invoiceAccount, siteId, warehouseId, deliveryMode"],
  ["parties", "Parties", "Y", "soldTo and shipTo required; others optional"],
  ["dates", "OrderDates", "Y", "orderDate, requestedDeliveryDate (DTM*002), requestedShipDate (DTM*010 or derived from transit), promisedShipDate, promisedDeliveryDate, cancelByDate, deliveryWindow"],
  ["commercial", "Commercial", "Y", "currency (ISO 4217), paymentTerms, priceAgreementId, customerPriceGroup, incoterms, fobPoint"],
  ["shipping", "ShippingInstructions", "Y", "deliveryMode, transportResponsibility, carrierScac, shipFromSite, shipFromWarehouse, temperature zone and range, appointmentRequired, specialInstructions"],
  ["fulfillment", "Fulfillment", "N", "Regional fulfilment and sourcing decision (1.1.0, see below)"],
  ["lines", "OrderLine[]", "Y", "At least one line"],
  ["allowancesCharges, totals", "array, Totals", "N, Y", "Header allowances and charges (X12 SAC); lineCount, totalQuantity, subtotal, allowances, charges, tax, grandTotal, currency, totalWeightLb"],
  ["shipments, invoices, acknowledgments", "arrays", "N", "Fulfilment facts and customer communications"],
  ["exceptions, dataQuality", "array, object", "Y", "Validation results and exception lifecycle"],
  ["roi", "RoiTags", "Y", "ROI and service-level tags (deck slide 4)"],
  ["relatedOrders, orderHistory", "arrays", "N", "Links to other orders; complete lifecycle history"],
  ["audit, extensions", "object, map", "Y, N", "Created/updated by and at, contract version, lineage steps; namespaced non-contract attributes"],
], [2400, 1600, 700, 4660], { size: 17 }));

c.push(H3("Party"));
c.push(TABLE(["Element", "Type", "Definition / source"], [
  ["role", "enum", "SOLD_TO, SHIP_TO, BILL_TO, PAYER, BUYER, SUPPLIER, CARRIER, SHIP_FROM"],
  ["partyId / customerId", "string", "Data product key (customer or ship-to id); owning customer for locations"],
  ["erpAccount", "string", "D365 customer account or delivery address location id"],
  ["name, address, contacts[]", "string, object, array", "Name; line1, line2, city, stateProvince, postalCode, country; contacts (role, name, email, phone: personal data)"],
  ["gln / duns / ediLocationCode", "string", "GS1 GLN (N104 qualifier UL), DUNS (qualifier 1), buyer location code (qualifier 92)"],
  ["receivingHours, deliveryDays[]", "string, array", "Receiving window and accepted delivery days of a ship-to"],
], [2600, 2000, 4760], { size: 17 }));

c.push(H3("OrderLine, ItemReference, LineQuantities, Price"));
c.push(TABLE(["Element", "Type", "Definition / source"], [
  ["lineNumber / buyerLineNumber / erpLineId", "integer / string", "Data product line; customer line (PO101); D365 line reference"],
  ["item.itemNumber", "string", "Taylor Farms item (D365 ItemNumber), resolved from buyer item, GTIN or UPC"],
  ["item.buyerItemNumber / vendorItemNumber / gtin / upc", "string", "PO1 qualifiers IN, VN, UK/EN (GTIN-14), UP (GTIN-12)"],
  ["item.description, packSize, brand, temperatureZone, shelfLifeDays, caseWeightLb, commodityCode", "mixed", "Enriched from the item master"],
  ["quantity", "Quantity", "value (decimal string) and uom (CA, EA, LB …) as ordered"],
  ["quantities", "LineQuantities", "ordered, confirmed, backordered, allocated, picked, shipped, delivered, invoiced, cancelled: the running lifecycle of the line"],
  ["unitPrice", "Price", "amount, currency, basisUom, source (CUSTOMER_AGREEMENT, LIST, PURCHASE_ORDER, MANUAL_OVERRIDE), agreementId, agreementPrice, listPrice"],
  ["lineAmount, allowancesCharges[], taxes[]", "decimal, arrays", "Extended amount and line-level adjustments"],
  ["requestedDeliveryDate, requestedShipDate, promisedShipDate", "date", "Line-level dates (default to header)"],
  ["status", "enum", "Line status (§4.5)"],
  ["lots[]", "LotAllocation[]", "lotNumber, quantity, traceabilityLotCode (FSMA 204), packDate, harvestDate, bestByDate, countryOfOrigin"],
  ["substitutionAllowed, minRemainingShelfLifeDays, notes[]", "mixed", "Fulfilment constraints and notes"],
], [3600, 1500, 4260], { size: 17 }));

c.push(H3("Fulfillment and SourcingDecision (added in 1.1.0)"));
c.push(TABLE(["Element", "Type", "Definition"], [
  ["regionId / regionName", "string", "Fulfilling operating region. Before sourcing, holds the demand region (the ship-to's region)"],
  ["customerRegionId", "string", "Region of the ship-to (where the demand is)"],
  ["siteId / warehouse / siteName", "string", "Processing site and D365 warehouse that ships the order"],
  ["accountScope", "enum", "NATIONAL (customer served from more than one region) or REGIONAL"],
  ["crossRegion", "boolean", "Shipped from outside the ship-to's region"],
  ["sourcing.rule", "enum", "HOME_SITE, ALTERNATE_SITE_IN_REGION, CROSS_REGION, BEST_AVAILABLE"],
  ["sourcing.reason / decidedAt", "string / date-time", "Plain-language explanation and decision time"],
  ["sourcing.candidates[]", "array", "Every site evaluated: warehouse, siteId, regionId, fillRate (0–1), home, chosen"],
], [2800, 1600, 4960], { size: 17 }));

c.push(H3("Shipment, Invoice, Acknowledgment"));
c.push(TABLE(["Entity", "Key elements"], [
  ["Shipment", "shipmentId, asnNumber (856 BSN02), erpPackingSlipId, carrierScac, carrierName, trackingNumber, billOfLading, proNumber, trailerNumber, sealNumber, shipFromWarehouse, shippedAt, estimatedDeliveryAt, deliveredAt, status (PLANNED, IN_TRANSIT, DELIVERED, EXCEPTION), sscc[], proofOfDelivery (receivedBy, receivedAt, exceptions[]), lines[] with lots"],
  ["Invoice", "invoiceId, invoiceDate, dueDate, amount, taxAmount, currency, status (POSTED, SENT, PAID, CREDITED), lines[] (lineNumber, quantity, unitPrice, amount)"],
  ["Acknowledgment", "type (FUNCTIONAL_ACK_997, PO_ACK_855, SHIP_NOTICE_856, INVOICE_810, EMAIL_CONFIRMATION, PORTAL_NOTIFICATION, WEBHOOK), documentId, controlNumber, status (ACCEPTED, ACCEPTED_WITH_CHANGES, REJECTED, SENT), channel, recipient, sentAt"],
], [1800, 7560], { size: 17 }));

c.push(H3("DataQuality, OrderException, RoiTags"));
c.push(TABLE(["Entity", "Key elements"], [
  ["DataQuality", "ruleSetVersion, validatedAt, attempt, passed, firstPass, score (0–1), results[] (ruleId DQ-001…DQ-011, name, passed, severity, message, lineNumber, field)"],
  ["OrderException", "exceptionId, code (e.g. PRICE_MISMATCH, ITEM_NOT_FOUND, CREDIT_HOLD, ERP_REJECTED), ruleId, severity, message, lineNumber, field, detectedAt, detectedBy, state (OPEN, RESOLVED, OVERRIDDEN), resolvedAt, resolvedBy, resolutionNote"],
  ["RoiTags", "sourceChannel, touchless, touchCount, manualTouches[] (at, actor, action, minutes), exceptionReasonCodes[], stageTimestamps (received … invoiced, closed, cancelled), acknowledgmentTimestamps (functionalAck997, poAck855, shipNotice856, invoice810), cycleTimes (receiptToErpMinutes, receiptToShipHours, receiptToInvoiceHours, shipToAsnMinutes), chargebackRisk[], registeredConsumers[]"],
], [1800, 7560], { size: 17 }));

c.push(H3("OrderHistoryEntry: how the lifecycle is recorded"));
c.push(TABLE(["Element", "Definition"], [
  ["eventId / sequence", "Unique id; monotonic sequence per order (1, 2, 3 …) giving a gap-free order of events"],
  ["eventType", "ORDER_RECEIVED, ORDER_INGESTED, VALIDATION_PASSED/FAILED, ORDER_ENRICHED, ORDER_SOURCED, ORDER_PUBLISHED, SUBMITTED_TO_ERP, ERP_ACCEPTED/REJECTED, CREDIT_HOLD_PLACED, HOLD_PLACED/RELEASED, INVENTORY_RESERVED, LINE_BACKORDERED, RELEASED_TO_WAREHOUSE, PICKED, PACKED, SHIPPED, DELIVERED, INVOICED, CLOSED, CANCELLED, REJECTED, ORDER_CORRECTED, ORDER_RESUBMITTED, DOCUMENT_SENT, NOTIFICATION_SENT, LATE_EVENT_IGNORED, ORDER_LINKED, ORDER_SPLIT"],
  ["occurredAt / recordedAt / elapsedSincePreviousSeconds", "Time in the source system, time recorded by the data product, and time since the previous event"],
  ["fromStatus → toStatus, statusCategory", "The transition (equal when the event does not change status)"],
  ["tier / sourceSystem / actor", "TIER1, TIER2 or TIER3; EDI-GATEWAY, PROCESS-API, D365, WMS, TMS, NOTIFICATION …; who acted (type SYSTEM, USER, CUSTOMER or PARTNER; id; name)"],
  ["correlationId / causationId / externalEventId", "Request trace; the message that caused it; the source system's event id (idempotency: duplicates are ignored)"],
  ["reasonCode / message", "Why (e.g. PRICE_MISMATCH, HOME_SITE, OUT_OF_ORDER) and a readable description"],
  ["changes[] / lineChanges[]", "JSON-Patch-style diff of the canonical order (op, path, value, fromValue) and line-level quantity or status changes"],
  ["documentRef", "Document produced or received (e.g. the 855)"],
], [3600, 5760], { size: 17 }));
c.push(P("History is append-only and written in the same transaction as the order change, so the order and its history can never disagree. For a **group of orders**, the group history merges the entries of every order by time. This is how a multi-PO EDI interchange or a split order is followed end to end."));

c.push(H2("5.4 Mapping to source standards"));
c.push(CAP("X12 850 → CDM (selected)"));
c.push(TABLE(["X12 850", "CDM element"], [
  ["ISA06 / ISA13 / GS06 / ST02", "source.sourceDocument.senderId / interchangeControlNumber / groupControlNumber / transactionSetControlNumber"],
  ["BEG01 / BEG02 / BEG03 / BEG05", "purpose / orderType / purchaseOrder.poNumber / purchaseOrder.poDate"],
  ["DTM*002 / DTM*010 / DTM*001", "dates.requestedDeliveryDate / requestedShipDate / cancelByDate"],
  ["N1*ST (qualifier 92 or UL)", "parties.shipTo.ediLocationCode or gln → resolved to shipTo.partyId"],
  ["PO1 quantity, unit, price; IN, VN, UK, UP", "lines[].quantity, unitPrice; item.buyerItemNumber, vendorItemNumber, gtin, upc"],
  ["SAC", "allowancesCharges[] (header or line)"],
], [3600, 5760]));
c.push(CAP("CDM → D365 F&O (SalesOrderHeadersV2 / SalesOrderLines)"));
c.push(TABLE(["CDM element", "D365 field"], [
  ["parties.soldTo.erpAccount / billTo.erpAccount", "OrderingCustomerAccountNumber / InvoiceCustomerAccountNumber"],
  ["purchaseOrder.poNumber / orderId", "CustomerRequisitionNumber / CustomersOrderReference"],
  ["dates.requestedDeliveryDate / requestedShipDate", "RequestedReceiptDate / RequestedShippingDate"],
  ["shipping.shipFromSite / shipFromWarehouse / deliveryMode", "DefaultShippingSiteId / DefaultShippingWarehouseId / DeliveryModeCode"],
  ["lines[].item.itemNumber, quantity, unitPrice", "ItemNumber, OrderedSalesQuantity + SalesUnitSymbol, SalesPrice"],
], [4400, 4960]));

c.push(H2("5.5 Versioning policy"));
c.push(B("**Patch (1.1.x):** documentation and description changes; no structural change."));
c.push(B("**Minor (1.x.0):** additive only: new optional elements, enumeration values or history event types. Example: 1.1.0 added `fulfillment` and `ORDER_SOURCED`."));
c.push(B("**Major (x.0.0):** removal, renaming, type or meaning change. Published alongside the previous major with at least 6 months' notice to registered consumers."));
c.push(B("Consumers must ignore unknown fields and tolerate unknown enumeration values."));

c.push(H2("5.6 Example (abridged)"));
c.push(...CODE(`{
  "schemaVersion": "1.1.0", "orderId": "SO-20261113-63B9D3E7", "orderGroupId": "OG-20261113-D69EA874",
  "version": 9, "orderType": "STANDARD", "purpose": "ORIGINAL",
  "status": "INVOICED", "statusCategory": "INVOICED",
  "source": {"channel": "EDI", "receivedAt": "2026-11-13T17:15:27Z",
             "sourceDocument": {"documentType": "X12_850", "senderId": "FRESHMART",
                                "interchangeControlNumber": "794590128", "transactionSetControlNumber": "0001"}},
  "purchaseOrder": {"poNumber": "FM590128A", "poDate": "2026-11-13"},
  "erp": {"system": "D365FO", "company": "usmf", "salesOrderId": "SO-00100002", "warehouseId": "WH-SAL"},
  "parties": {"soldTo": {"role": "SOLD_TO", "partyId": "C-1001", "name": "FreshMart Grocery", "erpAccount": "US-1001"},
              "shipTo": {"role": "SHIP_TO", "partyId": "ST-1001-01", "name": "FreshMart DC 12", "ediLocationCode": "FM-DC12"}},
  "fulfillment": {"regionId": "WEST", "regionName": "West", "warehouse": "WH-SAL", "accountScope": "NATIONAL",
                  "crossRegion": false, "sourcing": {"rule": "HOME_SITE", "reason": "Salinas Processing can fill the order"}},
  "lines": [{"lineNumber": 1, "item": {"itemNumber": "TF-10001", "buyerItemNumber": "FM-55012", "gtin": "10614141100011"},
             "quantity": {"value": "40", "uom": "CA"},
             "quantities": {"ordered": "40", "confirmed": "40", "shipped": "40", "delivered": "40", "invoiced": "40"},
             "unitPrice": {"amount": "24.50", "source": "CUSTOMER_AGREEMENT", "agreementId": "TA-FM-2026"},
             "lots": [{"lotNumber": "L26317001", "quantity": "40", "traceabilityLotCode": "0614141L26317001"}]}],
  "totals": {"grandTotal": "980.00", "currency": "USD"},
  "roi": {"touchless": true, "touchCount": 0, "cycleTimes": {"receiptToErpMinutes": 0.01}},
  "orderHistory": [ "… 18 entries: ORDER_RECEIVED … DOCUMENT_SENT (810) …" ]
}`));

// ---------- 6 Azure
c.push(H1("6. Azure reference architecture"));
c.push(H2("6.1 Overview"));
c.push(P("The solution runs on Azure Integration Services and Azure Container Apps, with Azure Databricks as the data hub (deck slide 11). API Management is the only public entry point for APIs; the Order Operations Console is a static website that calls APIM."));
c.push(P("The operational data store is **Databricks Lakebase**: a managed, Postgres-compatible OLTP database that runs inside the Azure Databricks workspace. GetOrderStatus therefore reads from the same Databricks platform that hosts the Delta lakehouse, and both are governed by Unity Catalog."));
c.push(...FIG("d4.png", 624, 374, "Azure reference architecture"));
c.push(H2("6.2 Components"));
c.push(TABLE(["Azure service", "Role (GetOrderStatus in bold)", "Dev / test", "Production"], [
  ["API Management", "**Front door: auth, products, quotas, rate limit, CORS, correlation, backend key**", "Developer (or Consumption / BasicV2)", "Premium (zones, VNet) or StandardV2"],
  ["Azure Container Apps", "**Process API hosts GetOrderStatus**; Experience and System APIs", "0.5 vCPU / 1 GiB, 1–3 replicas", "2–10 replicas, zone-redundant environment"],
  ["Databricks Lakebase (in the Azure Databricks workspace)", "**Read model and transactional store**: canonical orders (JSONB), projections, history, documents, outbox; Postgres-compatible; governed by Unity Catalog", "Smallest capacity instance", "Larger capacity with high availability and readable secondaries; size after load test"],
  ["Azure Service Bus (Standard)", "Commands and status events; sales-order-events for push; DLQ", "Standard", "Premium (geo-DR) recommended"],
  ["Azure Event Hubs", "order-status-stream for Databricks", "Standard, 1 TU", "Standard/Premium, auto-inflate"],
  ["Azure Key Vault (RBAC)", "Backend API key, DB credentials, D365 secret", "Standard", "Standard, private endpoint"],
  ["Managed identities", "Services to Azure resources; deployment identities", "User-assigned", "User-assigned"],
  ["Azure Container Registry", "Service image", "Basic", "Premium (geo-replication)"],
  ["Azure Databricks workspace (Premium) + ADLS Gen2", "Hosts Lakebase; bronze, silver and gold Delta tables; KPIs and regional marts; Delta Sharing; Unity Catalog", "Premium workspace, Standard LRS", "Premium, Private Link, ZRS/GRS storage"],
  ["Storage static website", "Order Operations Console", "Standard LRS", "Behind Front Door / App Gateway WAF"],
  ["Azure AI Document Intelligence", "E-mailed PDF orders (intake)", "S0", "S0"],
  ["Azure Monitor, Application Insights, Log Analytics", "**Tracing, APIM analytics, alerts**", "30-day retention", "90-day retention, alerts"],
  ["Microsoft Entra ID", "Users, tokens, GitHub OIDC for deployment", "", ""],
], [2500, 3100, 1880, 1880], { size: 16 }));
c.push(H2("6.3 Why Databricks Lakebase"));
c.push(P("GetOrderStatus needs an operational store: millisecond point reads, indexed filters, and transactions that write the order, its history entry and the outbox message together. Lakebase provides this as a Postgres-compatible database managed by Databricks, so the data product lives on one governed platform."));
c.push(TABLE(["Requirement", "How Lakebase meets it"], [
  ["p95 < 300 ms single-order reads; indexed search", "Row-store OLTP engine with B-tree indexes on the projection columns; primary-key reads of the JSONB order document"],
  ["One transaction for order + history + outbox", "Standard PostgreSQL transactions, so the transactional outbox and idempotent inbox work unchanged"],
  ["Flexible canonical document", "JSONB columns for the CDM document, with relational projection columns for filters"],
  ["Governance and lineage", "Lakebase databases are registered in Unity Catalog alongside the Delta tables: one place for permissions, lineage and discovery (Purview integration through Unity Catalog)"],
  ["Analytics without loading the API", "Order events still stream to bronze Delta tables; silver and gold are built in Databricks. Gold results such as regional performance can be synced back into Lakebase tables if the API needs to serve them"],
  ["Scale and availability", "Capacity scales independently of the Container Apps; high availability and readable secondaries in production; point-in-time restore"],
], [3000, 6360]));
c.push(H3("Impact on the current implementation"));
c.push(B("The repository's services and infrastructure currently use Azure Database for PostgreSQL Flexible Server. Because Lakebase speaks the PostgreSQL protocol, the services' data access (SQLAlchemy with the psycopg driver, JSONB, transactions) does not change."));
c.push(B("Migration steps:"));
c.push(B("Provision a Lakebase instance and databases in the Databricks workspace (Bicep plus the Databricks API).", 1));
c.push(B("Point `TFSO_DB_URL` at the Lakebase endpoint.", 1));
c.push(B("Authenticate with a Databricks service principal: short-lived OAuth tokens, refreshed by the connection layer, or a native Postgres role with the password in Key Vault.", 1));
c.push(B("Remove the PostgreSQL Flexible Server.", 1));
c.push(B("This document describes the target state; the migration is scheduled after the region and SLA check (R-4, D-5)."));
c.push(H2("6.4 GetOrderStatus request path in Azure")); NEWLIST();
c.push(N("A consumer calls `https://<apim>.azure-api.net/process/v1/orders/{id}/status` with a subscription key (and, in production, an Entra ID token)."));
c.push(N("APIM validates the key and token, applies the product policy (read-only, quota) and the rate limit, sets x-correlation-id, and injects the backend x-api-key from Key Vault."));
c.push(N("APIM forwards over TLS to the Process API Container App, which validates the key and runs the query."));
c.push(N("The Process API reads Databricks Lakebase over the PostgreSQL protocol (primary key or indexed projection) and returns the view with an ETag."));
c.push(N("APIM logs the call to Application Insights; the correlation id ties the gateway, service and database spans together."));
c.push(H2("6.5 Event path: how status reaches the read model and subscribers")); NEWLIST();
c.push(N("D365 business events, WMS ship confirmations and TMS proof of delivery reach the System API (webhooks or Logic Apps)."));
c.push(N("The System API publishes StatusEvents to Service Bus `system-status-events`."));
c.push(N("The Process API applies each event idempotently, updates the canonical order and history, and writes an outbox message in the same transaction."));
c.push(N("The outbox publishes `OrderStatusChanged` to Service Bus `sales-order-events` (lakehouse, CRM, webhooks) and Event Hubs `order-status-stream` (Databricks)."));
c.push(H2("6.6 Security and identity"));
c.push(B("Entra ID for users, APIM subscriptions for applications, managed identities between Azure services; no secrets in code or configuration files."));
c.push(B("RBAC on data planes: Key Vault Secrets User, Service Bus Data Sender and Receiver, Event Hubs Data Sender, Storage Blob Data Contributor, AcrPull, each granted to the workload identity only."));
c.push(B("Deployment uses GitHub OIDC federation (no stored client secret) or a deployment identity with Owner on the resource group only, plus a custom role limited to registering the required resource providers."));
c.push(B("Production hardening: Private Link for the Databricks workspace and Lakebase, private endpoints for Key Vault, Service Bus and storage; APIM in internal VNet mode behind Application Gateway WAF; Defender for Cloud."));
c.push(H2("6.7 Networking"));
c.push(TABLE(["Environment", "Network design"], [
  ["Dev / test", "Public endpoints restricted by keys and RBAC; Container Apps ingress reachable only with the APIM-injected backend key; Lakebase access restricted to the Container Apps outbound IPs and the deployment identity"],
  ["Production", "Hub-spoke VNet; Container Apps environment with internal ingress; private endpoints for data services; APIM Premium in internal mode behind Application Gateway WAF; egress through Azure Firewall"],
], [2000, 7360]));
c.push(H2("6.8 Resilience, DR and operations"));
c.push(B("Zone redundancy for Container Apps and APIM Premium, and Lakebase high availability, in production."));
c.push(B("Lakebase point-in-time restore (targets RPO 15 min, RTO 4 h; confirm cross-region options); Service Bus geo-DR pairing; replay from the outbox or bronze Delta tables to rebuild consumers or the operational store."));
c.push(B("Alerts on latency, error rate, outbox backlog, dead-letter queues and data freshness."));
c.push(H2("6.9 Deployment"));
c.push(P("All infrastructure is Bicep (`infra/`). `deploy.sh azure` provisions the platform, builds the image in ACR, rolls out the Container Apps, configures APIM, publishes the console and runs the smoke test. One-click options:"));
c.push(B("GitHub Actions workflow **Deploy to Azure** (OIDC, approvals per environment)."));
c.push(B("Azure portal Template Spec."));
c.push(B("**Deploy to Azure** button backed by `infra/azuredeploy.json`."));
c.push(P("See [R6] for details."));

// ---------- 7 Risks / open items
c.push(H1("7. Risks, assumptions and open decisions"));
c.push(TABLE(["ID", "Item", "Type", "Mitigation / recommendation"], [
  ["R-1", "Consumers poll aggressively and drive cost", "Risk", "ETag/304, quotas, webhooks and the event stream; publish polling guidance"],
  ["R-2", "Status lags when an outbox or consumer backs up", "Risk", "Health and freshness alerts; dead-letter monitoring; replay"],
  ["R-3", "Customer data exposure through internal routes", "Risk", "Customers and partners use Experience APIs only; read-only internal product; token claims for customer scoping"],
  ["A-1", "Regions and sites in the seed data are illustrative", "Assumption", "Replace with the actual facility network and D365 warehouses before pilot"],
  ["A-2", "D365 business events are available for status changes", "Assumption", "Confirm event catalogue with the D365 team; WMS/TMS webhooks as fallback"],
  ["R-4", "Databricks Lakebase availability, SLA or limits in the target Azure region do not meet the API targets", "Risk", "Confirm with Databricks before build; load-test Q1/Q3; Azure Database for PostgreSQL is a drop-in fallback (same protocol, current implementation)"],
  ["D-1", "Definition of \"on time\" (requested date vs appointment window)", "Decision", "Start with the requested delivery date"],
  ["D-2", "Production authentication for partners (subscription key only vs key + OAuth)", "Decision", "Key + Entra ID client credentials for production partners"],
  ["D-3", "Webhook retry policy and auto-disable thresholds", "Decision", "Retry with backoff for 24 h, then disable and notify"],
  ["D-4", "Data retention for history (7 years) and documents", "Decision", "Confirm with legal and finance"],
  ["D-5", "Adopt Databricks Lakebase as the production operational store", "Decision", "Approve after the region/SLA check and load test (R-4); migrate the current PostgreSQL implementation by configuration"],
], [700, 3300, 1300, 4060], { size: 17 }));

// ---------- Appendix
c.push(H1("Appendix A. Operation summary"));
c.push(TABLE(["Method", "Path (Process API unless noted)", "Purpose"], [
  ["GET", "/v1/orders/{orderId}/status", "Status view (GetOrderStatus)"],
  ["GET", "/v1/orders/{orderId}", "Canonical order with history"],
  ["GET", "/v1/orders", "Search, filter, page, sort"],
  ["GET", "/v1/orders/by-po/{customerPo}", "Find by customer PO"],
  ["GET", "/v1/orders/{orderId}/history", "Lifecycle history"],
  ["GET", "/v1/orders/{orderId}/related", "Related orders"],
  ["GET", "/v1/order-groups/{orderGroupId}/history", "Group lifecycle"],
  ["GET", "/v1/order-events", "Cross-order event feed"],
  ["GET", "/v1/orders/{orderId}/documents[/{documentId}]", "Documents"],
  ["GET", "/v1/exceptions", "Exception queue"],
  ["GET", "/v1/orders/at-risk", "Orders at risk"],
  ["GET", "/v1/status/summary", "Counts by status, category, channel, region, site"],
  ["GET", "/v1/metrics/kpis", "Service KPIs"],
  ["GET", "/v1/reference/statuses", "State machine"],
  ["GET/POST/DELETE", "/v1/subscriptions[/{id}]", "Webhooks"],
  ["GET", "/exp/portal/v1/orders/{orderId}", "Customer status view"],
  ["GET", "/exp/portal/v1/customers/{customerId}/orders", "Customer order list"],
  ["GET", "/exp/partner/v1/orders/{orderId}/status", "Partner status view"],
  ["GET", "/exp/edi/v1/orders/{orderId}/documents", "EDI documents"],
], [1600, 4460, 3300], { size: 17 }));

/* ------------------------------------------------------------------ document */
const doc = new Document({
  creator: "Sales Order data product team",
  title: "GetOrderStatus API - Solution Design Document",
  description: "Design of the Taylor Farms Order Status API, three-tier architecture, CDM and Azure reference architecture",
  features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: FONT, color: GREEN }, paragraph: { spacing: { before: 240, after: 160 }, outlineLevel: 0, keepNext: true } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: FONT, color: "1F2D24" }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1, keepNext: true } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 22, bold: true, font: FONT, color: GREEN }, paragraph: { spacing: { before: 220, after: 100 }, outlineLevel: 2, keepNext: true } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1080, hanging: 270 } } } }] },
      { reference: "numbers", levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
    ],
  },
  sections: [{
    properties: { page: { size: { width: 12240, height: 15840 }, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } }, titlePage: true },
    headers: {
      default: new Header({ children: [new Paragraph({
        border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "C9D2CC", space: 4 } },
        tabStops: [{ type: TabStopType.RIGHT, position: CONTENT }],
        children: [new TextRun({ text: "GetOrderStatus API · Solution Design Document", size: 16, color: "4B5A51" }),
                   new TextRun({ text: "\tTaylor Farms · Internal", size: 16, color: "4B5A51" })] })] }),
      first: new Header({ children: [new Paragraph({ children: [] })] }),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
        new TextRun({ text: "Page ", size: 16, color: "4B5A51" }), new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "4B5A51" }),
        new TextRun({ text: " of ", size: 16, color: "4B5A51" }), new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: "4B5A51" }),
        new TextRun({ text: "   ·   Version 1.1 · 16 November 2026", size: 16, color: "4B5A51" })] })] }),
      first: new Footer({ children: [new Paragraph({ children: [] })] }),
    },
    children: c,
  }],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, buf.length, "bytes"); });
