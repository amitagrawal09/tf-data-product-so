import { api, items } from "../api.js";
import { html, setHTML, $, fmtNum, fmtDate, fmtMoney, humanize, statusBadge, errorBox, loading, CATEGORIES } from "../ui.js";

let timer = null;

export async function mount(el) {
  setHTML(el, html`
    <div class="page-head">
      <div><h1>Dashboard</h1><div class="sub">Live view of the Sales Order data product</div></div>
      <div class="row">
        <label class="check"><input type="checkbox" id="autoRefresh"> Auto-refresh (10 s)</label>
        <button class="btn" id="refresh">Refresh</button>
        <a class="btn primary" href="#/new">New order</a>
      </div>
    </div>
    <div class="card"><h2>Service health</h2><div class="health" id="health">${loading()}</div></div>
    <div class="section-gap" id="kpis">${loading("Loading KPIs…")}</div>
    <div class="grid grid-3 section-gap">
      <div class="card"><h2>By status category</h2><div id="byCat">${loading()}</div></div>
      <div class="card"><h2>By status</h2><div id="byStatus">${loading()}</div></div>
      <div class="card"><h2>By channel</h2><div id="byChannel">${loading()}</div></div>
    </div>
    <div class="card section-gap">
      <div class="row"><h2 style="margin:0">Recent orders</h2><span class="spacer"></span><a href="#/orders">All orders →</a></div>
      <div id="recent" class="section-gap">${loading()}</div>
    </div>`);
  $("#refresh", el).addEventListener("click", () => load(el));
  $("#autoRefresh", el).addEventListener("change", e => {
    clearInterval(timer);
    if (e.target.checked) timer = setInterval(() => load(el), 10000);
  });
  await load(el);
}

export function unmount() { clearInterval(timer); timer = null; }

async function load(el) {
  await Promise.all([loadHealth(el), loadSummary(el), loadKpis(el), loadRecent(el)]);
}

async function loadHealth(el) {
  const layers = [["exp", "Experience API"], ["process", "Process API"], ["system", "System API"]];
  const results = await Promise.all(layers.map(async ([l, name]) => {
    try {
      const h = await api[l]("/health");
      const st = String((h && h.status) || "ok").toLowerCase();
      const up = ["ok", "up", "healthy", "pass"].includes(st);
      const extra = h && (h.d365Mode || h.adapters) ? ` · ${h.d365Mode ? "D365 " + h.d365Mode : ""}` : "";
      return html`<span title="${JSON.stringify(h)}"><span class="dot ${up ? "up" : "down"}"></span>${name} <span class="muted small">${st}${extra}${h && h.version ? " · v" + h.version : ""}</span></span>`;
    } catch (e) {
      return html`<span title="${e.message}"><span class="dot down"></span>${name} <span class="muted small">unreachable</span></span>`;
    }
  }));
  const host = $("#health", el);
  if (host) setHTML(host, html`${results}`);
}

function toPairs(v) {
  // Accept {A: 3, B: 4} or [{key|status|category|channel, count}]
  if (!v) return [];
  if (Array.isArray(v)) return v.map(x => [x.key ?? x.status ?? x.statusCategory ?? x.category ?? x.channel ?? x.sourceChannel ?? x.name, Number(x.count ?? x.orders ?? x.value ?? 0)]);
  if (typeof v === "object") return Object.entries(v).map(([k, n]) => [k, Number(typeof n === "object" ? n.count ?? 0 : n)]);
  return [];
}

function bars(pairs, linkKey) {
  if (!pairs.length) return html`<div class="empty">No orders yet</div>`;
  const max = Math.max(1, ...pairs.map(p => p[1]));
  return html`<div class="bars">${pairs.map(([k, n]) => html`
    <div class="bar"><a href="#/orders?${linkKey}=${encodeURIComponent(k)}" title="${k}">${humanize(k)}</a>
      <div class="track"><div class="fill" style="width:${(n / max) * 100}%"></div></div><span class="n">${fmtNum(n)}</span></div>`)}</div>`;
}

async function loadSummary(el) {
  try {
    const s = await api.process("/v1/status/summary");
    const byCat = toPairs(s.byStatusCategory || s.byCategory || s.statusCategories)
      .sort((a, b) => CATEGORIES.indexOf(a[0]) - CATEGORIES.indexOf(b[0]));
    const byStatus = toPairs(s.byStatus || s.statuses).sort((a, b) => b[1] - a[1]);
    const byChannel = toPairs(s.byChannel || s.bySourceChannel || s.channels).sort((a, b) => b[1] - a[1]);
    setHTML($("#byCat", el), bars(byCat, "statusCategory"));
    setHTML($("#byStatus", el), bars(byStatus, "status"));
    setHTML($("#byChannel", el), bars(byChannel, "channel"));
  } catch (e) {
    ["#byCat", "#byStatus", "#byChannel"].forEach(id => { const n = $(id, el); if (n) setHTML(n, errorBox(e)); });
  }
}

const KPI_META = {
  ordersTotal: ["Orders", v => fmtNum(v)],
  touchlessRate: ["Touchless rate", pct],
  firstPassValidationRate: ["First-pass validation", pct],
  avgReceiptToErpMinutes: ["Receipt → D365 (avg)", v => `${fmtNum(v, 1)} min`],
  openExceptions: ["Open exceptions", v => fmtNum(v)],
  dataFreshnessSeconds: ["Data freshness", v => `${fmtNum(v, 1)} s`],
};
function pct(v) { const n = Number(v); if (isNaN(n)) return "–"; return `${fmtNum(n <= 1 ? n * 100 : n, 1)}%`; }

async function loadKpis(el) {
  const host = $("#kpis", el);
  try {
    const k = await api.process("/v1/metrics/kpis");
    const tiles = [];
    for (const [key, val] of Object.entries(k || {})) {
      if (typeof val !== "number") continue;
      const [label, fmt] = KPI_META[key] || [humanize(key), v => (/rate|pct|percent/i.test(key) ? pct(v) : fmtNum(v, 2))];
      tiles.push({ key, html: html`<div class="tile"><div class="label">${label}</div><div class="value">${fmt(val)}</div></div>` });
    }
    const order = Object.keys(KPI_META);
    tiles.sort((a, b) => (order.indexOf(a.key) + 1 || 99) - (order.indexOf(b.key) + 1 || 99));
    const chan = items(k && (k.byChannel || k.channels));
    const chanTable = chan.length ? html`
      <div class="card section-gap"><h2>KPIs by channel</h2><div class="table-wrap"><table>
        <thead><tr>${Object.keys(chan[0]).map(c => html`<th>${humanize(c)}</th>`)}</tr></thead>
        <tbody>${chan.map(r => html`<tr>${Object.entries(r).map(([c, v]) => html`<td class="${typeof v === "number" ? "num" : ""}">${typeof v === "number" ? (/rate/i.test(c) ? pct(v) : fmtNum(v, 1)) : v}</td>`)}</tr>`)}</tbody>
      </table></div></div>` : "";
    setHTML(host, html`<div class="tiles">${tiles.map(t => t.html)}</div>${chanTable}`);
  } catch (e) { setHTML(host, errorBox(e)); }
}

async function loadRecent(el) {
  const host = $("#recent", el);
  try {
    const r = await api.process("/v1/orders", { query: { page: 1, pageSize: 10, sort: "-receivedAt" } });
    const list = items(r);
    if (!list.length) { setHTML(host, html`<div class="empty">No orders yet – <a href="#/new">create one</a>.</div>`); return; }
    setHTML(host, html`<div class="table-wrap"><table><thead><tr>
      <th>Order</th><th>Status</th><th>Channel</th><th>Customer</th><th>PO</th><th>Received</th><th class="num">Total</th></tr></thead>
      <tbody>${list.map(o => html`<tr class="clickable" data-id="${o.orderId}">
        <td class="mono"><a href="#/orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a></td>
        <td>${statusBadge(o.status, o.statusCategory)}</td><td>${o.sourceChannel || ""}</td>
        <td>${o.customerName || o.customerId || ""}</td><td>${o.customerPo || ""}</td>
        <td class="nowrap">${fmtDate(o.receivedAt)}</td><td class="num">${fmtMoney(o.totalAmount, o.currency)}</td></tr>`)}</tbody></table></div>`);
    host.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", e => {
      if (e.target.closest("a")) return;
      location.hash = `#/orders/${encodeURIComponent(tr.dataset.id)}`;
    }));
  } catch (e) { setHTML(host, errorBox(e)); }
}
