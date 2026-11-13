import { api, items } from "../api.js";
import { html, setHTML, $, toast, errorBox, loading, fmtDate, json } from "../ui.js";

const EVENT_TYPES = ["ORDER_RECEIVED", "VALIDATION_FAILED", "ORDER_PUBLISHED", "ERP_ACCEPTED", "ERP_REJECTED", "CREDIT_HOLD_PLACED",
  "RELEASED_TO_WAREHOUSE", "SHIPPED", "DELIVERED", "INVOICED", "CLOSED", "CANCELLED"];

export async function mount(el) {
  setHTML(el, html`
    <div class="page-head"><div><h1>Status webhooks</h1>
      <div class="sub">Push order status changes to partner systems. Deliveries are signed with HMAC-SHA256 in <span class="mono">x-tfso-signature</span>.</div></div></div>
    <form class="card" id="subForm">
      <h2>New subscription</h2>
      <div class="fields">
        <div class="field" style="grid-column: span 2"><label for="cb">Callback URL</label><input id="cb" type="url" required placeholder="https://partner.example.com/hooks/order-status"></div>
        <div class="field"><label for="cust">Customer id (optional filter)</label><input id="cust" placeholder="C-1001"></div>
      </div>
      <fieldset class="section-gap" style="border:0;padding:0;margin:0"><legend class="small muted" style="font-weight:600">Event types (none = all)</legend>
        <div class="row section-gap">${EVENT_TYPES.map(t => html`<label class="check"><input type="checkbox" name="et" value="${t}"> ${t}</label>`)}</div></fieldset>
      <div class="row section-gap"><button class="btn primary" type="submit" id="create">Create subscription</button></div>
      <div id="created" class="section-gap"></div>
    </form>
    <div class="card"><div class="row"><h2 style="margin:0">Subscriptions</h2><span class="spacer"></span><button class="btn sm" id="refresh">Refresh</button></div>
      <div id="list" class="section-gap">${loading()}</div></div>`);
  $("#refresh", el).addEventListener("click", () => load(el));
  $("#subForm", el).addEventListener("submit", async e => {
    e.preventDefault();
    const body = { callbackUrl: $("#cb", el).value.trim(), eventTypes: Array.from(el.querySelectorAll("input[name=et]:checked")).map(i => i.value) };
    const cust = $("#cust", el).value.trim();
    if (cust) body.customerId = cust;
    const btn = $("#create", el);
    btn.disabled = true;
    try {
      const r = await api.process("/v1/subscriptions", { body });
      setHTML($("#created", el), html`<div class="alert ok">Subscription <span class="mono">${r.subscriptionId || r.id}</span> created.
        ${r.secret ? html`<div>Signing secret (shown once): <span class="mono">${r.secret}</span></div>` : ""}</div>`);
      toast("Subscription created", "ok");
      await load(el);
    } catch (err) { setHTML($("#created", el), errorBox(err)); }
    finally { btn.disabled = false; }
  });
  await load(el);
}

async function load(el) {
  const host = $("#list", el);
  try {
    const list = items(await api.process("/v1/subscriptions"));
    if (!list.length) { setHTML(host, html`<div class="empty">No subscriptions</div>`); return; }
    setHTML(host, html`<div class="table-wrap"><table><thead><tr><th>Id</th><th>Callback</th><th>Events</th><th>Customer</th><th>Active</th><th>Created</th><th></th></tr></thead>
      <tbody>${list.map(s => html`<tr><td class="mono">${s.subscriptionId || s.id}</td><td class="mono" style="overflow-wrap:anywhere">${s.callbackUrl}</td>
        <td class="small">${(s.eventTypes || []).join(", ") || "All"}</td><td>${s.customerId || "–"}</td>
        <td>${s.active === false ? "No" : "Yes"}</td><td class="nowrap">${fmtDate(s.createdAt)}</td>
        <td><button class="btn sm danger" data-del="${s.subscriptionId || s.id}">Delete</button>
          ${s.lastDelivery ? html`<details><summary>Last delivery</summary>${json(s.lastDelivery)}</details>` : ""}</td></tr>`)}</tbody></table></div>`);
    host.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
      if (!confirm(`Delete subscription ${b.dataset.del}?`)) return;
      try { await api.process(`/v1/subscriptions/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); toast("Deleted", "ok"); load(el); }
      catch (err) { toast(err.message, "error"); }
    }));
  } catch (e) { setHTML(host, errorBox(e)); }
}
