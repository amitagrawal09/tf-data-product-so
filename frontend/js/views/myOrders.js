// My orders (customer portal): the signed-in customer's own orders (GET /exp/portal/v1/my/orders) and the
// customer-facing status with milestones (GET /exp/portal/v1/orders/{id}). Internal users with Orders.Read see all.
import { api, items } from "../api.js";
import { html, setHTML, $, errorBox, loading, fmtDate, fmtMoney, fmtNum, statusBadge, kpiCard, limitedTable, wireLimited, CATEGORIES, humanize } from "../ui.js";
import { me, isExternal } from "../session.js";
import { subtitle } from "../app.js";

export async function mount(el, id, qs) {
  if (id) return detail(el, decodeURIComponent(id));
  const params = new URLSearchParams(qs || "");
  const q = params.get("q") || "";
  const status = params.get("status") || "";
  const p = me();
  const sc = p.scope.customerIds;
  setHTML(el, html`
    <div class="page-head"><div><h1>My orders</h1><div class="sub">${subtitle()}</div></div>
      ${isExternal(p) ? html`<span class="badge rowscope" style="font-size:12px">Row-scoped to ${sc.length ? sc.join(", ") : "your account"}</span>` : html`<span class="small muted">Internal view: all customers</span>`}</div>
    <div id="tiles"></div>
    <form class="card section-gap" id="f"><div class="row" style="gap:8px">
      <input id="q" type="search" value="${q}" placeholder="Order id or your PO number" style="flex:1;min-width:180px" aria-label="Search my orders">
      <select id="status" aria-label="Status" style="width:auto"><option value="">All statuses</option>
        ${CATEGORIES.map(c => html`<option value="${c}" ${c === status ? "selected" : ""}>${humanize(c)}</option>`)}</select>
      <button class="btn primary" type="submit">Search</button></div></form>
    <div class="card section-gap"><div class="panel-head"><h2>Orders</h2><span class="panel-hint">Select an order to see its milestones, shipments and invoices</span></div>
      <div id="list">${loading()}</div></div>`);
  $("#f", el).addEventListener("submit", e => {
    e.preventDefault();
    const n = new URLSearchParams();
    if ($("#q", el).value.trim()) n.set("q", $("#q", el).value.trim());
    if ($("#status", el).value) n.set("status", $("#status", el).value);
    location.hash = `#/my-orders${n.toString() ? `?${n}` : ""}`;
  });
  const host = $("#list", el);
  try {
    const r = await api.exp("/portal/v1/my/orders", { query: { q, status, pageSize: 200 } });
    const list = items(r);
    const open = list.filter(o => !["CLOSED", "CANCELLED", "INVOICED", "DELIVERED"].includes(o.statusCategory));
    const attention = list.filter(o => o.statusCategory === "ACTION_REQUIRED");
    const value = list.reduce((a, o) => a + (Number(o.totalAmount) || 0), 0);
    setHTML($("#tiles", el), html`<div class="kpis">
      ${kpiCard({ label: "Orders", value: fmtNum(r.total ?? list.length), hint: q || status ? "matching your search" : "all time" })}
      ${kpiCard({ label: "Open", value: fmtNum(open.length), hint: "not yet delivered" })}
      ${kpiCard({ label: "Needs attention", value: fmtNum(attention.length), state: attention.length ? "breach" : "ok", hint: attention.length ? "Customer service is on it" : "Nothing blocked" })}
      ${kpiCard({ label: "Order value", value: fmtMoney(value, (list[0] || {}).currency || "USD"), hint: `${fmtNum(list.length)} orders shown` })}
    </div>`);
    setHTML(host, list.length ? limitedTable(html`<tr><th>Order</th><th>Your PO</th><th>Status</th><th>Ship to</th><th>Requested delivery</th><th>Received</th><th class="num">Total</th></tr>`,
      list.map(o => html`<tr class="clickable" tabindex="0" data-id="${o.orderId}">
        <td class="mono"><a href="#/my-orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a></td>
        <td class="mono small">${o.customerPo || "–"}</td><td>${statusBadge(o.status, o.statusCategory)}</td>
        <td class="small">${o.shipToName || o.shipToId || "–"}</td>
        <td class="nowrap">${fmtDate(o.requestedDeliveryDate)}</td><td class="nowrap small">${fmtDate(o.receivedAt)}</td>
        <td class="num mono">${fmtMoney(o.totalAmount, o.currency)}</td></tr>`), { limit: 10 })
      : html`<div class="empty">No orders found${q ? ` for “${q}”` : ""}.</div>`);
    wireLimited(host);
    host.querySelectorAll("tr[data-id]").forEach(tr => {
      const go = () => { location.hash = `#/my-orders/${encodeURIComponent(tr.dataset.id)}`; };
      tr.addEventListener("click", e => { if (!e.target.closest("a")) go(); });
      tr.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    });
  } catch (e) { setHTML(host, errorBox(e)); setHTML($("#tiles", el), ""); }
}

async function detail(el, id) {
  setHTML(el, html`<div class="page-head"><div><div class="crumb"><a href="#/my-orders">My orders</a> /</div><h1 class="mono" style="font-family:inherit">${id}</h1>
      <div class="sub" id="sub">${subtitle()}</div></div>
      <button class="btn" id="reload">Refresh</button></div><div id="body">${loading()}</div>`);
  $("#reload", el).addEventListener("click", () => detail(el, id));
  const host = $("#body", el);
  let v;
  try { v = await api.exp(`/portal/v1/orders/${encodeURIComponent(id)}`); }
  catch (e) { setHTML(host, errorBox(e)); return; }
  setHTML($("#sub", el), html`${v.customerName || v.customerId || ""} · PO <span class="mono">${v.customerPo || "–"}</span> · ${statusBadge(v.status, v.statusCategory)}`);
  const ms = v.milestones || [];
  const t = v.totals || {};
  setHTML(host, html`
    <div class="card"><div class="panel-head"><h2>Progress</h2>
      <span class="panel-hint">${v.nextExpected ? html`Next: <strong>${v.nextExpected.label}</strong>${v.nextExpected.by ? ` by ${fmtDate(v.nextExpected.by)}` : ""}` : v.statusDescription || ""}</span></div>
      <ol class="milestones">${ms.map(m => html`<li class="${m.status === "DONE" ? "done" : m.status === "CURRENT" ? "current" : ""}" title="${m.blockedReason || ""}">
        <div class="m-l">${m.label}</div><div class="m-at">${m.at ? fmtDate(m.at) : m.status === "CURRENT" ? (m.blocked ? "On hold" : "In progress") : m.status === "SKIPPED" ? "Skipped" : "Pending"}</div></li>`)}</ol>
      ${(v.openIssues || []).length ? html`<div class="notice section-gap"><strong>We are working on your order:</strong> ${v.openIssues.map(i => i.message).join(" · ")}</div>` : ""}
    </div>
    <div class="kpis section-gap">
      ${kpiCard({ label: "Requested delivery", value: html`<span style="font-size:20px">${fmtDate(v.requestedDeliveryDate)}</span>` })}
      ${kpiCard({ label: "Promised ship", value: html`<span style="font-size:20px">${fmtDate(v.promisedShipDate)}</span>` })}
      ${kpiCard({ label: "Ships from", value: html`<span style="font-size:18px">${v.shipFromSite || v.shipFromWarehouse || "–"}</span>`, hint: v.regionName || "" })}
      ${kpiCard({ label: "Order total", value: html`<span style="font-size:20px">${fmtMoney(t.grandTotal ?? t.total ?? t.netAmount, t.currency || "USD")}</span>` })}
    </div>
    <div class="card section-gap"><h2>Lines</h2>
      <div class="table-wrap"><table class="dense"><thead><tr><th class="num">#</th><th>Item</th><th>Description</th><th>UoM</th><th class="num">Ordered</th><th class="num">Confirmed</th><th class="num">Shipped</th><th class="num">Delivered</th><th>Status</th></tr></thead>
      <tbody>${(v.lines || []).map(l => html`<tr><td class="num">${l.lineNumber}</td><td class="mono small">${l.itemNumber || ""}${l.buyerItemNumber ? html`<div class="muted">${l.buyerItemNumber}</div>` : ""}</td>
        <td class="small">${l.description || ""}</td><td class="small">${l.uom || ""}</td>
        ${["ordered", "confirmed", "shipped", "delivered"].map(k => html`<td class="num">${fmtNum(Number(l[k] || 0))}</td>`)}<td class="small">${l.status ? humanize(l.status) : "–"}</td></tr>`)}</tbody></table></div></div>
    <div class="grid grid-2 section-gap">
      <div class="card"><h2>Shipments</h2>${(v.shipments || []).length ? html`<div class="table-wrap"><table class="dense"><thead><tr><th>Shipment</th><th>Carrier</th><th>Tracking</th><th>Shipped</th><th>ETA / delivered</th></tr></thead><tbody>
        ${v.shipments.map(s => html`<tr><td class="mono small">${s.shipmentId || s.asnNumber || "–"}</td><td class="small">${s.carrierName || s.carrierScac || "–"}</td>
          <td class="mono small">${s.trackingNumber || "–"}</td><td class="small">${fmtDate(s.shippedAt)}</td><td class="small">${fmtDate(s.deliveredAt || s.estimatedDeliveryAt)}</td></tr>`)}</tbody></table></div>`
        : html`<div class="empty">Not shipped yet</div>`}</div>
      <div class="card"><h2>Invoices</h2>${(v.invoices || []).length ? html`<div class="table-wrap"><table class="dense"><thead><tr><th>Invoice</th><th>Date</th><th>Due</th><th class="num">Amount</th><th>Status</th></tr></thead><tbody>
        ${v.invoices.map(i => html`<tr><td class="mono small">${i.invoiceId}</td><td class="small">${fmtDate(i.invoiceDate)}</td><td class="small">${fmtDate(i.dueDate)}</td>
          <td class="num mono">${fmtMoney(i.amount, i.currency)}</td><td class="small">${i.status || "–"}</td></tr>`)}</tbody></table></div>`
        : html`<div class="empty">No invoices yet</div>`}</div>
    </div>
    <div class="small muted section-gap">As of ${fmtDate(v.asOf)}</div>`);
}
