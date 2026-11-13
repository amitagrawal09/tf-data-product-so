import { api, items } from "../api.js";
import { html, setHTML, $, fmtDate, fmtMoney, statusBadge, errorBox, loading, humanize, STATUSES, CATEGORIES, CHANNELS } from "../ui.js";

const FIELDS = ["status", "statusCategory", "channel", "customerId", "customerPo", "erpSalesOrderId", "q", "fromDate", "toDate", "hasExceptions", "sort", "page", "pageSize"];

export async function mount(el, qs) {
  const params = new URLSearchParams(qs || "");
  const f = Object.fromEntries(FIELDS.map(k => [k, params.get(k) || ""]));
  f.page = Number(f.page) || 1;
  f.pageSize = Number(f.pageSize) || 25;
  f.sort = f.sort || "-receivedAt";
  const opt = (list, sel) => list.map(v => html`<option value="${v}" ${v === sel ? "selected" : ""}>${humanize(v)}</option>`);

  setHTML(el, html`
    <div class="page-head"><div><h1>Orders</h1><div class="sub">Search the Order Status API (<span class="mono">GET /process/v1/orders</span>)</div></div>
      <a class="btn primary" href="#/new">New order</a></div>
    <form class="card" id="search">
      <div class="fields">
        <div class="field"><label for="q">Search</label><input id="q" value="${f.q}" placeholder="Order id, PO, customer…"></div>
        <div class="field"><label for="status">Status</label><select id="status"><option value="">Any</option>${opt(STATUSES, f.status)}</select></div>
        <div class="field"><label for="statusCategory">Category</label><select id="statusCategory"><option value="">Any</option>${opt(CATEGORIES, f.statusCategory)}</select></div>
        <div class="field"><label for="channel">Channel</label><select id="channel"><option value="">Any</option>${opt(CHANNELS, f.channel)}</select></div>
        <div class="field"><label for="customerId">Customer id</label><input id="customerId" value="${f.customerId}"></div>
        <div class="field"><label for="customerPo">Customer PO</label><input id="customerPo" value="${f.customerPo}"></div>
        <div class="field"><label for="erpSalesOrderId">D365 sales order</label><input id="erpSalesOrderId" value="${f.erpSalesOrderId}"></div>
        <div class="field"><label for="fromDate">Received from</label><input id="fromDate" type="date" value="${f.fromDate}"></div>
        <div class="field"><label for="toDate">Received to</label><input id="toDate" type="date" value="${f.toDate}"></div>
        <div class="field"><label for="hasExceptions">Exceptions</label><select id="hasExceptions">
          <option value="">Any</option><option value="true" ${f.hasExceptions === "true" ? "selected" : ""}>With open exceptions</option>
          <option value="false" ${f.hasExceptions === "false" ? "selected" : ""}>Without</option></select></div>
        <div class="field"><label for="sort">Sort</label><select id="sort">
          ${[["-receivedAt", "Newest first"], ["receivedAt", "Oldest first"], ["-updatedAt", "Recently updated"], ["requestedDeliveryDate", "Delivery date"], ["-totalAmount", "Largest total"]]
            .map(([v, l]) => html`<option value="${v}" ${v === f.sort ? "selected" : ""}>${l}</option>`)}</select></div>
        <div class="field"><label for="pageSize">Page size</label><select id="pageSize">${[10, 25, 50, 100].map(n => html`<option ${n === f.pageSize ? "selected" : ""}>${n}</option>`)}</select></div>
      </div>
      <div class="row section-gap"><button class="btn primary" type="submit">Search</button><button class="btn" type="button" id="reset">Reset</button></div>
    </form>
    <div id="results" class="section-gap">${loading()}</div>`);

  $("#search", el).addEventListener("submit", e => { e.preventDefault(); go(el, 1); });
  $("#reset", el).addEventListener("click", () => { location.hash = "#/orders"; });

  const query = {};
  for (const k of FIELDS) if (f[k] !== "" && f[k] !== undefined) query[k] = f[k];
  const host = $("#results", el);
  try {
    const r = await api.process("/v1/orders", { query });
    const list = items(r);
    const total = Number(r.total ?? r.totalCount ?? list.length);
    const pages = Math.max(1, Math.ceil(total / f.pageSize));
    if (!list.length) { setHTML(host, html`<div class="card empty">No orders match these filters.</div>`); return; }
    setHTML(host, html`
      <div class="table-wrap"><table>
        <thead><tr><th>Order</th><th>Status</th><th>Channel</th><th>Customer</th><th>PO</th><th>D365 SO</th>
          <th>Delivery</th><th>Received</th><th>Updated</th><th class="num">Total</th><th>Exc.</th><th>Touchless</th></tr></thead>
        <tbody>${list.map(o => html`<tr class="clickable" data-id="${o.orderId}">
          <td class="mono nowrap"><a href="#/orders/${encodeURIComponent(o.orderId)}">${o.orderId}</a>
            ${o.orderGroupId && o.orderGroupId !== o.orderId ? html`<div class="small muted">grp ${o.orderGroupId}</div>` : ""}</td>
          <td>${statusBadge(o.status, o.statusCategory)}</td>
          <td>${o.sourceChannel || ""}</td>
          <td>${o.customerName || ""}<div class="small muted">${o.customerId || ""}</div></td>
          <td>${o.customerPo || ""}</td><td class="mono">${o.erpSalesOrderId || "–"}</td>
          <td class="nowrap">${fmtDate(o.requestedDeliveryDate)}</td>
          <td class="nowrap">${fmtDate(o.receivedAt)}</td><td class="nowrap">${fmtDate(o.updatedAt)}</td>
          <td class="num">${fmtMoney(o.totalAmount, o.currency)}</td>
          <td>${o.openExceptionCount ? html`<span class="badge sev-ERROR">${o.openExceptionCount}</span>` : ""}</td>
          <td>${o.touchless === true ? html`<span class="badge ok">Yes</span>` : o.touchless === false ? html`<span class="badge">No</span>` : ""}</td>
        </tr>`)}</tbody></table></div>
      <div class="row section-gap">
        <span class="muted">${total} order(s) · page ${f.page} of ${pages}</span><span class="spacer"></span>
        <button class="btn sm" id="prev" ${f.page <= 1 ? "disabled" : ""}>← Prev</button>
        <button class="btn sm" id="next" ${f.page >= pages ? "disabled" : ""}>Next →</button>
      </div>`);
    host.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", e => {
      if (e.target.closest("a")) return;
      location.hash = `#/orders/${encodeURIComponent(tr.dataset.id)}`;
    }));
    $("#prev", host).addEventListener("click", () => go(el, f.page - 1));
    $("#next", host).addEventListener("click", () => go(el, f.page + 1));
  } catch (e) { setHTML(host, errorBox(e)); }
}

function go(el, page) {
  const p = new URLSearchParams();
  for (const k of FIELDS) {
    if (k === "page") continue;
    const node = $("#" + k, el);
    if (node && node.value) p.set(k, node.value);
  }
  if (page > 1) p.set("page", page);
  const qs = p.toString();
  location.hash = qs ? `#/orders?${qs}` : "#/orders";
}
