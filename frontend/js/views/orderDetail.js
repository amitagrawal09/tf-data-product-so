import { api, items } from "../api.js";
import {
  html, raw, setHTML, $, $$, toast, errorBox, loading, fmtDate, fmtMoney, fmtNum, fmtDuration, humanize,
  statusBadge, sevBadge, json, x12, kv, pick, sleep, categoryOf,
} from "../ui.js";

const TABS = [["overview", "Overview"], ["lines", "Lines"], ["history", "History"], ["documents", "Documents"], ["related", "Related orders"], ["raw", "Canonical JSON"]];
const CORRECTABLE = ["EXCEPTION", "ERP_REJECTED", "ON_HOLD"];
const TERMINAL = ["CLOSED", "CANCELLED", "REJECTED"];
const SIM_STEPS = [["RELEASED", "Release"], ["PICKED", "Pick"], ["PACKED", "Pack"], ["SHIPPED", "Ship"], ["DELIVERED", "Deliver"], ["INVOICED", "Invoice"], ["CLOSED", "Close"]];

let state = { id: null, order: null, etag: null, statusView: null, tab: "overview" };
let pollToken = 0;

export async function mount(el, id, tab) {
  state = { id, order: null, etag: null, statusView: null, tab: TABS.some(t => t[0] === tab) ? tab : "overview" };
  setHTML(el, html`
    <div class="page-head">
      <div><div class="small muted"><a href="#/orders">Orders</a> /</div>
        <h1 class="mono" id="title">${id}</h1><div class="sub" id="subtitle"></div></div>
      <div class="row"><button class="btn" id="reload">Refresh</button></div>
    </div>
    <div class="card" id="stepperCard">${loading()}</div>
    <div class="grid grid-2 section-gap" id="topCards"></div>
    <div class="section-gap tabs" role="tablist">${TABS.map(([k, l]) => html`<button role="tab" data-tab="${k}" aria-selected="${k === state.tab}">${l}</button>`)}</div>
    <div id="tab"></div>`);
  $("#reload", el).addEventListener("click", () => load(el));
  $$(".tabs button", el).forEach(b => b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    $$(".tabs button", el).forEach(x => x.setAttribute("aria-selected", String(x === b)));
    history.replaceState(null, "", `#/orders/${encodeURIComponent(id)}/${state.tab}`);
    renderTab(el);
  }));
  await load(el);
}

export function unmount() { pollToken++; }

async function load(el) {
  try {
    const [o, s] = await Promise.all([
      api.process(`/v1/orders/${encodeURIComponent(state.id)}`, { withHeaders: true }),
      api.process(`/v1/orders/${encodeURIComponent(state.id)}/status`).catch(() => null),
    ]);
    state.order = normalizeOrder(o.data);
    state.etag = o.etag || (o.data && o.data.version != null ? String(o.data.version) : null);
    state.statusView = s;
  } catch (e) {
    setHTML($("#stepperCard", el), errorBox(e));
    return;
  }
  renderHeader(el);
  renderTab(el);
}

/** Poll the status endpoint after an async action until the status changes (or give up). */
async function pollAfterAction(el, prevStatus, expect) {
  const token = ++pollToken;
  for (let i = 0; i < 8; i++) {
    await sleep(i < 3 ? 600 : 1200);
    if (token !== pollToken) return;
    try {
      const s = await api.process(`/v1/orders/${encodeURIComponent(state.id)}/status`);
      if (s && s.status !== prevStatus && (!expect || s.status === expect || i > 4)) break;
    } catch { /* keep polling */ }
  }
  if (token === pollToken) await load(el);
}

const O = () => state.order || {};

/**
 * Flatten the canonical Sales Order (CDM v1: parties/purchaseOrder/dates/source/commercial) onto the
 * convenience fields this view renders. The canonical document stays intact (Canonical JSON tab).
 */
function normalizeOrder(d) {
  if (!d || typeof d !== "object") return d;
  const p = d.parties || {};
  const party = (x, idKey) => x ? { ...x, [idKey]: x[idKey] || x.partyId, address: x.address ? { ...x.address, state: x.address.state || x.address.stateProvince } : x.address } : x;
  return {
    ...d,
    soldTo: d.soldTo || party(p.soldTo, "customerId"),
    shipTo: d.shipTo || party(p.shipTo, "shipToId"),
    billTo: d.billTo || party(p.billTo, "customerId"),
    sourceChannel: d.sourceChannel || (d.source && d.source.channel),
    customerPo: d.customerPo || (d.purchaseOrder && d.purchaseOrder.poNumber),
    receivedAt: d.receivedAt || (d.source && d.source.receivedAt),
    requestedDeliveryDate: d.requestedDeliveryDate || (d.dates && d.dates.requestedDeliveryDate),
    requestedShipDate: d.requestedShipDate || (d.dates && d.dates.requestedShipDate),
    promisedDeliveryDate: d.promisedDeliveryDate || (d.dates && (d.dates.promisedDeliveryDate || d.dates.promisedShipDate)),
    currency: d.currency || (d.totals && d.totals.currency) || (d.commercial && d.commercial.currency),
    paymentTerms: d.paymentTerms || (d.commercial && d.commercial.paymentTerms),
    erpSalesOrderId: d.erpSalesOrderId || (d.erp && d.erp.salesOrderId),
    exceptions: (d.exceptions || []).map(x => ({ ...x, status: x.status || x.state })),
    lines: (d.lines || []).map(l => ({ ...l, lineStatus: l.lineStatus || l.status })),
  };
}
const erpId = o => pick(o, "erpSalesOrderId", "erp.salesOrderId", "erpReferences.salesOrderId", "systemReferences.d365SalesOrderId");
const custName = o => pick(o, "soldTo.name", "customer.name", "customerName");
const custId = o => pick(o, "soldTo.customerId", "customer.customerId", "customerId");
const channel = o => pick(o, "sourceChannel", "source.channel", "sourceDocument.channel");

function renderHeader(el) {
  const o = O(), s = state.statusView || {};
  const status = s.status || o.status, cat = s.statusCategory || o.statusCategory;
  setHTML($("#subtitle", el), html`${statusBadge(status, cat)} <span class="chip">${channel(o) || "?"}</span>
    ${custName(o) || custId(o) || ""} · PO <strong>${pick(o, "customerPo", "customerPurchaseOrder.number", "purchaseOrderNumber") || "–"}</strong>
    ${erpId(o) ? html` · D365 <span class="mono">${erpId(o)}</span>` : ""} · v${o.version ?? "?"}
    ${(() => { const f = fulfil(o); return f.regionName || f.regionId ? html` · <span class="chip" title="Fulfilment region / site">${f.regionName || f.regionId}${f.warehouse ? ` · ${f.warehouse}` : ""}</span>` : ""; })()}
    ${(fulfil(o).crossRegion) ? html` <span class="tag" title="Sourced outside the home region">cross-region</span>` : ""}`);

  // Milestone stepper
  let ms = Array.isArray(s.milestones) ? s.milestones : [];
  if (!ms.length) ms = fallbackMilestones(status);
  const attention = categoryOf(status, cat) === "ACTION_REQUIRED";
  setHTML($("#stepperCard", el), html`
    <div class="row"><h2 style="margin:0">Order status</h2><span class="spacer"></span>
      ${s.nextExpected ? html`<span class="small muted">Next expected: <strong>${typeof s.nextExpected === "object" ? (s.nextExpected.label || s.nextExpected.milestone || s.nextExpected.status || JSON.stringify(s.nextExpected)) : humanize(s.nextExpected)}</strong>${s.nextExpected && s.nextExpected.by ? ` by ${fmtDate(s.nextExpected.by)}` : ""}</span>` : ""}</div>
    ${attention ? html`<div class="alert error section-gap"><strong>Action required:</strong> ${humanize(status)}${openExceptions(o).length ? ` – ${openExceptions(o).length} open exception(s)` : ""}</div>` : ""}
    <div class="stepper" role="list">${ms.map((m, i) => {
      const st = m.status === "CURRENT" && attention ? "CURRENT ATTENTION" : m.status || "PENDING";
      return html`<div class="step ${st}" role="listitem" aria-label="${m.label || m.milestone}: ${m.status}">
        <div class="node">${m.status === "DONE" ? "✓" : i + 1}</div>
        <div class="lbl">${m.label || humanize(m.milestone)}</div><div class="at">${m.at ? fmtDate(m.at) : ""}</div></div>`;
    })}</div>`);

  // Top cards: parties + actions
  const party = (p, title) => p ? html`<div><h3>${title}</h3>${kv(p, [["customerId", "Customer"], ["shipToId", "Ship-to"], ["name", "Name"], ["erpAccount", "D365 account"],
    ["gln", "GLN"], ["duns", "DUNS"], ["address", "Address", fmtAddress], ["contact", "Contact", c => [c.name, c.email, c.phone].filter(Boolean).join(" · ")]])}</div>` : "";
  const totals = o.totals || {};
  setHTML($("#topCards", el), html`
    <div class="card"><h2>Order</h2>
      ${kv(o, [["orderId", "Order id"], ["orderGroupId", "Order group"], ["orderType", "Type"], ["orderDate", "Order date", fmtDate],
        ["receivedAt", "Received", fmtDate], ["requestedDeliveryDate", "Requested delivery", fmtDate], ["requestedShipDate", "Requested ship", fmtDate],
        ["promisedDeliveryDate", "Promised delivery", fmtDate], ["currency", "Currency"], ["paymentTerms", "Payment terms", v => typeof v === "object" ? v.code || v.description : v],
        ["sourceDocument", "Source document", v => [v.type || v.documentType, v.reference, v.controlNumber || (v.controlNumbers && v.controlNumbers.st)].filter(Boolean).join(" · ")]])}
      <div class="grid grid-3 section-gap">
        <div class="tile"><div class="label">Total</div><div class="value">${fmtMoney(pick(totals, "grandTotal", "totalAmount", "orderTotal") ?? o.totalAmount, o.currency)}</div></div>
        <div class="tile"><div class="label">Lines</div><div class="value">${(o.lines || []).length}</div></div>
        <div class="tile"><div class="label">Touchless</div><div class="value">${pick(o, "roi.touchless") === false ? "No" : pick(o, "roi.touchless") === true ? "Yes" : "–"}</div></div>
      </div>
      <div class="grid grid-2 section-gap">${party(o.soldTo, "Sold-to")}${party(o.shipTo, "Ship-to")}${party(o.billTo, "Bill-to")}</div>
    </div>
    <div class="card" id="actions"></div>`);
  renderActions(el);
}

function fmtAddress(a) {
  if (!a || typeof a !== "object") return a;
  return [a.line1 || a.street1 || a.street, a.line2, a.city, [a.state || a.stateProvince, a.postalCode].filter(Boolean).join(" "), a.country].filter(Boolean).join(", ");
}

function fallbackMilestones(status) {
  const order = ["RECEIVED", "CONFIRMED", "IN_FULFILLMENT", "SHIPPED", "DELIVERED", "INVOICED"];
  const cur = order.indexOf(categoryOf(status));
  return order.map((m, i) => ({ milestone: m, label: humanize(m), status: cur < 0 ? (i === 0 ? "CURRENT" : "PENDING") : i < cur ? "DONE" : i === cur ? "CURRENT" : "PENDING" }));
}

function openExceptions(o) {
  const ex = Array.isArray(o.exceptions) ? o.exceptions : [];
  return ex.filter(x => !x.resolved && !x.resolvedAt && (x.status || "OPEN") === "OPEN");
}

/* ---------- Actions ---------- */

function renderActions(el) {
  const o = O();
  const status = (state.statusView && state.statusView.status) || o.status;
  const host = $("#actions", el);
  const terminal = TERMINAL.includes(status);
  const shippedish = ["PARTIALLY_SHIPPED", "SHIPPED", "DELIVERED", "INVOICED", "CLOSED"].includes(status);
  const lines = o.lines || [];
  setHTML(host, html`
    <h2>Actions</h2>
    <div class="row">
      ${status === "ON_HOLD"
        ? html`<button class="btn" data-act="release-hold">Release hold</button>`
        : html`<button class="btn" data-act="hold" ${terminal || shippedish ? "disabled" : ""}>Place hold</button>`}
      <button class="btn danger" data-act="cancel" ${terminal || shippedish ? "disabled" : ""}>Cancel order</button>
      ${CORRECTABLE.includes(status) ? html`<button class="btn primary" data-act="correct">Correct &amp; resubmit</button>` : ""}
      ${status === "EXCEPTION" || status === "ERP_REJECTED" ? html`<button class="btn" data-act="resubmit">Resubmit as is</button>` : ""}
    </div>
    <div class="field section-gap"><label for="actor">Acting user</label><input id="actor" value="cs.agent@console"></div>
    <div id="correctHost"></div>
    <h3 class="section-gap">Fulfillment simulator <span class="small muted">(System API – emulates D365 / WMS / TMS events)</span></h3>
    <div class="row">
      ${SIM_STEPS.map(([to, label]) => html`<button class="btn sm" data-sim="${to}" ${terminal && to !== "CLOSED" ? "disabled" : ""}>${label}</button>`)}
      ${status === "CREDIT_HOLD" ? html`<button class="btn sm primary" data-act="credit">Release credit hold</button>` : ""}
    </div>
    <details class="section-gap"><summary>Partial shipment / carrier options</summary>
      <div class="fields section-gap">
        <div class="field"><label for="carrier">Carrier SCAC</label><input id="carrier" placeholder="e.g. TFCL"></div>
        ${lines.map(l => html`<div class="field"><label for="ship-${l.lineNumber}">Line ${l.lineNumber} ship qty</label>
          <input id="ship-${l.lineNumber}" data-line="${l.lineNumber}" class="shipqty" type="number" min="0" step="1" placeholder="${lineQty(l, "ordered") ?? ""}"></div>`)}
      </div>
      <p class="small muted">Leave quantities blank to ship everything confirmed. Applies to the Ship step.</p>
    </details>
    <div id="actResult" class="section-gap" aria-live="polite"></div>`);

  host.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", () => doAction(el, b.dataset.act, b)));
  host.querySelectorAll("[data-sim]").forEach(b => b.addEventListener("click", () => doSim(el, b.dataset.sim, b)));
}

async function doAction(el, act, btn) {
  const id = encodeURIComponent(state.id);
  const actor = $("#actor", el).value.trim() || "console.user";
  const out = $("#actResult", el);
  const prev = O().status;
  try {
    if (act === "correct") { renderCorrection(el); return; }
    btn.disabled = true;
    if (act === "hold") {
      const reason = prompt("Hold reason", "Customer requested hold");
      if (reason === null) return;
      await api.process(`/v1/orders/${id}/hold`, { body: { reason, actor } });
    } else if (act === "release-hold") {
      await api.process(`/v1/orders/${id}/release-hold`, { body: { reason: "Released from console", actor } });
    } else if (act === "cancel") {
      const reasonCode = prompt("Cancel reason code (e.g. CUSTOMER_REQUEST, DUPLICATE, PRICE_DISPUTE)", "CUSTOMER_REQUEST");
      if (reasonCode === null) return;
      await api.process(`/v1/orders/${id}/cancel`, { body: { reasonCode, reason: reasonCode, actor } });
    } else if (act === "resubmit") {
      await api.process(`/v1/orders/${id}/resubmit`, { body: { actor, comment: "Resubmitted from console" } });
    } else if (act === "credit") {
      await api.system(`/v1/sim/orders/${id}/release-credit-hold`, { body: { actor } });
    }
    toast(`${humanize(act)} sent`, "ok");
    setHTML(out, "");
    await load(el);
    if (act === "credit" || act === "resubmit") pollAfterAction(el, O().status || prev);
  } catch (e) {
    setHTML(out, errorBox(e)); toast(e.message, "error");
  } finally { btn.disabled = false; }
}

async function doSim(el, to, btn) {
  const body = { to };
  const carrier = $("#carrier", el) && $("#carrier", el).value.trim();
  if (carrier) body.carrier = carrier;
  if (to === "SHIPPED") {
    const lines = $$(".shipqty", el).filter(i => i.value !== "").map(i => ({ lineNumber: Number(i.dataset.line), quantity: Number(i.value) }));
    if (lines.length) body.lines = lines;
  }
  const out = $("#actResult", el);
  btn.disabled = true;
  const prev = (state.statusView && state.statusView.status) || O().status;
  try {
    const r = await api.system(`/v1/sim/orders/${encodeURIComponent(state.id)}/advance`, { body });
    toast(`Simulator: ${humanize(to)} event emitted`, "ok");
    setHTML(out, r && typeof r === "object" ? html`<details><summary>Simulator response</summary>${json(r)}</details>` : "");
    pollAfterAction(el, prev, to === "SHIPPED" && body.lines ? null : to);
  } catch (e) {
    setHTML(out, errorBox(e)); toast(e.message, "error");
  } finally { btn.disabled = false; }
}

function renderCorrection(el) {
  const o = O();
  const host = $("#correctHost", el);
  const ex = openExceptions(o);
  setHTML(host, html`<form class="card section-gap" id="correctForm" style="background:var(--surface-2)">
    <h3>Correct order (v${o.version ?? "?"})</h3>
    ${ex.length ? html`<div class="alert warn"><ul>${ex.map(x => html`<li><strong>${x.ruleId || x.code}</strong> ${x.lineNumber ? `line ${x.lineNumber}` : ""} ${x.field ? `(${x.field})` : ""}: ${x.message}</li>`)}</ul></div>` : ""}
    <div class="fields section-gap">
      <div class="field"><label for="c-po">Customer PO</label><input id="c-po" value="${pick(o, "customerPo") || ""}"></div>
      <div class="field"><label for="c-rdd">Requested delivery</label><input id="c-rdd" type="date" value="${String(o.requestedDeliveryDate || "").slice(0, 10)}"></div>
      <div class="field"><label for="c-shipto">Ship-to id</label><input id="c-shipto" value="${pick(o, "shipTo.shipToId") || ""}"></div>
    </div>
    <div class="table-wrap section-gap"><table><thead><tr><th>Line</th><th>Item</th><th class="num">Qty</th><th>UoM</th><th class="num">Unit price</th></tr></thead>
      <tbody>${(o.lines || []).map(l => html`<tr data-line="${l.lineNumber}">
        <td>${l.lineNumber}</td>
        <td><input class="c-item" value="${pick(l, "item.itemNumber", "itemNumber") || ""}" aria-label="Item line ${l.lineNumber}"></td>
        <td><input class="c-qty" type="number" step="1" value="${lineQty(l, "ordered") ?? ""}" style="width:80px" aria-label="Quantity line ${l.lineNumber}"></td>
        <td><input class="c-uom" value="${pick(l, "quantity.uom", "uom") || ""}" style="width:70px" aria-label="UoM line ${l.lineNumber}"></td>
        <td><input class="c-price" type="number" step="0.01" value="${pick(l, "unitPrice.amount", "unitPrice") ?? ""}" style="width:100px" aria-label="Price line ${l.lineNumber}"></td></tr>`)}</tbody></table></div>
    <div class="field section-gap"><label for="c-reason">Correction reason</label><input id="c-reason" value="Corrected per customer confirmation" required></div>
    <div class="row section-gap"><button class="btn primary" type="submit" id="c-submit">Save correction &amp; resubmit</button>
      <button class="btn" type="button" id="c-cancel">Close</button></div>
  </form>`);
  $("#c-cancel", host).addEventListener("click", () => setHTML(host, ""));
  $("#correctForm", host).addEventListener("submit", async e => {
    e.preventDefault();
    const changes = {};
    const po = $("#c-po", host).value.trim(), rdd = $("#c-rdd", host).value, st = $("#c-shipto", host).value.trim();
    if (po !== (o.customerPo || "")) changes.customerPo = po;
    if (rdd && rdd !== String(o.requestedDeliveryDate || "").slice(0, 10)) changes.requestedDeliveryDate = rdd;
    if (st && st !== pick(o, "shipTo.shipToId")) changes.shipTo = { shipToId: st };
    const lineChanges = [];
    $$("tbody tr", host).forEach(tr => {
      const ln = Number(tr.dataset.line);
      const l = (o.lines || []).find(x => Number(x.lineNumber) === ln) || {};
      const c = { lineNumber: ln };
      const item = $(".c-item", tr).value.trim(), qty = $(".c-qty", tr).value, uom = $(".c-uom", tr).value.trim(), price = $(".c-price", tr).value;
      if (item && item !== pick(l, "item.itemNumber", "itemNumber")) c.item = { itemNumber: item };
      if ((qty !== "" && Number(qty) !== Number(lineQty(l, "ordered"))) || (uom && uom !== pick(l, "quantity.uom", "uom"))) c.quantity = { value: Number(qty), uom: uom || pick(l, "quantity.uom", "uom") };
      if (price !== "" && Number(price) !== Number(pick(l, "unitPrice.amount", "unitPrice"))) c.unitPrice = { amount: Number(price) };
      if (Object.keys(c).length > 1) lineChanges.push(c);
    });
    if (lineChanges.length) changes.lines = lineChanges;
    const actor = $("#actor", el).value.trim() || "console.user";
    const btn = $("#c-submit", host);
    btn.disabled = true;
    try {
      const headers = {};
      if (state.etag) headers["If-Match"] = state.etag;
      if (Object.keys(changes).length) {
        await api.process(`/v1/orders/${encodeURIComponent(state.id)}`, { method: "PATCH", headers, body: { changes, reason: $("#c-reason", host).value, actor } });
      }
      const r = await api.process(`/v1/orders/${encodeURIComponent(state.id)}/resubmit`, { body: { actor, comment: $("#c-reason", host).value } });
      const exc = items(r && r.exceptions);
      toast(exc.length ? `Resubmitted – still ${exc.length} exception(s)` : "Corrected and resubmitted", exc.length ? "error" : "ok");
      setHTML(host, "");
      await load(el);
      pollAfterAction(el, O().status);
    } catch (err) {
      const box = document.createElement("div");
      setHTML(box, errorBox(err));
      host.prepend(box);
      if (err.status === 412 || err.status === 409) toast("Order changed since you loaded it – refresh and try again", "error");
    } finally { btn.disabled = false; }
  });
  host.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- Tabs ---------- */

function lineQty(l, kind) {
  const map = {
    ordered: ["quantities.ordered", "quantity.value", "orderedQuantity", "quantity"],
    confirmed: ["quantities.confirmed", "confirmedQuantity.value", "confirmedQuantity"],
    backordered: ["quantities.backordered", "backorderedQuantity.value", "backorderedQuantity"],
    shipped: ["quantities.shipped", "shippedQuantity.value", "shippedQuantity"],
    delivered: ["quantities.delivered", "deliveredQuantity.value", "deliveredQuantity"],
    invoiced: ["quantities.invoiced", "invoicedQuantity.value", "invoicedQuantity"],
  };
  const v = pick(l, ...map[kind]);
  return typeof v === "object" && v !== null ? v.value : v;
}

async function renderTab(el) {
  const host = $("#tab", el);
  const o = O();
  if (!state.order) return;
  const t = state.tab;
  if (t === "overview") return renderOverview(host, o);
  if (t === "lines") return renderLines(host, o);
  if (t === "raw") return setHTML(host, html`<div class="card">${json(o)}</div>`);
  setHTML(host, loading());
  try {
    if (t === "history") await renderHistory(host, `/v1/orders/${encodeURIComponent(state.id)}/history`);
    if (t === "documents") await renderDocuments(host);
    if (t === "related") await renderRelated(host, o);
  } catch (e) { setHTML(host, errorBox(e)); }
}

/** Fulfilment & sourcing facts: canonical `fulfillment`, falling back to the status view / shipping block. */
function fulfil(o) {
  const f = o.fulfillment || {};
  const s = state.statusView || {};
  const shp = o.shipping || {};
  return {
    regionId: f.regionId || s.region, regionName: f.regionName || s.regionName,
    siteId: f.siteId || shp.shipFromSite, warehouse: f.warehouse || s.shipFromWarehouse || shp.shipFromWarehouse,
    siteName: f.siteName, accountScope: f.accountScope, crossRegion: !!f.crossRegion,
    sourcing: f.sourcing || (s.sourcingRule ? { rule: s.sourcingRule } : null),
  };
}

const RULE_LABEL = { HOME_SITE: "Home site", ALTERNATE_SITE_IN_REGION: "Alternate site in region", CROSS_REGION: "Cross-region", BEST_AVAILABLE: "Best available" };

function fulfilmentCard(o) {
  const f = fulfil(o);
  if (!f.regionId && !f.warehouse && !f.sourcing) return "";
  const src = f.sourcing || {};
  const cands = items(src.candidates || []);
  const pctFill = v => (v === null || v === undefined ? "–" : `${fmtNum(Number(v) <= 1 ? Number(v) * 100 : Number(v), 1)}%`);
  return html`<div class="card"><h2>Fulfillment &amp; sourcing</h2>
    <div class="grid grid-2">
      <div>${kv({
        region: f.regionName || f.regionId, site: f.siteName ? `${f.siteName} (${f.siteId || ""})` : f.siteId,
        warehouse: f.warehouse, accountScope: f.accountScope, crossRegion: f.crossRegion,
        rule: src.rule, decidedAt: src.decidedAt }, [
        ["region", "Region"], ["site", "Site"], ["warehouse", "Warehouse"],
        ["accountScope", "Account scope", v => (v === "NATIONAL" ? "National account" : v === "REGIONAL" ? "Regional account" : v)],
        ["crossRegion", "Cross-region", v => (v ? "Yes – shipped outside the home region" : "No")],
        ["rule", "Sourcing rule", v => RULE_LABEL[v] || humanize(v)], ["decidedAt", "Decided", fmtDate]])}
        ${src.reason ? html`<p class="small section-gap">${src.reason}</p>` : ""}</div>
      <div>${cands.length ? html`<div class="table-wrap"><table class="dense"><thead><tr><th>Warehouse</th><th>Region</th><th class="num">Fill rate</th><th></th></tr></thead>
        <tbody>${cands.map(c => html`<tr class="${c.chosen ? "selected" : ""}"><td class="mono">${c.warehouse}${c.siteId ? html`<div class="small muted">${c.siteId}</div>` : ""}</td>
          <td>${c.regionId || "–"}</td><td class="num">${pctFill(c.fillRate)}</td>
          <td class="nowrap">${c.home ? html`<span class="tag">home</span> ` : ""}${c.chosen ? html`<span class="badge ok">chosen</span>` : ""}</td></tr>`)}</tbody></table></div>
        <div class="small muted section-gap">Candidates the sourcing engine evaluated (fill rate = share of ordered quantity it could confirm).</div>`
        : html`<div class="empty">No sourcing candidates recorded</div>`}</div>
    </div></div>`;
}

function renderOverview(host, o) {
  const s = state.statusView || {};
  const ex = Array.isArray(o.exceptions) ? o.exceptions : [];
  const dq = items(pick(o, "dataQuality.results", "dataQuality.rules", "dataQualityResults", "qualityResults") || []);
  const roi = o.roi || {};
  const shipments = items(s.shipments || o.shipments || []);
  const invoices = items(s.invoices || o.invoices || []);
  setHTML(host, html`
    ${fulfilmentCard(o)}
    <div class="grid grid-2 section-gap">
      <div class="card"><h2>Exceptions</h2>
        ${ex.length ? html`<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Severity</th><th>Line</th><th>Message</th><th>State</th></tr></thead>
          <tbody>${ex.map(x => html`<tr><td class="mono">${x.ruleId || x.code}</td><td>${sevBadge(x.severity)}</td><td>${x.lineNumber || ""}</td>
            <td>${x.message || ""}${x.field ? html`<div class="small muted">${x.field}</div>` : ""}</td>
            <td>${x.resolved || x.resolvedAt || (x.status && x.status !== "OPEN") ? html`<span class="badge ok">${x.status || "Resolved"}</span>` : html`<span class="badge sev-ERROR">Open</span>`}</td></tr>`)}</tbody></table></div>`
          : html`<div class="empty">No exceptions</div>`}</div>
      <div class="card"><h2>Data quality</h2>
        ${dq.length ? html`<div class="table-wrap"><table><thead><tr><th>Rule</th><th>Result</th><th>Detail</th></tr></thead>
          <tbody>${dq.map(r => html`<tr><td class="mono">${r.ruleId || r.code}<div class="small muted">${r.name || r.description || ""}</div></td>
            <td>${String(r.result || r.status || (r.passed ? "PASSED" : "FAILED")).toUpperCase().startsWith("PASS") ? html`<span class="badge ok">${r.result || r.status || "PASSED"}</span>` : sevBadge(r.severity || "ERROR")}</td>
            <td class="small">${r.message || r.detail || ""}</td></tr>`)}</tbody></table></div>`
          : o.dataQuality ? json(o.dataQuality) : html`<div class="empty">No data-quality results recorded</div>`}</div>
      <div class="card"><h2>ROI tags</h2>${Object.keys(roi).length ? kv(roi, Object.keys(roi).map(k => [k, humanize(k), v => typeof v === "object" ? raw(`<pre class="json">${JSON.stringify(v, null, 2).replace(/[<>&]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]))}</pre>`) : String(v)])) : html`<div class="empty">No ROI tags</div>`}</div>
      <div class="card"><h2>Shipments &amp; invoices</h2>
        ${shipments.length ? html`<h3>Shipments</h3><div class="table-wrap"><table><thead><tr><th>Shipment</th><th>Carrier</th><th>Tracking / BOL</th><th>Shipped</th><th>Delivered</th></tr></thead>
          <tbody>${shipments.map(sh => html`<tr><td class="mono">${sh.shipmentId || sh.id || ""}</td><td>${pick(sh, "carrier.scac", "carrier.name", "carrier", "carrierScac") || ""}</td>
            <td class="mono">${sh.trackingNumber || sh.billOfLading || sh.bolNumber || ""}</td><td>${fmtDate(sh.shippedAt || sh.shipDate)}</td><td>${fmtDate(sh.deliveredAt)}</td></tr>`)}</tbody></table></div>` : ""}
        ${invoices.length ? html`<h3 class="section-gap">Invoices</h3><div class="table-wrap"><table><thead><tr><th>Invoice</th><th>Date</th><th>Due</th><th class="num">Amount</th></tr></thead>
          <tbody>${invoices.map(iv => html`<tr><td class="mono">${iv.invoiceId || iv.invoiceNumber || ""}</td><td>${fmtDate(iv.invoiceDate || iv.postedAt)}</td><td>${fmtDate(iv.dueDate)}</td>
            <td class="num">${fmtMoney(pick(iv, "totalAmount", "amount", "total"), iv.currency || o.currency)}</td></tr>`)}</tbody></table></div>` : ""}
        ${!shipments.length && !invoices.length ? html`<div class="empty">Nothing shipped or invoiced yet</div>` : ""}
      </div>
    </div>
    ${o.notes || (o.references && o.references.length) ? html`<div class="card section-gap"><h2>Notes &amp; references</h2>${json({ notes: o.notes, references: o.references })}</div>` : ""}`);
}

function renderLines(host, o) {
  const lines = o.lines || [];
  const statusLines = items((state.statusView && state.statusView.lines) || []);
  setHTML(host, html`<div class="card"><div class="table-wrap"><table>
    <thead><tr><th>#</th><th>Item</th><th>Status</th><th>UoM</th><th class="num">Ordered</th><th class="num">Confirmed</th><th class="num">Backordered</th>
      <th class="num">Shipped</th><th class="num">Invoiced</th><th class="num">Unit price</th><th class="num">Extended</th><th>Requested</th></tr></thead>
    <tbody>${lines.map(l => {
      const sl = statusLines.find(x => Number(x.lineNumber) === Number(l.lineNumber)) || {};
      const q = k => fmtNum(lineQty(sl, k) ?? lineQty(l, k), 2);
      return html`<tr>
        <td>${l.lineNumber}</td>
        <td><span class="mono">${pick(l, "item.itemNumber", "itemNumber") || ""}</span>
          <div class="small muted">${pick(l, "item.description", "description") || ""}</div>
          <div class="small muted">${[pick(l, "item.gtin", "gtin") && `GTIN ${pick(l, "item.gtin", "gtin")}`, pick(l, "item.buyerItemNumber", "buyerItemNumber") && `Buyer ${pick(l, "item.buyerItemNumber", "buyerItemNumber")}`, pick(l, "item.packSize")].filter(Boolean).join(" · ")}</div></td>
        <td>${statusBadge(sl.status || sl.lineStatus || l.lineStatus || l.status, "")}</td>
        <td>${pick(l, "quantity.uom", "uom") || ""}</td>
        <td class="num">${q("ordered")}</td><td class="num">${q("confirmed")}</td><td class="num">${q("backordered")}</td>
        <td class="num">${q("shipped")}</td><td class="num">${q("invoiced")}</td>
        <td class="num">${fmtMoney(pick(l, "unitPrice.amount", "unitPrice"), o.currency)}${pick(l, "pricing.priceSource", "unitPrice.source", "priceSource") ? html`<div class="small muted">${pick(l, "pricing.priceSource", "unitPrice.source", "priceSource")}</div>` : ""}</td>
        <td class="num">${fmtMoney(pick(l, "extendedAmount.amount", "extendedAmount", "lineAmount"), o.currency)}</td>
        <td class="nowrap">${fmtDate(pick(l, "requestedDeliveryDate", "requestedShipDate"))}</td></tr>`;
    })}</tbody></table></div></div>`);
}

async function renderHistory(host, path) {
  const r = await api.process(path);
  const list = items(r).slice().sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0) || String(a.occurredAt).localeCompare(String(b.occurredAt)));
  if (!list.length) { setHTML(host, html`<div class="card empty">No history</div>`); return; }
  const types = Array.from(new Set(list.map(e => e.eventType))).sort();
  setHTML(host, html`<div class="card">
    <div class="row"><h2 style="margin:0">Lifecycle history</h2><span class="muted small">${list.length} event(s)</span><span class="spacer"></span>
      <label class="sr" for="evFilter" style="position:absolute;left:-999px">Filter event type</label>
      <select id="evFilter" style="max-width:240px"><option value="">All event types</option>${types.map(t => html`<option>${t}</option>`)}</select></div>
    <ol class="timeline section-gap" id="tl"></ol></div>`);
  const draw = filter => {
    setHTML($("#tl", host), html`${list.filter(e => !filter || e.eventType === filter).map(e => {
      const bad = /FAILED|REJECT|EXCEPTION|HOLD|CANCEL|IGNORED/.test(e.eventType || "");
      const doc = /DOCUMENT|NOTIFICATION/.test(e.eventType || "");
      const actor = e.actor ? [e.actor.name || e.actor.id, e.actor.type && `(${e.actor.type})`].filter(Boolean).join(" ") : "";
      const extra = {};
      for (const k of ["changes", "lineChanges", "payload", "data", "documentRef"]) if (e[k] && (!Array.isArray(e[k]) || e[k].length)) extra[k] = e[k];
      return html`<li class="${bad ? "bad" : doc ? "doc" : ""}">
        <div class="ev-head"><span class="ev-type">${humanize(e.eventType)}</span>
          ${e.orderId && path.includes("order-groups") ? html`<a class="chip mono" href="#/orders/${encodeURIComponent(e.orderId)}">${e.orderId}</a>` : ""}
          ${e.fromStatus || e.toStatus ? html`<span class="small">${e.fromStatus ? statusBadge(e.fromStatus) : ""} ${e.toStatus && e.toStatus !== e.fromStatus ? html`→ ${statusBadge(e.toStatus)}` : ""}</span>` : ""}
          ${e.tier ? html`<span class="chip">${e.tier}</span>` : ""}${e.sourceSystem ? html`<span class="chip">${e.sourceSystem}</span>` : ""}
          ${e.reasonCode ? html`<span class="badge sev-WARNING">${e.reasonCode}</span>` : ""}</div>
        ${e.message ? html`<div>${e.message}</div>` : ""}
        <div class="ev-meta">#${e.sequence ?? "?"} · ${fmtDate(e.occurredAt)}${e.recordedAt && e.recordedAt !== e.occurredAt ? ` (recorded ${fmtDate(e.recordedAt)})` : ""}
          ${e.elapsedSincePreviousSeconds != null ? ` · +${fmtDuration(e.elapsedSincePreviousSeconds)}` : ""}${actor ? ` · ${actor}` : ""}
          ${e.correlationId ? html` · <span class="mono">${String(e.correlationId).slice(0, 8)}</span>` : ""}</div>
        ${Object.keys(extra).length ? html`<details><summary>Details</summary>${json(extra)}</details>` : ""}
      </li>`;
    })}`);
  };
  draw("");
  $("#evFilter", host).addEventListener("change", e => draw(e.target.value));
}

async function renderDocuments(host) {
  const r = await api.process(`/v1/orders/${encodeURIComponent(state.id)}/documents`);
  const list = items(r);
  if (!list.length) { setHTML(host, html`<div class="card empty">No documents yet</div>`); return; }
  setHTML(host, html`<div class="grid grid-2">
    <div class="card"><h2>Documents</h2><div class="table-wrap"><table><thead><tr><th>Type</th><th>Direction</th><th>Control #</th><th>Created</th></tr></thead>
      <tbody>${list.map(d => html`<tr class="clickable" data-doc="${d.documentId}" tabindex="0">
        <td><strong>${d.documentType || d.type}</strong>${d.description ? html`<div class="small muted">${d.description}</div>` : ""}</td>
        <td><span class="chip">${d.direction || ""}</span></td><td class="mono">${d.controlNumber || ""}</td><td class="nowrap">${fmtDate(d.createdAt)}</td></tr>`)}</tbody></table></div></div>
    <div class="card"><h2 id="docTitle">Select a document</h2><div id="docView" class="muted">Click a row to view its content.</div></div></div>`);
  const open = async id => {
    setHTML($("#docView", host), loading());
    try {
      const d = await api.process(`/v1/orders/${encodeURIComponent(state.id)}/documents/${encodeURIComponent(id)}`);
      const content = typeof d === "string" ? d : d.content ?? d.body ?? d;
      $("#docTitle", host).textContent = `${d.documentType || "Document"} ${d.controlNumber ? "#" + d.controlNumber : ""}`;
      let body;
      if (typeof content === "string") {
        try { body = json(JSON.parse(content)); } catch { body = x12(content); }
      } else body = json(content);
      setHTML($("#docView", host), body);
    } catch (e) { setHTML($("#docView", host), errorBox(e)); }
  };
  host.querySelectorAll("tr[data-doc]").forEach(tr => {
    tr.addEventListener("click", () => open(tr.dataset.doc));
    tr.addEventListener("keydown", e => { if (e.key === "Enter") open(tr.dataset.doc); });
  });
  open(list[list.length - 1].documentId);
}

async function renderRelated(host, o) {
  const r = await api.process(`/v1/orders/${encodeURIComponent(state.id)}/related`);
  const list = items(r.orders || r.items || r);
  const links = items(r.relatedOrders || o.relatedOrders || []);
  const group = r.orderGroupId || o.orderGroupId;
  setHTML(host, html`<div class="card"><h2>Order group <span class="mono">${group || "–"}</span></h2>
      ${list.length ? html`<div class="table-wrap"><table><thead><tr><th>Order</th><th>Status</th><th>PO</th><th>Ship-to</th><th>Received</th><th>Relation</th></tr></thead>
        <tbody>${list.map(x => html`<tr><td class="mono"><a href="#/orders/${encodeURIComponent(x.orderId)}">${x.orderId}</a>${x.orderId === state.id ? " (this)" : ""}</td>
          <td>${statusBadge(x.status, x.statusCategory)}</td><td>${x.customerPo || ""}</td><td>${x.shipToId || pick(x, "shipTo.shipToId") || ""}</td>
          <td>${fmtDate(x.receivedAt)}</td><td>${x.relationType || x.relation || ""}</td></tr>`)}</tbody></table></div>` : html`<div class="empty">No other orders in this group</div>`}
      ${links.length ? html`<h3 class="section-gap">Linked orders</h3>${json(links)}` : ""}
    </div>
    <div class="section-gap" id="groupHistory">${group ? loading("Loading group history…") : ""}</div>`);
  if (group) {
    try { await renderHistory($("#groupHistory", host), `/v1/order-groups/${encodeURIComponent(group)}/history`); }
    catch (e) { setHTML($("#groupHistory", host), errorBox(e)); }
  }
}
