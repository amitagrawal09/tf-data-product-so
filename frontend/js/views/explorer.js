import { request } from "../api.js";
import { html, setHTML, $, toast, json, x12, esc } from "../ui.js";

// Presets for every Order Status API route (Process API) plus the key channel and system routes.
const PRESETS = [
  ["Order Status API", [
    ["GET", "/process/v1/orders?page=1&pageSize=10"],
    ["GET", "/process/v1/orders/{orderId}"],
    ["GET", "/process/v1/orders/{orderId}/status"],
    ["GET", "/process/v1/orders/{orderId}/history"],
    ["GET", "/process/v1/orders/{orderId}/documents"],
    ["GET", "/process/v1/orders/{orderId}/related"],
    ["GET", "/process/v1/order-groups/{orderGroupId}/history"],
    ["GET", "/process/v1/orders/by-po/{customerPo}?customerId={customerId}"],
    ["PATCH", "/process/v1/orders/{orderId}", { changes: { requestedDeliveryDate: "2026-12-01" }, reason: "Customer request", actor: "explorer" }],
    ["POST", "/process/v1/orders/{orderId}/resubmit", { actor: "explorer", comment: "Resubmit" }],
    ["POST", "/process/v1/orders/{orderId}/hold", { reason: "Customer requested hold", actor: "explorer" }],
    ["POST", "/process/v1/orders/{orderId}/release-hold", { reason: "Released", actor: "explorer" }],
    ["POST", "/process/v1/orders/{orderId}/cancel", { reasonCode: "CUSTOMER_REQUEST", actor: "explorer" }],
    ["GET", "/process/v1/exceptions"],
    ["GET", "/process/v1/status/summary"],
    ["GET", "/process/v1/metrics/kpis"],
    ["GET", "/process/v1/reference/statuses"],
    ["GET", "/process/v1/subscriptions"],
    ["POST", "/process/v1/events", { eventId: "evt-explorer-1", orderId: "{orderId}", eventType: "SHIPPED", occurredAt: new Date().toISOString(), sourceSystem: "WMS" }],
  ]],
  ["Experience APIs", [
    ["GET", "/exp/portal/v1/catalog/customers"],
    ["GET", "/exp/portal/v1/catalog/items?customerId=C-1003"],
    ["GET", "/exp/portal/v1/orders/{orderId}"],
    ["GET", "/exp/portal/v1/customers/{customerId}/orders"],
    ["GET", "/exp/partner/v1/orders/{orderId}/status"],
    ["GET", "/exp/edi/v1/orders/{orderId}/documents"],
  ]],
  ["System APIs", [
    ["GET", "/system/health"],
    ["GET", "/system/v1/masterdata/customers"],
    ["GET", "/system/v1/masterdata/items"],
    ["GET", "/system/v1/masterdata/price-agreements?customerId=C-1001"],
    ["GET", "/system/v1/sim/config"],
    ["PUT", "/system/v1/sim/config", { autoProgress: false, stepSeconds: 5 }],
    ["POST", "/system/v1/sim/orders/{orderId}/advance", { to: "RELEASED" }],
    ["GET", "/system/v1/lakehouse/files"],
  ]],
];

export async function mount(el) {
  setHTML(el, html`
    <div class="page-head"><div><h1>API explorer</h1><div class="sub">Send raw requests through the same gateway the console uses. Placeholders like <span class="mono">{orderId}</span> are filled from the variables below.</div></div></div>
    <div class="grid grid-2">
      <div class="card">
        <h2>Request</h2>
        <div class="fields">
          <div class="field"><label for="v-orderId">{orderId}</label><input id="v-orderId" placeholder="SO-…"></div>
          <div class="field"><label for="v-orderGroupId">{orderGroupId}</label><input id="v-orderGroupId"></div>
          <div class="field"><label for="v-customerId">{customerId}</label><input id="v-customerId" value="C-1001"></div>
          <div class="field"><label for="v-customerPo">{customerPo}</label><input id="v-customerPo"></div>
        </div>
        <div class="row section-gap">
          <select id="method" style="width:110px" aria-label="Method">${["GET", "POST", "PUT", "PATCH", "DELETE"].map(m => html`<option>${m}</option>`)}</select>
          <input id="path" style="flex:1;min-width:200px" value="/process/v1/orders?page=1&pageSize=10" aria-label="Path (relative to API base)">
        </div>
        <div class="field section-gap"><label for="hdrs">Extra headers (JSON)</label><textarea id="hdrs" rows="3">{}</textarea></div>
        <div class="field section-gap"><label for="body">Body (JSON or text)</label><textarea id="body" rows="8"></textarea></div>
        <div class="row section-gap"><button class="btn primary" id="send">Send</button><span class="muted small" id="fullUrl"></span></div>
        <h3 class="section-gap">Presets</h3>
        ${PRESETS.map(([group, list], gi) => html`<div class="section-gap"><div class="small muted" style="font-weight:600">${group}</div>
          <div class="row" style="margin-top:4px">${list.map((p, i) => html`<button class="btn sm" data-preset="${gi}:${i}" title="${p[0]} ${p[1]}"><span class="mono">${p[0]}</span>&nbsp;${p[1].replace(/^\/(process|exp|system)/, "").split("?")[0]}</button>`)}</div></div>`)}
      </div>
      <div class="card"><h2>Response</h2><div id="resp" class="muted">No request sent yet.</div></div>
    </div>`);

  el.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", () => {
    const [gi, i] = b.dataset.preset.split(":").map(Number);
    const [m, p, body] = PRESETS[gi][1][i];
    $("#method", el).value = m;
    $("#path", el).value = p;
    $("#body", el).value = body ? JSON.stringify(body, null, 2) : "";
  }));
  $("#send", el).addEventListener("click", () => send(el));
}

function fill(el, s, encode = true) {
  return s.replace(/\{(orderId|orderGroupId|customerId|customerPo)\}/g, (m, k) => {
    const v = $(`#v-${k}`, el).value.trim();
    return v ? (encode ? encodeURIComponent(v) : v) : m;
  });
}

async function send(el) {
  const method = $("#method", el).value;
  const path = fill(el, $("#path", el).value.trim());
  let headers = {};
  try { headers = JSON.parse($("#hdrs", el).value || "{}"); } catch { toast("Headers must be JSON", "error"); return; }
  let body = $("#body", el).value.trim();
  if (body) {
    body = fill(el, body, false);
    try { body = JSON.parse(body); } catch { /* send as text */ }
  } else body = undefined;
  const resp = $("#resp", el);
  resp.innerHTML = '<span class="spinner"></span> Sending…';
  const r = await request("", path, { method, body: method === "GET" ? undefined : body, headers, raw: true, idempotent: method === "POST" });
  $("#fullUrl", el).textContent = r.url || "";
  const hdrs = Object.entries(r.headers || {}).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join("\n");
  const content = typeof r.data === "string" ? x12(r.data) : json(r.data);
  setHTML(resp, html`<div class="row"><span class="badge ${r.ok ? "ok" : "sev-ERROR"}">${r.status || "ERR"}</span>
      <span class="muted small">${r.ms} ms · correlation <span class="mono">${r.correlationId}</span></span></div>
    <details class="section-gap"><summary>Response headers</summary><pre class="json">${{ __raw: true, value: hdrs }}</pre></details>
    <div class="section-gap">${content}</div>`);
  // Remember ids for chaining calls
  const d = r.data && typeof r.data === "object" ? r.data : {};
  const id = d.orderId || (Array.isArray(d.items) && d.items[0] && d.items[0].orderId);
  if (id && !$("#v-orderId", el).value) $("#v-orderId", el).value = id;
  if (d.orderGroupId && !$("#v-orderGroupId", el).value) $("#v-orderGroupId", el).value = d.orderGroupId;
}
