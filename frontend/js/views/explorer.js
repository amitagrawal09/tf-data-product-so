import { request, callLog, onCall, clearCallLog, toCurl } from "../api.js";
import { html, raw, setHTML, $, $$, toast, json, x12, esc, sleep } from "../ui.js";

// Presets for every Order Status API route (Process API) plus the key channel and system routes.
// [method, path, body?, description]
const PRESETS = [
  ["Order Status API (Process)", [
    ["GET", "/process/v1/orders?page=1&pageSize=10", null, "Search orders (filters: status, statusCategory, customerId, channel, customerPo, fromDate, toDate, hasExceptions, q, sort)"],
    ["GET", "/process/v1/orders/{orderId}", null, "Full canonical sales order (CDM v1) including the complete orderHistory; ETag = version"],
    ["GET", "/process/v1/orders/{orderId}/status", null, "Status view: status, category, milestones, line quantities, shipments, invoices, next expected"],
    ["GET", "/process/v1/orders/{orderId}/history", null, "Ordered lifecycle history of the order (sequence, tier, source system, actor, diffs)"],
    ["GET", "/process/v1/orders/{orderId}/documents", null, "Inbound/outbound documents: X12 850/997/855/856/810, e-mail, JSON"],
    ["GET", "/process/v1/orders/{orderId}/related", null, "Orders in the same order group"],
    ["GET", "/process/v1/order-groups/{orderGroupId}/history", null, "Merged lifecycle history of a group of orders"],
    ["GET", "/process/v1/orders/by-po/{customerPo}?customerId={customerId}", null, "Find orders by customer PO"],
    ["PATCH", "/process/v1/orders/{orderId}", { changes: { requestedDeliveryDate: "2026-12-01" }, reason: "Customer request", actor: "explorer" }, "Correct an order in EXCEPTION / ERP_REJECTED / ON_HOLD. Needs If-Match: \"<version>\" in extra headers"],
    ["POST", "/process/v1/orders/{orderId}/resubmit", { actor: "explorer", comment: "Resubmit" }, "Re-enter a corrected order at validation"],
    ["POST", "/process/v1/orders/{orderId}/hold", { reason: "Customer requested hold", actor: "explorer" }, "Place a manual hold"],
    ["POST", "/process/v1/orders/{orderId}/release-hold", { reason: "Released", actor: "explorer" }, "Release a manual hold"],
    ["POST", "/process/v1/orders/{orderId}/cancel", { reasonCode: "CUSTOMER_REQUEST", actor: "explorer" }, "Cancel (only before shipping)"],
    ["GET", "/process/v1/exceptions", null, "Exception queue"],
    ["GET", "/process/v1/status/summary", null, "Counts by status, category and channel"],
    ["GET", "/process/v1/metrics/kpis", null, "Touchless rate, first-pass validation, cycle times, freshness, ROI"],
    ["GET", "/process/v1/reference/statuses", null, "State machine: statuses, categories, allowed transitions"],
    ["GET", "/process/v1/subscriptions", null, "Webhook subscriptions"],
    ["POST", "/process/v1/events", { eventId: "evt-explorer-1", orderId: "{orderId}", eventType: "SHIPPED", occurredAt: new Date().toISOString(), sourceSystem: "WMS" }, "Inject a status event as if from D365/WMS/TMS"],
  ]],
  ["Experience APIs", [
    ["GET", "/exp/portal/v1/catalog/customers", null, "Customers for the portal"],
    ["GET", "/exp/portal/v1/catalog/items?customerId=C-1003", null, "Items with contract price for a customer"],
    ["POST", "/exp/portal/v1/orders", { channel: "PORTAL", customerId: "C-1001", shipToId: "ST-1001-01", customerPo: "EXPLORER-001", requestedDeliveryDate: "{deliveryDate}", lines: [{ itemNumber: "TF-10001", quantity: 10, uom: "CA" }] }, "Submit a portal order (returns OrderAccepted)"],
    ["GET", "/exp/portal/v1/orders/{orderId}", null, "Customer-facing status view"],
    ["GET", "/exp/portal/v1/customers/{customerId}/orders", null, "A customer's orders"],
    ["GET", "/exp/partner/v1/orders/{orderId}/status", null, "Partner status view"],
    ["GET", "/exp/edi/v1/orders/{orderId}/documents", null, "EDI documents for an order"],
  ]],
  ["System APIs", [
    ["GET", "/system/health", null, "Adapter modes and outbox state"],
    ["GET", "/system/v1/masterdata/customers", null, "Customer master"],
    ["GET", "/system/v1/masterdata/items", null, "Item master"],
    ["GET", "/system/v1/masterdata/price-agreements?customerId=C-1001", null, "Trade agreements"],
    ["GET", "/system/v1/sim/config", null, "Simulator settings"],
    ["PUT", "/system/v1/sim/config", { autoProgress: false, stepSeconds: 5 }, "Change simulator settings"],
    ["POST", "/system/v1/sim/orders/{orderId}/advance", { to: "RELEASED" }, "Simulate D365/WMS/TMS progress (RELEASED … INVOICED, CLOSED)"],
    ["GET", "/system/v1/lakehouse/files", null, "Bronze files written by the lakehouse system API"],
  ]],
];

let unsubscribe = null;
let expanded = new Set();

export async function mount(el) {
  if (unsubscribe) unsubscribe();
  expanded = new Set();
  setHTML(el, html`
    <div class="page-head"><div><h1>API explorer</h1>
      <div class="sub">Send requests through the same gateway the console uses and see the full <strong>input</strong> (method, URL, headers, body) and <strong>output</strong> (status, time, headers, body) of every call. Placeholders like <span class="mono">{orderId}</span> are filled from the variables below.</div></div>
      <div class="row"><button class="btn primary" id="scenario">Run end-to-end scenario</button></div>
    </div>
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
        <div class="small muted section-gap" id="presetDesc"></div>
        <div class="field section-gap"><label for="hdrs">Extra headers (JSON)</label><textarea id="hdrs" rows="3">{}</textarea></div>
        <div class="field section-gap"><label for="body">Body (JSON or text)</label><textarea id="body" rows="8"></textarea></div>
        <div class="row section-gap"><button class="btn primary" id="send">Send</button></div>
        <h3 class="section-gap">Presets</h3>
        ${PRESETS.map(([group, list], gi) => html`<div class="section-gap"><div class="small muted" style="font-weight:600">${group}</div>
          <div class="row" style="margin-top:4px">${list.map((p, i) => html`<button class="btn sm" data-preset="${gi}:${i}" title="${p[3] || ""}"><span class="mono">${p[0]}</span>&nbsp;${p[1].replace(/^\/(process|exp|system)/, "").split("?")[0]}</button>`)}</div></div>`)}
      </div>
      <div class="card"><h2>Input &amp; output</h2><div id="resp" class="muted">No request sent yet.</div></div>
    </div>
    <div class="card section-gap" id="scenarioCard" hidden><h2>End-to-end scenario</h2><div id="scenarioSteps"></div></div>
    <div class="card section-gap">
      <div class="row" style="justify-content:space-between;align-items:center">
        <h2 style="margin:0">API call log <span class="small muted" id="logCount"></span></h2>
        <div class="row"><button class="btn sm" id="logExport">Export JSON</button><button class="btn sm" id="logClear">Clear</button></div>
      </div>
      <div class="small muted">Every API call the console has made in this browser session (all pages), newest first. Select a row to see its input and output.</div>
      <div id="log" class="section-gap"></div>
    </div>`);

  el.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", () => {
    const [gi, i] = b.dataset.preset.split(":").map(Number);
    const [m, p, body, desc] = PRESETS[gi][1][i];
    $("#method", el).value = m;
    $("#path", el).value = p;
    $("#body", el).value = body ? JSON.stringify(body, null, 2) : "";
    $("#presetDesc", el).textContent = desc || "";
  }));
  $("#send", el).addEventListener("click", () => send(el));
  $("#scenario", el).addEventListener("click", e => runScenario(el, e.currentTarget));
  $("#logClear", el).addEventListener("click", () => { clearCallLog(); expanded.clear(); });
  $("#logExport", el).addEventListener("click", exportLog);
  $("#log", el).addEventListener("click", e => {
    const row = e.target.closest("[data-call]");
    if (!row) return;
    const id = Number(row.dataset.call);
    expanded.has(id) ? expanded.delete(id) : expanded.add(id);
    renderLog(el);
  });
  el.addEventListener("click", e => {
    const b = e.target.closest("[data-curl]");
    if (!b) return;
    const call = callLog().find(c => c.id === Number(b.dataset.curl));
    if (call) copy(toCurl(call));
  });
  unsubscribe = onCall(() => { if (document.body.contains(el)) renderLog(el); else if (unsubscribe) { unsubscribe(); unsubscribe = null; } });
  renderLog(el);
}

/* ---------- helpers ---------- */

function nextDelivery(days = 3) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  while (d.getDay() === 0) d.setDate(d.getDate() + 1); // FreshMart DC 12 receives Mon–Sat
  return d.toISOString().slice(0, 10);
}

function fill(el, s, encode = true) {
  return s.replace(/\{(orderId|orderGroupId|customerId|customerPo|deliveryDate)\}/g, (m, k) => {
    if (k === "deliveryDate") return nextDelivery();
    const v = $(`#v-${k}`, el).value.trim();
    return v ? (encode ? encodeURIComponent(v) : v) : m;
  });
}

function copy(text) {
  (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
    .then(() => toast("Copied cURL command"), () => toast("Clipboard unavailable", "error"));
}

function bodyView(v) {
  if (v === undefined || v === null || v === "") return html`<div class="muted small">(no body)</div>`;
  return typeof v === "string" ? x12(v) : json(v);
}

function headersView(h) {
  const entries = Object.entries(h || {});
  if (!entries.length) return html`<div class="muted small">(none)</div>`;
  return html`<pre class="json">${entries.map(([k, v]) => `${k}: ${v}`).join("\n")}</pre>`;
}

/** Side-by-side input / output panel for one recorded call. */
function ioPanel(call) {
  const rq = call.request, rs = call.response || {};
  return html`<div class="io-grid">
    <div class="io-col">
      <div class="io-title">Input</div>
      <div class="mono small io-line"><span class="badge">${rq.method}</span> ${rq.url}</div>
      <details><summary class="small">Request headers</summary>${headersView(rq.headers)}</details>
      <div class="small muted io-label">Request body</div>${bodyView(rq.body)}
    </div>
    <div class="io-col">
      <div class="io-title">Output</div>
      <div class="io-line"><span class="badge ${rs.ok ? "ok" : "sev-ERROR"}">${rs.status || "ERR"}</span>
        <span class="small muted">${rs.ms ?? "–"} ms${rs.correlationId ? raw(` · correlation <span class="mono">${esc(rs.correlationId)}</span>`) : ""}</span></div>
      <details><summary class="small">Response headers</summary>${headersView(rs.headers)}</details>
      <div class="small muted io-label">Response body</div>${bodyView(rs.body)}
    </div>
  </div>
  <div class="row section-gap"><button class="btn sm" data-curl="${call.id}">Copy as cURL</button></div>`;
}

function renderLog(el) {
  const host = $("#log", el);
  if (!host) return;
  const calls = callLog();
  $("#logCount", el).textContent = calls.length ? `(${calls.length})` : "";
  if (!calls.length) { setHTML(host, html`<div class="empty">No API calls yet.</div>`); return; }
  setHTML(host, html`<div class="table-wrap"><table class="log-table"><thead><tr>
      <th>#</th><th>Time</th><th>Method</th><th>API</th><th>Path</th><th>Status</th><th class="num">ms</th><th>From</th></tr></thead>
    <tbody>${calls.map(c => {
      const rs = c.response || {};
      const row = html`<tr data-call="${c.id}" class="clickable${expanded.has(c.id) ? " selected" : ""}" tabindex="0">
        <td class="mono">${c.id}</td><td class="nowrap">${new Date(c.at).toLocaleTimeString()}</td>
        <td><span class="badge">${c.request.method}</span></td><td>${c.layer}</td>
        <td class="mono small" style="word-break:break-all">${c.request.path}</td>
        <td><span class="badge ${rs.ok ? "ok" : "sev-ERROR"}">${rs.status || "ERR"}</span></td>
        <td class="num">${rs.ms ?? ""}</td><td class="small muted">${c.source}</td></tr>`;
      return expanded.has(c.id)
        ? html`${row}<tr class="io-row"><td colspan="8">${ioPanel(c)}</td></tr>`
        : row;
    })}</tbody></table></div>`);
}

function exportLog() {
  const blob = new Blob([JSON.stringify(callLog().slice().reverse(), null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `api-call-log-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function remember(el, d) {
  if (!d || typeof d !== "object") return;
  const id = d.orderId || (Array.isArray(d.items) && d.items[0] && d.items[0].orderId);
  if (id && !$("#v-orderId", el).value) $("#v-orderId", el).value = id;
  if (d.orderGroupId && !$("#v-orderGroupId", el).value) $("#v-orderGroupId", el).value = d.orderGroupId;
  if (d.customerPo && !$("#v-customerPo", el).value) $("#v-customerPo", el).value = d.customerPo;
}

/* ---------- single request ---------- */

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
  const r = await request("", path, { method, body: method === "GET" ? undefined : body, headers, raw: true,
                                      idempotent: method === "POST", source: "explorer" });
  setHTML(resp, ioPanel(r.call));
  remember(el, r.data);
}

/* ---------- end-to-end scenario ---------- */

async function runScenario(el, btn) {
  btn.disabled = true;
  const card = $("#scenarioCard", el), host = $("#scenarioSteps", el);
  card.hidden = false;
  const steps = [];
  const draw = () => setHTML(host, html`<ol class="scenario">${steps.map((s, i) => html`<li>
      <details ${i === steps.length - 1 ? raw("open") : ""}><summary>
        <span class="badge ${s.state === "ok" ? "ok" : s.state === "fail" ? "sev-ERROR" : ""}">${s.state === "run" ? "…" : s.state === "ok" ? "OK" : "FAIL"}</span>
        <strong>${s.name}</strong> <span class="small muted mono">${s.call ? `${s.call.request.method} ${s.call.request.path} → ${s.call.response?.status ?? ""}` : ""}</span>
        ${s.note ? html`<span class="small muted"> · ${s.note}</span>` : ""}</summary>
        ${s.call ? ioPanel(s.call) : ""}</details></li>`)}</ol>`);
  const call = async (name, layer, path, opts = {}) => {
    const step = { name, state: "run" };
    steps.push(step); draw();
    const r = await request(layer, path, { ...opts, raw: true, source: `scenario: ${name}` });
    step.call = r.call; step.state = r.ok ? "ok" : "fail"; draw();
    if (!r.ok) throw new Error(`${name} failed with HTTP ${r.status}`);
    return r.data;
  };
  const waitFor = async (name, id, wanted) => {
    for (let i = 0; i < 20; i++) {
      const r = await request("process", `/v1/orders/${id}/status`, { raw: true, source: `scenario: ${name}` });
      if (r.ok && wanted.includes(r.data.status)) {
        steps.push({ name, state: "ok", call: r.call, note: `status ${r.data.status} after ${i + 1} poll(s)` }); draw();
        return r.data;
      }
      await sleep(500);
    }
    steps.push({ name, state: "fail", note: `never reached ${wanted.join("/")}` }); draw();
    throw new Error(`${name}: order never reached ${wanted.join("/")}`);
  };
  try {
    await call("1. Look up customers (Experience → System)", "exp", "/portal/v1/catalog/customers");
    const po = `E2E-${Date.now().toString(36).toUpperCase()}`;
    const acc = await call("2. Submit portal order (Experience → Process intake)", "exp", "/portal/v1/orders", {
      body: { channel: "PORTAL", customerId: "C-1001", shipToId: "ST-1001-01", customerPo: po,
              requestedDeliveryDate: nextDelivery(), contact: { name: "API explorer", email: "explorer@example.com" },
              lines: [{ itemNumber: "TF-10001", quantity: 10, uom: "CA" }, { itemNumber: "TF-20001", quantity: 4, uom: "CA" }] },
      idempotent: true });
    const id = acc.orderId;
    $("#v-orderId", el).value = id; $("#v-orderGroupId", el).value = acc.orderGroupId || ""; $("#v-customerPo", el).value = po;
    await waitFor("3. Wait for D365 acceptance (Order Status API)", id, ["ACCEPTED"]);
    await call("4. Warehouse ships (System API simulator)", "system", `/v1/sim/orders/${id}/advance`, { body: { to: "SHIPPED" } });
    await waitFor("5. Status shows SHIPPED", id, ["SHIPPED"]);
    await call("6. Deliver and invoice (System API simulator)", "system", `/v1/sim/orders/${id}/advance`, { body: { to: "INVOICED" } });
    await waitFor("7. Status shows INVOICED", id, ["INVOICED"]);
    await call("8. Lifecycle history", "process", `/v1/orders/${id}/history`);
    await call("9. Documents", "process", `/v1/orders/${id}/documents`);
    await call("10. Canonical order (CDM v1)", "process", `/v1/orders/${id}`);
    await call("11. Search by customer PO", "process", `/v1/orders/by-po/${encodeURIComponent(po)}?customerId=C-1001`);
    await call("12. KPIs", "process", "/v1/metrics/kpis");
    toast(`Scenario passed for ${id}`);
  } catch (e) {
    toast(e.message, "error");
  } finally {
    btn.disabled = false;
  }
}
