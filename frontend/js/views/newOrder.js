import { api, items } from "../api.js";
import { html, setHTML, $, $$, toast, errorBox, today, fmtMoney, statusBadge, sevBadge, x12, loading, esc } from "../ui.js";

const TABS = [
  ["portal", "Portal / manual entry"],
  ["edi", "EDI X12 850"],
  ["email", "Email"],
  ["partner", "Partner API"],
];

let catalog = { customers: [], shipTos: [], items: [] };

export async function mount(el, tab) {
  tab = TABS.some(t => t[0] === tab) ? tab : "portal";
  setHTML(el, html`
    <div class="page-head"><div><h1>New order</h1>
      <div class="sub">Submit an order through any Tier 1 channel. Each channel has its own Experience API over the shared Process API.</div></div></div>
    <div class="tabs" role="tablist">${TABS.map(([k, label]) => html`
      <button role="tab" aria-selected="${k === tab}" data-tab="${k}">${label}</button>`)}</div>
    <div id="tabBody"></div>
    <div id="result" class="section-gap" aria-live="polite"></div>`);
  $$(".tabs button", el).forEach(b => b.addEventListener("click", () => { location.hash = `#/new/${b.dataset.tab}`; }));
  const body = $("#tabBody", el);
  ({ portal: portalTab, edi: ediTab, email: emailTab, partner: partnerTab })[tab](body, $("#result", el));
}

/* ---------- shared result rendering ---------- */

function renderAccepted(host, resp, extra = "") {
  const list = Array.isArray(resp && resp.orders) ? resp.orders : resp ? [resp] : [];
  const blocks = list.map(o => {
    const ex = Array.isArray(o.exceptions) ? o.exceptions : [];
    return html`<div class="card">
      <div class="row"><h2 style="margin:0">Order <a class="mono" href="#/orders/${encodeURIComponent(o.orderId || "")}">${o.orderId || "(no id)"}</a></h2>
        ${statusBadge(o.status, o.statusCategory)}<span class="spacer"></span>
        ${o.orderId ? html`<a class="btn sm" href="#/orders/${encodeURIComponent(o.orderId)}">Open order →</a>` : ""}</div>
      <div class="small muted section-gap">Group ${o.orderGroupId || "–"} · received ${o.receivedAt || "–"} · correlation ${o.correlationId || "–"}</div>
      <div class="small section-gap" data-fulfil="${o.orderId || ""}">${fulfilLine(o)}</div>
      ${ex.length ? html`<div class="alert warn section-gap"><strong>${ex.length} data-quality exception(s) – order is in the exception queue</strong>
        <ul>${ex.map(x => html`<li>${sevBadge(x.severity)} <strong>${x.ruleId || x.code}</strong> ${x.lineNumber ? `line ${x.lineNumber}: ` : ""}${x.message || ""}</li>`)}</ul></div>`
        : html`<div class="alert ok section-gap">Passed all quality rules – published and sent to D365.</div>`}
    </div>`;
  });
  setHTML(host, html`${blocks}${extra}`);
  host.scrollIntoView({ behavior: "smooth", block: "start" });
  // Best effort: the fulfilment region / site is decided during enrichment - read it from the status view.
  list.filter(o => o.orderId && !(o.region || o.shipFromWarehouse)).forEach(async o => {
    try {
      const st = await api.process(`/v1/orders/${encodeURIComponent(o.orderId)}/status`);
      const node = host.querySelector(`[data-fulfil="${CSS.escape(o.orderId)}"]`);
      if (node) setHTML(node, fulfilLine(st));
    } catch { /* status not available yet */ }
  });
}

const RULES = { HOME_SITE: "home site", ALTERNATE_SITE_IN_REGION: "alternate site in region", CROSS_REGION: "cross-region", BEST_AVAILABLE: "best available" };
function fulfilLine(o) {
  const region = o.regionName || o.region;
  const wh = o.shipFromWarehouse;
  if (!region && !wh) return "";
  return html`<span class="muted">Fulfilment:</span> <strong>${region || "–"}</strong>${wh ? html` · ships from <span class="mono">${wh}</span>` : ""}${o.sourcingRule ? html` · <span class="tag">${RULES[o.sourcingRule] || o.sourcingRule}</span>` : ""}`;
}

async function submit(host, btn, fn) {
  btn.disabled = true;
  setHTML(host, loading("Submitting…"));
  try {
    const r = await fn();
    toast("Order submitted", "ok");
    return r;
  } catch (e) {
    setHTML(host, errorBox(e));
    toast(e.message, "error");
    return null;
  } finally { btn.disabled = false; }
}

function poNumber(prefix = "PO") { return `${prefix}-${Date.now().toString().slice(-8)}`; }

/* ---------- Portal / manual entry ---------- */

async function portalTab(el, result) {
  setHTML(el, html`<form class="card" id="portalForm" novalidate>
    <div class="fields">
      <div class="field"><label for="channel">Channel</label>
        <select id="channel"><option value="PORTAL">Customer portal</option><option value="MANUAL">Manual entry (customer service)</option></select></div>
      <div class="field"><label for="customer">Customer (sold-to)</label><select id="customer" required><option>Loading…</option></select></div>
      <div class="field"><label for="shipTo">Ship-to</label><select id="shipTo" required></select></div>
      <div class="field"><label for="po">Customer PO</label><input id="po" required value="${poNumber()}"></div>
      <div class="field"><label for="rdd">Requested delivery date</label><input id="rdd" type="date" value="${today(2)}" required></div>
      <div class="field"><label for="orderType">Order type</label>
        <select id="orderType"><option value="STANDARD">Standard</option><option value="RUSH">Rush</option><option value="STANDING">Standing / recurring</option><option value="DROP_SHIP">Drop ship</option><option value="SAMPLE">Sample</option></select></div>
      <div class="field"><label for="contactName">Contact name</label><input id="contactName" value="Jordan Lee"></div>
      <div class="field"><label for="contactEmail">Contact email</label><input id="contactEmail" type="email" value="buyer@example.com"></div>
      <div class="field"><label for="enteredBy">Entered by</label><input id="enteredBy" value="console.user"></div>
    </div>
    <div class="field section-gap"><label for="notes">Notes</label><input id="notes" placeholder="Delivery instructions, dock hours…"></div>
    <div class="row section-gap"><h2 style="margin:0">Lines</h2><span class="spacer"></span>
      <button type="button" class="btn sm" id="addLine">+ Add line</button></div>
    <div class="table-wrap section-gap"><table>
      <thead><tr><th>#</th><th>Item</th><th class="num">Qty</th><th>UoM</th><th class="num">Unit price</th><th class="num">Ext.</th><th></th></tr></thead>
      <tbody id="lines"></tbody>
      <tfoot><tr><td colspan="5" class="right"><strong>Total</strong></td><td class="num" id="total">–</td><td></td></tr></tfoot>
    </table></div>
    <div class="row section-gap">
      <button class="btn primary" type="submit" id="submitBtn">Submit order</button>
      <span class="spacer"></span>
      <span class="muted small">Test exceptions:</span>
      <button type="button" class="btn sm" data-bad="price">Bad price</button>
      <button type="button" class="btn sm" data-bad="item">Unknown item</button>
      <button type="button" class="btn sm" data-bad="date">Past date</button>
      <button type="button" class="btn sm" data-bad="qty">Zero qty</button>
      <button type="button" class="btn sm" data-bad="dup">Duplicate PO</button>
    </div>
  </form>`);

  const custSel = $("#customer", el), shipSel = $("#shipTo", el), tbody = $("#lines", el);
  let lastPo = null;

  try {
    catalog.customers = items(await api.exp("/portal/v1/catalog/customers"));
  } catch (e) { setHTML(result, errorBox(e)); catalog.customers = []; }
  setHTML(custSel, html`${catalog.customers.map(c => html`<option value="${c.customerId}" ${c.active === false ? "" : ""}>${c.customerId} – ${c.name || ""}${c.active === false ? " (inactive)" : ""}</option>`)}`);
  const preferred = catalog.customers.find(c => c.active !== false && /portal|manual/i.test(JSON.stringify(c.channels || c.orderChannels || ""))) || catalog.customers.find(c => c.active !== false);
  if (preferred) custSel.value = preferred.customerId;

  async function loadCustomer() {
    const id = custSel.value;
    if (!id) return;
    setHTML(shipSel, html`<option>Loading…</option>`);
    try {
      const [st, it] = await Promise.all([
        api.exp(`/portal/v1/catalog/customers/${encodeURIComponent(id)}/ship-tos`),
        api.exp("/portal/v1/catalog/items", { query: { customerId: id } }),
      ]);
      catalog.shipTos = items(st);
      catalog.items = items(it);
    } catch (e) { catalog.shipTos = []; catalog.items = []; toast(e.message, "error"); }
    setHTML(shipSel, html`${catalog.shipTos.map(s => html`<option value="${s.shipToId}">${s.shipToId} – ${s.name || ""}${s.address ? ` (${[s.address.city, s.address.state || s.address.stateProvince].filter(Boolean).join(", ")})` : ""}${s.active === false ? " (inactive)" : ""}</option>`)}`);
    tbody.innerHTML = "";
    catalog.items.filter(i => i.active !== false).slice(0, 2).forEach((i, n) => addLine(i.itemNumber, (n + 1) * 10));
    if (!tbody.children.length) addLine();
  }

  function itemOptions(selected) {
    const opts = catalog.items.map(i => `<option value="${esc(i.itemNumber)}" ${i.itemNumber === selected ? "selected" : ""}>${esc(i.itemNumber)} – ${esc(i.description || "")}</option>`).join("");
    const custom = selected && !catalog.items.some(i => i.itemNumber === selected) ? `<option value="${esc(selected)}" selected>${esc(selected)} (not in catalog)</option>` : "";
    return `<option value="">Select item…</option>${opts}${custom}`;
  }
  function uomOptions(item, selected) {
    const uoms = item ? Array.from(new Set([item.uom, ...(item.salesUoms || [])].filter(Boolean))) : ["CA", "EA", "LB"];
    if (selected && !uoms.includes(selected)) uoms.push(selected);
    return uoms.map(u => `<option ${u === selected ? "selected" : ""}>${esc(u)}</option>`).join("");
  }
  function priceFor(item) { return item ? Number(item.contractPrice ?? item.listPrice ?? item.price ?? 0) : 0; }

  function addLine(itemNumber = "", qty = 10, price, uom) {
    const tr = document.createElement("tr");
    const item = catalog.items.find(i => i.itemNumber === itemNumber);
    tr.innerHTML = `<td class="ln"></td>
      <td><select class="it" aria-label="Item">${itemOptions(itemNumber)}</select><div class="small muted pk"></div></td>
      <td><input class="qty" type="number" min="0" step="1" value="${qty}" aria-label="Quantity" style="width:80px"></td>
      <td><select class="uom" aria-label="Unit of measure">${uomOptions(item, uom || (item && item.uom))}</select></td>
      <td><input class="price" type="number" step="0.01" min="0" value="${price ?? priceFor(item).toFixed(2)}" aria-label="Unit price" style="width:100px"></td>
      <td class="num ext"></td>
      <td><button type="button" class="btn sm danger rm" aria-label="Remove line">✕</button></td>`;
    tbody.appendChild(tr);
    const sel = $(".it", tr);
    sel.addEventListener("change", () => {
      const it = catalog.items.find(i => i.itemNumber === sel.value);
      $(".uom", tr).innerHTML = uomOptions(it, it && it.uom);
      $(".price", tr).value = priceFor(it).toFixed(2);
      recalc();
    });
    tr.addEventListener("input", recalc);
    $(".rm", tr).addEventListener("click", () => { tr.remove(); recalc(); });
    recalc();
    return tr;
  }

  function recalc() {
    let total = 0;
    $$("tr", tbody).forEach((tr, i) => {
      $(".ln", tr).textContent = (i + 1) * 10;
      const ext = Number($(".qty", tr).value) * Number($(".price", tr).value);
      total += isNaN(ext) ? 0 : ext;
      $(".ext", tr).textContent = fmtMoney(ext);
      const it = catalog.items.find(x => x.itemNumber === $(".it", tr).value);
      $(".pk", tr).textContent = it ? [it.packSize, it.temperatureZone || it.storageTemperature].filter(Boolean).join(" · ") : "";
    });
    $("#total", el).textContent = fmtMoney(total);
  }

  function buildRequest() {
    const contactName = $("#contactName", el).value.trim(), contactEmail = $("#contactEmail", el).value.trim();
    return {
      channel: $("#channel", el).value,
      customerId: custSel.value,
      shipToId: shipSel.value,
      customerPo: $("#po", el).value.trim(),
      requestedDeliveryDate: $("#rdd", el).value,
      orderType: $("#orderType", el).value,
      currency: "USD",
      notes: $("#notes", el).value.trim() || undefined,
      contact: contactName || contactEmail ? { name: contactName, email: contactEmail } : undefined,
      enteredBy: $("#enteredBy", el).value.trim() || undefined,
      lines: $$("tr", tbody).map(tr => ({
        itemNumber: $(".it", tr).value,
        quantity: Number($(".qty", tr).value),
        uom: $(".uom", tr).value,
        unitPrice: $(".price", tr).value === "" ? undefined : Number($(".price", tr).value),
      })),
    };
  }

  custSel.addEventListener("change", loadCustomer);
  $("#addLine", el).addEventListener("click", () => addLine());
  el.querySelectorAll("[data-bad]").forEach(b => b.addEventListener("click", () => {
    const kind = b.dataset.bad;
    const first = $("tr", tbody) || addLine();
    if (kind === "price") { const p = $(".price", first); p.value = (Number(p.value || 10) * 1.37).toFixed(2); }
    if (kind === "item") { const s = $(".it", first); s.innerHTML = itemOptions("TF-99999"); s.value = "TF-99999"; }
    if (kind === "date") $("#rdd", el).value = today(-3);
    if (kind === "qty") $(".qty", first).value = 0;
    if (kind === "dup") { if (lastPo) $("#po", el).value = lastPo; else toast("Submit one order first, then use Duplicate PO"); }
    recalc();
    toast(`Form changed to trigger a ${kind} exception – now submit`);
  }));
  $("#portalForm", el).addEventListener("submit", async e => {
    e.preventDefault();
    const req = buildRequest();
    if (!req.customerId || !req.lines.length) { toast("Pick a customer and add at least one line", "error"); return; }
    const r = await submit(result, $("#submitBtn", el), () => api.exp("/portal/v1/orders", { body: req, idempotent: true }));
    if (r) {
      lastPo = req.customerPo;
      renderAccepted(result, r);
      $("#po", el).value = poNumber();
    }
  });
  await loadCustomer();
}

/* ---------- EDI X12 850 ---------- */

const pad = (s, n) => String(s).padEnd(n, " ").slice(0, n);
const num = (v, n) => String(v).padStart(n, "0");

export function buildX12850({ partner = "FRESHMART", receiver = "TAYLORFARMS", transactions = 1, deliverInDays = 3, poBase } = {}) {
  const now = new Date();
  const yymmdd = now.toISOString().slice(2, 10).replace(/-/g, "");
  const ccyymmdd = now.toISOString().slice(0, 10).replace(/-/g, "");
  const hhmm = now.toISOString().slice(11, 16).replace(":", "");
  const d = new Date(now); d.setDate(d.getDate() + deliverInDays);
  const deliver = d.toISOString().slice(0, 10).replace(/-/g, "");
  const ctrl = num(Math.floor(Math.random() * 1e9), 9);
  const gsCtrl = String(Math.floor(Math.random() * 9e5) + 1e5);
  const base = poBase || `FM${Date.now().toString().slice(-7)}`;
  const lines = [
    { qty: 40, uom: "CA", price: "24.50", buyer: "FM-55012", vendor: "TF-10001", desc: "Chopped Romaine 6x2lb" },
    { qty: 25, uom: "CA", price: "31.00", buyer: "FM-55020", vendor: "TF-10002", desc: "Spring Mix 4x3lb" },
    { qty: 12, uom: "CA", price: "42.00", buyer: "FM-61001", vendor: "TF-20001", desc: "Caesar Salad Kit 12ct" },
  ];
  const shipTos = [["FreshMart DC 12", "FM-DC12"], ["FreshMart DC 12", "FM-DC12"]];
  const segs = [
    `ISA*00*${pad("", 10)}*00*${pad("", 10)}*ZZ*${pad(partner, 15)}*ZZ*${pad(receiver, 15)}*${yymmdd}*${hhmm}*U*00401*${ctrl}*0*T*>`,
    `GS*PO*${partner}*${receiver}*${ccyymmdd}*${hhmm}*${gsCtrl}*X*004010`,
  ];
  for (let t = 0; t < transactions; t++) {
    const st = num(t + 1, 4);
    const txLines = t === 0 ? lines : lines.slice(0, 2).map(l => ({ ...l, qty: l.qty / 2 }));
    const body = [
      `ST*850*${st}`,
      `BEG*00*SA*${base}${transactions > 1 ? "-" + (t + 1) : ""}**${ccyymmdd}`,
      `CUR*BY*USD`,
      `REF*DP*PRODUCE`,
      `PER*BD*Pat Morgan*TE*5555550100`,
      `DTM*002*${deliver}`,
      `N1*BY*FreshMart Grocery*92*FRESHMART`,
      `N1*ST*${shipTos[t % 2][0]}*92*${shipTos[t % 2][1]}`,
    ];
    txLines.forEach((l, i) => {
      body.push(`PO1*${i + 1}*${l.qty}*${l.uom}*${l.price}**IN*${l.buyer}*VN*${l.vendor}`);
      body.push(`PID*F****${l.desc}`);
    });
    body.push(`CTT*${txLines.length}`);
    body.push(`SE*${body.length + 1}*${st}`);
    segs.push(...body);
  }
  segs.push(`GE*${transactions}*${gsCtrl}`, `IEA*1*${ctrl}`);
  return segs.join("~\n") + "~";
}

function ediTab(el, result) {
  setHTML(el, html`<form class="card" id="ediForm">
    <div class="fields">
      <div class="field"><label for="partner">Trading partner (x-partner-id / ISA06)</label><input id="partner" value="FRESHMART"></div>
      <div class="field"><label for="txCount">ST transactions in interchange</label>
        <select id="txCount"><option value="1">1 – single order</option><option value="2">2 – multi-order interchange</option></select></div>
      <div class="field"><label for="ediDays">Deliver in (days)</label><input id="ediDays" type="number" value="3"></div>
    </div>
    <div class="field section-gap"><label for="x12">X12 850 interchange</label><textarea id="x12" rows="18" spellcheck="false"></textarea></div>
    <div class="row section-gap">
      <button class="btn primary" type="submit" id="ediSubmit">Send 850</button>
      <button class="btn" type="button" id="regen">Regenerate sample</button>
      <span class="muted small">Returns a 997 functional acknowledgment synchronously; 855/856/810 appear on the order's Documents tab as it progresses.</span>
    </div>
  </form>`);
  const regen = () => {
    $("#x12", el).value = buildX12850({ partner: $("#partner", el).value.trim() || "FRESHMART", transactions: Number($("#txCount", el).value), deliverInDays: Number($("#ediDays", el).value) || 3 });
  };
  regen();
  $("#regen", el).addEventListener("click", regen);
  $("#txCount", el).addEventListener("change", regen);
  $("#ediForm", el).addEventListener("submit", async e => {
    e.preventDefault();
    const body = $("#x12", el).value.replace(/\n/g, "");
    const r = await submit(result, $("#ediSubmit", el), () => api.exp("/edi/v1/x12/850", {
      body, contentType: "text/plain", idempotent: true, headers: { "x-partner-id": $("#partner", el).value.trim() },
    }));
    if (r) {
      const ack = r.ack997 || r.ack || r.acknowledgment;
      const none = (r.orders || []).length ? "" : html`<div class="alert warn">No orders were created from this interchange.</div>`;
      renderAccepted(result, { orders: r.orders || [] },
        html`${none}${ack ? html`<div class="card section-gap"><h2>997 functional acknowledgment</h2>${x12(ack)}</div>` : ""}`);
    }
  });
}

/* ---------- Email ---------- */

function emailTab(el, result) {
  const sample = `Hello Taylor Farms team,

Please process the order below.

PO: EM-${Date.now().toString().slice(-6)}
Customer: C-1003
Ship-To: ST-1003-01
Deliver: ${today(3)}

TF-10001 10 CA
TF-30001 5 CA

Thanks,
Greenleaf Cafe Group purchasing`;
  setHTML(el, html`<form class="card" id="emailForm">
    <div class="fields">
      <div class="field"><label for="from">From</label><input id="from" type="email" value="purchasing@greenleaf.example"></div>
      <div class="field"><label for="subject">Subject</label><input id="subject" value="New order – Greenleaf Cafe Group"></div>
    </div>
    <div class="field section-gap"><label for="emailBody">Body</label><textarea id="emailBody" rows="14">${sample}</textarea></div>
    <div class="field section-gap"><label for="attach">Attachment (optional: CSV, XLSX, PDF or TXT order form)</label>
      <input id="attach" type="file" accept=".csv,.xlsx,.xls,.pdf,.txt,text/csv,application/pdf,text/plain"></div>
    <div class="row section-gap">
      <button class="btn primary" type="submit" id="emailSubmit">Send email</button>
      <button class="btn" type="button" id="csvSample">Use CSV attachment sample</button>
      <span class="muted small">Email orders are extracted (Document Intelligence for PDFs) and must reach 0.80 confidence (DQ-011).</span>
    </div>
  </form>`);
  let csvAttachment = null;
  $("#csvSample", el).addEventListener("click", () => {
    const csv = "itemNumber,quantity,uom,unitPrice\nTF-10001,10,CA,24.50\nTF-20001,6,CA,42.00\n";
    csvAttachment = { fileName: "order.csv", contentType: "text/csv", contentBase64: btoa(csv) };
    $("#emailBody", el).value = $("#emailBody", el).value.replace(/\n(TF-[^\n]*\n?)+/g, "\n(see attached order.csv)\n");
    toast("CSV attachment order.csv will be sent with the email");
  });
  $("#emailForm", el).addEventListener("submit", async e => {
    e.preventDefault();
    const attachments = [];
    const f = $("#attach", el).files[0];
    if (f) attachments.push({ fileName: f.name, contentType: f.type || "application/octet-stream", contentBase64: await fileToBase64(f) });
    else if (csvAttachment) attachments.push(csvAttachment);
    const body = {
      from: $("#from", el).value.trim(), subject: $("#subject", el).value.trim(),
      receivedAt: new Date().toISOString(), body: $("#emailBody", el).value, attachments,
    };
    const r = await submit(result, $("#emailSubmit", el), () => api.exp("/email/v1/orders", { body, idempotent: true }));
    if (r) renderAccepted(result, r);
  });
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
    fr.onerror = () => reject(fr.error);
    fr.readAsDataURL(file);
  });
}

/* ---------- Partner API ---------- */

function partnerTab(el, result) {
  const draft = {
    customerPo: poNumber("PA"),
    orderType: "STANDARD",
    soldTo: { customerId: "C-1002" },
    shipTo: { shipToId: "ST-1002-01" },
    requestedDeliveryDate: today(3),
    currency: "USD",
    lines: [
      { lineNumber: 10, item: { itemNumber: "TF-10001" }, quantity: { value: 60, uom: "CA" }, unitPrice: { amount: 24.50 } },
      { lineNumber: 20, item: { itemNumber: "TF-40001" }, quantity: { value: 20, uom: "CA" } },
    ],
  };
  setHTML(el, html`<form class="card" id="partnerForm">
    <p class="muted">Partner platforms post the canonical draft format directly (system to system, authenticated). Prices may be omitted – the agreement price is applied during enrichment.</p>
    <div class="field"><label for="pjson">Canonical order draft (JSON)</label><textarea id="pjson" rows="22" spellcheck="false">${JSON.stringify(draft, null, 2)}</textarea></div>
    <div class="row section-gap"><button class="btn primary" type="submit" id="pSubmit">POST /partner/v1/orders</button>
      <button class="btn" type="button" id="pFmt">Format JSON</button></div>
  </form>`);
  $("#pFmt", el).addEventListener("click", () => {
    try { $("#pjson", el).value = JSON.stringify(JSON.parse($("#pjson", el).value), null, 2); } catch (err) { toast(`Invalid JSON: ${err.message}`, "error"); }
  });
  $("#partnerForm", el).addEventListener("submit", async e => {
    e.preventDefault();
    let body;
    try { body = JSON.parse($("#pjson", el).value); } catch (err) { toast(`Invalid JSON: ${err.message}`, "error"); return; }
    const r = await submit(result, $("#pSubmit", el), () => api.exp("/partner/v1/orders", { body, idempotent: true }));
    if (r) renderAccepted(result, r);
  });
}
