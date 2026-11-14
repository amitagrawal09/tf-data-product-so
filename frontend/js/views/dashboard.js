// Command center for the selected operating scope (National → Region → Site, all / national / regional accounts).
import { api, items, scope, scopeQuery } from "../api.js";
import { html, setHTML, $, fmtNum, fmtDate, fmtMoney, humanize, statusBadge, errorBox, loading, CATEGORIES } from "../ui.js";
import { scopePill, scopeLabel, drillTo, regionName, siteName } from "../scope.js";

let timer = null;

export async function mount(el) {
  const s = scope();
  const perfTitle = s.site ? "Site performance" : s.region ? `Site performance - ${regionName(s.region)}` : "Regional performance";
  setHTML(el, html`
    <div class="page-head">
      <div><h1>${s.region || s.site ? "Regional command center" : "National command center"}</h1>
        <div class="sub">Order-to-cash health for ${scopeLabel(s)} · ${scopePill()}</div></div>
      <div class="row">
        <label class="check"><input type="checkbox" id="autoRefresh"> Auto-refresh (10 s)</label>
        <button class="btn" id="refresh">Refresh</button>
        <a class="btn primary" href="#/new">New order</a>
      </div>
    </div>
    <div class="card"><h2>Service health</h2><div class="health" id="health">${loading()}</div></div>
    <div class="section-gap" id="kpis">${loading("Loading KPIs…")}</div>
    <div class="grid grid-2 section-gap">
      <div class="card"><div class="row"><h2 style="margin:0">${perfTitle}</h2><span class="spacer"></span>
        ${s.region || s.site ? html`<button class="btn sm" id="upScope">${s.site ? "↑ Whole region" : "↑ National"}</button>` : ""}</div>
        <div class="small muted">Select a row to drill into it.</div>
        <div id="perf" class="section-gap">${loading()}</div></div>
      <div class="card"><div class="row"><h2 style="margin:0">Orders at risk</h2><span class="spacer"></span><span id="riskTotals" class="small muted"></span></div>
        <div class="small muted">Open orders likely to miss the customer promise - act on these first.</div>
        <div id="risk" class="section-gap">${loading()}</div></div>
    </div>
    <div class="grid grid-3 section-gap">
      <div class="card"><h2>By status category</h2><div id="byCat">${loading()}</div></div>
      <div class="card"><h2>By status</h2><div id="byStatus">${loading()}</div></div>
      <div class="card"><h2>By channel</h2><div id="byChannel">${loading()}</div></div>
    </div>
    <div id="chanKpis"></div>
    <div class="card section-gap">
      <div class="row"><h2 style="margin:0">Recent orders</h2><span class="spacer"></span><a href="#/orders">All orders →</a></div>
      <div id="recent" class="section-gap">${loading()}</div>
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
  await Promise.all([loadHealth(el), loadSummary(el), loadKpis(el), loadRisk(el), loadRecent(el)]);
}

/* ---------- formatting ---------- */
function pct(v) { if (v === null || v === undefined || v === "") return "–"; const n = Number(v); if (isNaN(n)) return "–"; return `${fmtNum(n <= 1 ? n * 100 : n, 1)}%`; }
function rateClass(v) {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v) > 1 ? Number(v) / 100 : Number(v);
  return n >= 0.95 ? "rate-good" : n >= 0.9 ? "rate-warn" : "rate-bad";
}
const rate = v => html`<span class="rate ${rateClass(v)}">${pct(v)}</span>`;
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
      return html`<span title="${JSON.stringify(h)}"><span class="dot ${up ? "up" : "down"}"></span>${name} <span class="muted small">${st}${extra}${h && h.version ? " · v" + h.version : ""}</span></span>`;
    } catch (e) {
      return html`<span title="${e.message}"><span class="dot down"></span>${name} <span class="muted small">unreachable</span></span>`;
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
    const byStatus = toPairs(s.byStatus).sort((a, b) => b[1] - a[1]);
    const byChannel = toPairs(s.byChannel).sort((a, b) => b[1] - a[1]);
    setHTML($("#byCat", el), bars(byCat, "statusCategory"));
    setHTML($("#byStatus", el), bars(byStatus, "status"));
    setHTML($("#byChannel", el), bars(byChannel, "channel"));
  } catch (e) {
    ["#byCat", "#byStatus", "#byChannel"].forEach(id => { const n = $(id, el); if (n) setHTML(n, errorBox(e)); });
  }
}

/* ---------- KPIs + performance table ---------- */
const TILES = [
  ["perfectOrderRate", "Perfect order rate", v => pct(v), () => "North star: touchless + on time + in full", true],
  ["revenue", "Revenue", (v, k) => money(v), k => k.avgOrderValue != null ? `Avg order ${money(k.avgOrderValue)}` : ""],
  ["ordersTotal", "Orders", v => fmtNum(v), k => k.openExceptions != null ? `${fmtNum(k.openExceptions)} with open exceptions` : ""],
  ["otifRate", "OTIF", v => pct(v), k => (k.onTimeDeliveryRate != null || k.inFullRate != null) ? `On time ${pct(k.onTimeDeliveryRate)} · in full ${pct(k.inFullRate)}` : "On time, in full", true],
  ["fillRate", "Fill rate", v => pct(v), () => "Shipped ÷ ordered quantity", true],
  ["ordersAtRisk", "Orders at risk", v => fmtNum(v), k => k.valueAtRisk != null ? `${money(k.valueAtRisk)} at risk` : ""],
  ["touchlessRate", "Touchless rate", v => pct(v), () => "No manual touch to reach D365", true],
  ["firstPassValidationRate", "First-pass validation", v => pct(v), () => "All quality rules first time", true],
  ["avgReceiptToErpMinutes", "Receipt → D365", v => (v == null ? "–" : `${fmtNum(v, 1)} min`), k => k.avgReceiptToShipHours != null ? `Receipt → ship ${fmtNum(k.avgReceiptToShipHours, 1)} h` : ""],
  ["crossRegionSourcingRate", "Cross-region sourcing", v => pct(v), () => "Orders shipped outside the home region"],
  ["dataFreshnessSeconds", "Data freshness", v => (v == null ? "–" : `${fmtNum(v, 1)} s`), () => "Source event → data product"],
];

async function loadKpis(el) {
  const host = $("#kpis", el);
  try {
    const k = await api.process("/v1/metrics/kpis", { query: scopeQuery() });
    const tiles = TILES.filter(([key]) => key in (k || {})).map(([key, label, fmt, hint, isRate]) => html`
      <div class="tile${isRate ? " " + rateClass(k[key]) : ""}"><div class="label">${label}</div>
        <div class="value">${fmt(k[key], k)}</div><div class="hint">${hint(k) || ""}</div></div>`);
    setHTML(host, html`<div class="tiles">${tiles}</div>`);
    renderPerf(el, k);
    const chan = items(k && k.byChannel);
    setHTML($("#chanKpis", el), chan.length ? html`
      <div class="card section-gap"><h2>KPIs by channel</h2><div class="table-wrap"><table>
        <thead><tr>${Object.keys(chan[0]).map(c => html`<th>${humanize(c)}</th>`)}</tr></thead>
        <tbody>${chan.map(r => html`<tr>${Object.entries(r).map(([c, v]) => html`<td class="${typeof v === "number" ? "num" : ""}">${typeof v === "number" ? (/rate/i.test(c) ? pct(v) : fmtNum(v, 1)) : v ?? "–"}</td>`)}</tr>`)}</tbody>
      </table></div></div>` : "");
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
      <td class="num">${fmtNum(r.orders)}</td><td class="num">${money(r.revenue)}</td>
      <td class="num">${rate(r.otifRate)}</td><td class="num">${rate(r.fillRate)}</td>
      ${bySite ? "" : html`<td class="num">${pct(r.touchlessRate)}</td><td class="num">${pct(r.firstPassValidationRate)}</td><td class="num">${r.avgReceiptToErpMinutes == null ? "–" : fmtNum(r.avgReceiptToErpMinutes, 1)}</td>`}
      <td class="num">${fmtNum(r.openExceptions)}</td>
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

/* ---------- recent orders ---------- */
async function loadRecent(el) {
  const host = $("#recent", el);
  try {
    const r = await api.process("/v1/orders", { query: { ...scopeQuery(), page: 1, pageSize: 10, sort: "-receivedAt" } });
    const list = items(r);
    if (!list.length) { setHTML(host, html`<div class="empty">No orders in this scope – <a href="#/new">create one</a>.</div>`); return; }
    setHTML(host, html`<div class="table-wrap"><table><thead><tr>
      <th>Order</th><th>Status</th><th>Channel</th><th>Customer</th><th>Region</th><th>Site</th><th>Received</th><th class="num">Total</th></tr></thead>
      <tbody>${list.map(o => html`<tr class="clickable" data-id="${o.orderId}">
        <td class="mono"><a href="#/orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a></td>
        <td>${statusBadge(o.status, o.statusCategory)}</td><td>${o.sourceChannel || ""}</td>
        <td>${o.customerName || o.customerId || ""}${o.accountScope === "NATIONAL" ? html` <span class="tag">National</span>` : ""}</td>
        <td>${o.regionName || regionName(o.region) || "–"}${o.crossRegion ? html` <span class="tag" title="Shipped from outside the home region">cross-region</span>` : ""}</td>
        <td class="small mono">${o.shipFromWarehouse || "–"}</td>
        <td class="nowrap">${fmtDate(o.receivedAt)}</td><td class="num">${fmtMoney(o.totalAmount, o.currency)}</td></tr>`)}</tbody></table></div>`);
    host.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", e => {
      if (e.target.closest("a")) return;
      location.hash = `#/orders/${encodeURIComponent(tr.dataset.id)}`;
    }));
  } catch (e) { setHTML(host, errorBox(e)); }
}
