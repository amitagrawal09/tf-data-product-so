// Operations overview for the selected operating scope (National → Region → Site, all / national / regional accounts):
// KPI cards against target, regional performance, orders at risk, most active customers and recent orders.
import { api, items, scope, scopeQuery } from "../api.js";
import { html, setHTML, $, fmtNum, fmtDate, fmtMoney, humanize, statusBadge, errorBox, loading, CATEGORIES, kpiCard, limitedTable, wireLimited } from "../ui.js";
import { drillTo, regionName, siteName } from "../scope.js";
import { me, rangeFromDate, rangeLabel } from "../session.js";
import { subtitle } from "../app.js";

let timer = null;

export async function mount(el) {
  const s = scope();
  const perfTitle = s.site ? "Site performance" : s.region ? `Site performance · ${regionName(s.region)}` : "Regional performance";
  const p = me();
  setHTML(el, html`
    <div class="page-head">
      <div><h1>Operations overview</h1>
        <div class="sub">${subtitle()}</div></div>
      <div class="row">
        <span class="health" id="health"></span>
        <label class="check"><input type="checkbox" id="autoRefresh"> Auto-refresh (10 s)</label>
        <button class="btn" id="refresh">Refresh</button>
        ${p && p.canOperateOrders ? html`<a class="btn primary" href="#/new">New order</a>` : ""}
      </div>
    </div>
    <div id="kpis">${loading("Loading KPIs…")}</div>
    <div class="grid grid-2 section-gap">
      <div class="card"><div class="panel-head"><h2>${perfTitle}</h2>
        ${s.region || s.site ? html`<button class="btn sm" id="upScope">${s.site ? "↑ Whole region" : "↑ National"}</button>` : html`<span class="panel-hint">Select a row to drill in</span>`}</div>
        <div id="perf">${loading()}</div></div>
      <div class="card"><div class="panel-head"><h2>Orders at risk</h2><span id="riskTotals" class="panel-hint"></span></div>
        <div class="panel-hint" style="margin:-6px 0 8px">Open orders likely to miss the customer promise. Act on these first.</div>
        <div id="risk">${loading()}</div></div>
    </div>
    <div class="grid grid-2 section-gap">
      <div class="card"><div class="panel-head"><h2>Most active customers</h2><span class="panel-hint" id="custHint">${rangeLabel()}</span></div><div id="customers">${loading()}</div></div>
      <div class="card"><div class="panel-head"><h2>Pipeline by status</h2><span class="panel-hint">Current open and closed orders</span></div><div id="byCat">${loading()}</div></div>
    </div>
    <div class="card section-gap">
      <div class="panel-head"><h2>Recent orders</h2><a class="small" href="#/orders">All orders →</a></div>
      <div id="recent">${loading()}</div>
    </div>`);
  $("#refresh", el).addEventListener("click", () => load(el));
  $("#autoRefresh", el).addEventListener("change", e => {
    clearInterval(timer);
    if (e.target.checked) timer = setInterval(() => load(el), 10000);
  });
  const up = $("#upScope", el);
  if (up) up.addEventListener("click", () => drillTo(s.site ? { region: s.region } : {}));
  await load(el);
}

export function unmount() { clearInterval(timer); timer = null; }

async function load(el) {
  await Promise.all([loadHealth(el), loadSummary(el), loadKpis(el), loadRisk(el), loadOrders(el)]);
}

/* ---------- formatting ---------- */
function pct(v) { if (v === null || v === undefined || v === "") return "–"; const n = Number(v); if (isNaN(n)) return "–"; return `${fmtNum(n <= 1 ? n * 100 : n, 1)}%`; }
function rateClass(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v) > 1 ? Number(v) / 100 : Number(v);
  return n >= 0.95 ? "rate-good" : n >= 0.9 ? "rate-warn" : "rate-bad";
}
const rate = (v, target = 0.95) => {
  if (v === null || v === undefined || v === "") return html`<span class="pill">–</span>`;
  const n = Number(v) > 1 ? Number(v) / 100 : Number(v);
  return html`<span class="pill ${n >= target ? "good" : "bad"}">${pct(v)}</span>`;
};
function money(v) {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  if (Math.abs(n) >= 1e6) return `$${fmtNum(n / 1e6, 2)}M`;
  if (Math.abs(n) >= 1e4) return `$${fmtNum(n / 1e3, 1)}k`;
  return fmtMoney(n, "USD");
}

/* ---------- health ---------- */
async function loadHealth(el) {
  const layers = [["exp", "Experience API"], ["process", "Process API"], ["system", "System API"]];
  const results = await Promise.all(layers.map(async ([l, name]) => {
    try {
      const h = await api[l]("/health");
      const st = String((h && h.status) || "ok").toLowerCase();
      const up = ["ok", "up", "healthy", "pass"].includes(st);
      const extra = h && h.d365Mode ? ` · D365 ${h.d365Mode}` : "";
      return html`<span class="small" title="${name}: ${st}${extra}${h && h.version ? " · v" + h.version : ""}"><span class="dot ${up ? "up" : "down"}"></span>${name.replace(" API", "")}</span>`;
    } catch (e) {
      return html`<span class="small" title="${name} unreachable: ${e.message}"><span class="dot down"></span>${name.replace(" API", "")}</span>`;
    }
  }));
  const host = $("#health", el);
  if (host) setHTML(host, html`${results}`);
}

/* ---------- status / channel bars ---------- */
function toPairs(v) {
  if (!v) return [];
  if (Array.isArray(v)) return v.map(x => [x.key ?? x.status ?? x.statusCategory ?? x.category ?? x.channel ?? x.sourceChannel ?? x.name, Number(x.count ?? x.orders ?? x.value ?? 0)]);
  if (typeof v === "object") return Object.entries(v).map(([k, n]) => [k, Number(typeof n === "object" ? n.count ?? 0 : n)]);
  return [];
}
function bars(pairs, linkKey) {
  if (!pairs.length) return html`<div class="empty">No orders in this scope</div>`;
  const max = Math.max(1, ...pairs.map(p => p[1]));
  return html`<div class="bars">${pairs.map(([k, n]) => html`
    <div class="bar"><a href="#/orders?${linkKey}=${encodeURIComponent(k)}" title="${k}">${humanize(k)}</a>
      <div class="track"><div class="fill" style="width:${(n / max) * 100}%"></div></div><span class="n">${fmtNum(n)}</span></div>`)}</div>`;
}
async function loadSummary(el) {
  try {
    const s = await api.process("/v1/status/summary", { query: scopeQuery() });
    const byCat = toPairs(s.byStatusCategory || s.byCategory).sort((a, b) => CATEGORIES.indexOf(a[0]) - CATEGORIES.indexOf(b[0]));
    setHTML($("#byCat", el), bars(byCat, "statusCategory"));
  } catch (e) {
    const n = $("#byCat", el); if (n) setHTML(n, errorBox(e));
  }
}

/* ---------- KPIs + performance table ---------- */
// [key, label, format, hint, test(value) → true when within target; null = informational]
const ratio = v => (v == null || v === "" ? null : Number(v) > 1 ? Number(v) / 100 : Number(v));
const TILES = [
  ["perfectOrderRate", "Perfect order", pct, () => "Target ≥ 90% · touchless, on time, in full", v => ratio(v) >= 0.9],
  ["otifRate", "OTIF", pct, k => (k.onTimeDeliveryRate != null || k.inFullRate != null) ? `Target ≥ 95% · on time ${pct(k.onTimeDeliveryRate)} · in full ${pct(k.inFullRate)}` : "Target ≥ 95%", v => ratio(v) >= 0.95],
  ["fillRate", "Fill rate", pct, () => "Target ≥ 98% · shipped ÷ ordered", v => ratio(v) >= 0.98],
  ["ordersAtRisk", "At risk", v => fmtNum(v), k => k.valueAtRisk != null ? `Target 0 · ${money(k.valueAtRisk)} at risk` : "Target 0", v => Number(v) === 0],
  ["touchlessRate", "Touchless", pct, () => "Target ≥ 80% · no manual touch", v => ratio(v) >= 0.8],
  ["firstPassValidationRate", "First-pass", pct, () => "Target ≥ 90% · all rules first time", v => ratio(v) >= 0.9],
  ["openExceptions", "Open exceptions", v => fmtNum(v), k => k.ordersTotal != null ? `Target 0 · of ${fmtNum(k.ordersTotal)} orders` : "Target 0", v => Number(v) === 0],
  ["dataFreshnessSeconds", "Freshness", v => (v == null ? "–" : `${fmtNum(v, 1)} s`), () => "Target ≤ 60 s · source → data product", v => Number(v) <= 60],
];

async function loadKpis(el) {
  const host = $("#kpis", el);
  try {
    const k = await api.process("/v1/metrics/kpis", { query: scopeQuery() });
    const cards = TILES.filter(([key]) => key in (k || {})).map(([key, label, fmt, hint, ok]) => {
      const v = k[key];
      const state = v == null || v === "" ? "info" : ok(v) ? "ok" : "breach";
      return kpiCard({ label, value: fmt(v, k), state, hint: hint(k) });
    });
    if ("revenue" in (k || {})) cards.push(kpiCard({ label: "Revenue", value: money(k.revenue), hint: k.avgOrderValue != null ? `Avg order ${money(k.avgOrderValue)}` : "" }));
    setHTML(host, cards.length ? html`<div class="kpis">${cards}</div>` : html`<div class="empty card">No KPIs for this scope yet</div>`);
    renderPerf(el, k);
  } catch (e) {
    setHTML(host, errorBox(e));
    setHTML($("#perf", el), errorBox(e));
  }
}

function renderPerf(el, k) {
  const host = $("#perf", el);
  const s = scope();
  const bySite = !!(s.region || s.site);
  let rows = items(bySite ? k.bySite : k.byRegion);
  if (bySite && s.site) rows = rows.filter(r => r.site === s.site);
  if (!rows.length) {
    setHTML(host, html`<div class="empty">${bySite ? "No site breakdown for this scope yet" : "No regional breakdown yet"}</div>`);
    return;
  }
  const key = bySite ? "site" : "region";
  setHTML(host, html`<div class="table-wrap"><table class="dense perf"><thead><tr>
      <th>${bySite ? "Site" : "Region"}</th><th class="num">Orders</th><th class="num">Revenue</th><th class="num">OTIF</th><th class="num">Fill</th>
      ${bySite ? "" : html`<th class="num">Touchless</th><th class="num">First-pass</th><th class="num">→ D365 min</th>`}
      <th class="num">Exceptions</th>${bySite ? "" : html`<th class="num">At risk</th>`}</tr></thead>
    <tbody>${rows.map(r => html`<tr class="clickable" tabindex="0" data-${key}="${r[key]}" data-region="${r.region || ""}" title="Drill into ${r.siteName || r.regionName || r[key]}">
      <td><strong>${bySite ? (r.siteName || siteName(r.site)) : (r.regionName || regionName(r.region))}</strong>${bySite ? html`<div class="small muted mono">${r.site}</div>` : r.crossRegionOrders ? html`<div class="small muted">${fmtNum(r.crossRegionOrders)} cross-region</div>` : ""}</td>
      <td class="num">${fmtNum(r.orders)}</td><td class="num mono">${money(r.revenue)}</td>
      <td class="num">${rate(r.otifRate)}</td><td class="num">${rate(r.fillRate, 0.98)}</td>
      ${bySite ? "" : html`<td class="num">${rate(r.touchlessRate, 0.8)}</td><td class="num">${rate(r.firstPassValidationRate, 0.9)}</td><td class="num">${r.avgReceiptToErpMinutes == null ? "–" : fmtNum(r.avgReceiptToErpMinutes, 1)}</td>`}
      <td class="num">${Number(r.openExceptions) ? html`<span class="pill bad">${fmtNum(r.openExceptions)}</span>` : html`<span class="pill good">0</span>`}</td>
      ${bySite ? "" : html`<td class="num">${fmtNum(r.ordersAtRisk)}${r.valueAtRisk ? html`<div class="small muted">${money(r.valueAtRisk)}</div>` : ""}</td>`}</tr>`)}</tbody></table></div>`);
  host.querySelectorAll("tr.clickable").forEach(tr => {
    const go = () => (bySite ? drillTo({ region: tr.dataset.region || s.region, site: tr.dataset.site }) : drillTo({ region: tr.dataset.region }));
    tr.addEventListener("click", go);
    tr.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
  });
}

/* ---------- orders at risk ---------- */
const REASON_LABEL = { ACTION_REQUIRED: "Needs action", LATE_PROMISE: "Promise after requested date", SHIP_DATE_MISSED: "Ship date missed",
  DELIVERY_OVERDUE: "Delivery overdue", BACKORDER: "Backorder", ACK_855_LATE: "855 ack late (chargeback risk)" };

async function loadRisk(el) {
  const host = $("#risk", el);
  try {
    const r = await api.process("/v1/orders/at-risk", { query: { ...scopeQuery(), limit: 15 } });
    const list = items(r);
    const sev = r.bySeverity || {};
    $("#riskTotals", el).textContent = r.total != null ? `${fmtNum(r.total)} orders · ${money(r.valueAtRisk)} · ${fmtNum(sev.HIGH || 0)} high` : "";
    if (!list.length) { setHTML(host, html`<div class="empty">Nothing at risk in this scope 🎉</div>`); return; }
    setHTML(host, html`<ul class="risk-list">${list.slice(0, 15).map(o => html`<li>
      <div class="row" style="gap:8px;justify-content:space-between;flex-wrap:wrap">
        <div><span class="badge ${o.severity === "HIGH" ? "sev-ERROR" : "sev-WARNING"}">${o.severity || "MEDIUM"}</span>
          <a class="mono" href="#/orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a>
          <span class="small muted">${o.customerName || o.customerId || ""} · PO ${o.customerPo || "–"}</span></div>
        <div class="small"><strong>${money(o.totalAmount)}</strong> · ${o.regionName || regionName(o.region) || "–"}${o.shipFromWarehouse ? ` · ${o.shipFromWarehouse}` : ""}</div>
      </div>
      <div class="small">${statusBadge(o.status, o.statusCategory)} ${(o.reasons || []).map(x => html`<span class="tag" title="${x.message || ""}">${REASON_LABEL[x.code] || humanize(x.code)}</span> `)}
        ${o.requestedDeliveryDate ? html`<span class="muted">Due ${fmtDate(o.requestedDeliveryDate)}</span>` : ""}</div>
      ${(o.reasons || [])[0] && o.reasons[0].message ? html`<div class="small muted">${o.reasons.map(x => x.message).join(" · ")}</div>` : ""}
    </li>`)}</ul>`);
  } catch (e) {
    setHTML(host, e.status === 404 ? html`<div class="empty">At-risk view not available on this backend</div>` : errorBox(e));
  }
}

/* ---------- most active customers + recent orders (one query, derived client-side) ---------- */
async function loadOrders(el) {
  const host = $("#recent", el);
  const cust = $("#customers", el);
  try {
    const r = await api.process("/v1/orders", { query: { ...scopeQuery(), fromDate: rangeFromDate(), page: 1, pageSize: 200, sort: "-receivedAt" } });
    const list = items(r);
    const total = r.total ?? list.length;
    // Most active customers in the selected range (from up to 200 most recent orders).
    const by = new Map();
    for (const o of list) {
      const id = o.customerId || o.customerName || "–";
      const c = by.get(id) || { id, name: o.customerName || o.customerId || "–", orders: 0, value: 0, open: 0, currency: o.currency };
      c.orders += 1; c.value += Number(o.totalAmount) || 0;
      if (!["CLOSED", "CANCELLED", "INVOICED", "DELIVERED"].includes(o.statusCategory)) c.open += 1;
      by.set(id, c);
    }
    const top = [...by.values()].sort((a, b) => b.orders - a.orders || b.value - a.value);
    setHTML($("#custHint", el), html`${rangeLabel()} · from ${fmtNum(list.length)} of ${fmtNum(total)} orders`);
    setHTML(cust, top.length ? limitedTable(html`<tr><th>Customer</th><th class="num">Orders</th><th class="num">Open</th><th class="num">Value</th></tr>`,
      top.map(c => html`<tr class="clickable" data-cust="${c.id}"><td><strong>${c.name}</strong><div class="small muted mono">${c.id}</div></td>
        <td class="num">${fmtNum(c.orders)}</td><td class="num">${fmtNum(c.open)}</td><td class="num mono">${money(c.value)}</td></tr>`), { limit: 8, cls: "dense" })
      : html`<div class="empty">No orders in ${rangeLabel().toLowerCase()}</div>`);
    wireLimited(cust);
    cust.querySelectorAll("tr[data-cust]").forEach(tr => tr.addEventListener("click", () => { location.hash = `#/orders?customerId=${encodeURIComponent(tr.dataset.cust)}`; }));

    const recent = list.slice(0, 15);
    if (!recent.length) { setHTML(host, html`<div class="empty">No orders in this scope and range${me() && me().canOperateOrders ? html` – <a href="#/new">create one</a>` : ""}.</div>`); return; }
    setHTML(host, limitedTable(html`<tr><th>Order</th><th>Status</th><th>Channel</th><th>Customer</th><th>PO</th><th>Region</th><th>Site</th><th>Received</th><th class="num">Total</th></tr>`,
      recent.map(o => html`<tr class="clickable" data-id="${o.orderId}">
        <td class="mono"><a href="#/orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a></td>
        <td>${statusBadge(o.status, o.statusCategory)}</td><td>${o.sourceChannel || ""}</td>
        <td>${o.customerName || o.customerId || ""}${o.accountScope === "NATIONAL" ? html` <span class="tag">National</span>` : ""}</td>
        <td class="mono small">${o.customerPo || "–"}</td>
        <td>${o.regionName || regionName(o.region) || "–"}${o.crossRegion ? html` <span class="tag" title="Shipped from outside the home region">cross-region</span>` : ""}</td>
        <td class="small mono">${o.shipFromWarehouse || "–"}</td>
        <td class="nowrap">${fmtDate(o.receivedAt)}</td><td class="num mono">${fmtMoney(o.totalAmount, o.currency)}</td></tr>`), { limit: 10 }));
    wireLimited(host);
    host.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", e => {
      if (e.target.closest("a")) return;
      location.hash = `#/orders/${encodeURIComponent(tr.dataset.id)}`;
    }));
  } catch (e) { setHTML(host, errorBox(e)); setHTML(cust, errorBox(e)); }
}
