import { api, items } from "../api.js";
import { html, setHTML, $, fmtDate, statusBadge, sevBadge, errorBox, loading } from "../ui.js";

export async function mount(el) {
  setHTML(el, html`
    <div class="page-head"><div><h1>Exception queue</h1>
      <div class="sub">Orders held for customer service. No order is dropped – correct it and it re-enters at validation.</div></div>
      <button class="btn" id="refresh">Refresh</button></div>
    <div id="list">${loading()}</div>`);
  $("#refresh", el).addEventListener("click", () => load(el));
  await load(el);
}

async function load(el) {
  const host = $("#list", el);
  setHTML(host, loading());
  try {
    const list = items(await api.process("/v1/exceptions"));
    if (!list.length) { setHTML(host, html`<div class="card empty">The exception queue is empty.</div>`); return; }
    const byRule = {};
    list.forEach(o => (o.exceptions || []).forEach(x => { const k = x.ruleId || x.code || "?"; byRule[k] = (byRule[k] || 0) + 1; }));
    setHTML(host, html`
      <div class="tiles">
        <div class="tile"><div class="label">Orders in queue</div><div class="value">${list.length}</div></div>
        ${Object.entries(byRule).sort((a, b) => b[1] - a[1]).map(([k, n]) => html`<div class="tile"><div class="label">${k}</div><div class="value">${n}</div></div>`)}
      </div>
      <div class="table-wrap section-gap"><table>
        <thead><tr><th>Order</th><th>Status</th><th>Customer</th><th>PO</th><th>Exceptions</th><th>Received</th><th></th></tr></thead>
        <tbody>${list.map(o => html`<tr>
          <td class="mono"><a href="#/orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a><div class="small muted">${o.sourceChannel || ""}</div></td>
          <td>${statusBadge(o.status, o.statusCategory)}</td>
          <td>${o.customerName || ""}<div class="small muted">${o.customerId || ""}</div></td>
          <td>${o.customerPo || ""}</td>
          <td>${(o.exceptions || []).map(x => html`<div>${sevBadge(x.severity)} <span class="mono">${x.ruleId || x.code}</span>
            ${x.lineNumber ? html`<span class="small muted">line ${x.lineNumber}</span>` : ""} ${x.message || ""}</div>`)}</td>
          <td class="nowrap">${fmtDate(o.receivedAt)}</td>
          <td><a class="btn sm primary" href="#/orders/${encodeURIComponent(o.orderId)}">Resolve</a></td></tr>`)}</tbody></table></div>`);
  } catch (e) { setHTML(host, errorBox(e)); }
}
