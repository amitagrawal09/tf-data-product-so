// Data marketplace: catalog of governed datasets (Sales Orders, lines, history, shipments, invoices, KPIs,
// customers, ship-tos, items, prices, partners) with definitions, lineage, refresh SLAs, preview, export and
// access requests. Every action is fulfilled by the Experience → Process / System APIs.
import { api, download, loadSettings, scope } from "../api.js";
import { scopePill, regionName } from "../scope.js";
import { html, raw, setHTML, $, $$, toast, errorBox, loading, fmtDate, fmtNum, esc } from "../ui.js";

const ID_KEY = "tfso.consumer";
const TABS = [["overview", "Overview"], ["schema", "Data definition"], ["lineage", "Lineage"], ["preview", "Preview"],
              ["export", "Retrieve / export"], ["access", "Access"], ["consume", "How to consume"]];

/* ---------- consumer identity (sent as x-user-id) ---------- */
function identity() {
  try { return Object.assign({ userId: "analyst@taylorfarms.example", team: "Commercial Analytics" }, JSON.parse(localStorage.getItem(ID_KEY) || "{}")); }
  catch { return { userId: "analyst@taylorfarms.example", team: "Commercial Analytics" }; }
}
function setIdentity(v) { try { localStorage.setItem(ID_KEY, JSON.stringify(v)); } catch { /* storage unavailable */ } }
const H = () => ({ "x-user-id": identity().userId });
const mp = (path, opts = {}) => api.exp(`/marketplace/v1${path}`, { ...opts, headers: { ...H(), ...(opts.headers || {}) } });

/* ---------- formatting ---------- */
function ago(ts) {
  if (!ts) return "–";
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (Number.isNaN(s)) return String(ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
function sla(sec) {
  if (!sec) return "n/a";
  return sec < 120 ? `${sec} s` : sec < 7200 ? `${Math.round(sec / 60)} min` : `${Math.round(sec / 3600)} h`;
}
const classBadge = c => html`<span class="badge cls-${c}">${c}</span>`;
const freshDot = st => html`<span class="fresh fresh-${st || "UNKNOWN"}" title="${st || ""}"></span>`;

/* =====================================================================================
 * Catalog
 * ===================================================================================*/
let filters = { q: "", domain: "", classification: "", type: "" };

export async function mount(el, id, tab) {
  if (id) return mountDetail(el, decodeURIComponent(id), tab);
  const me = identity();
  setHTML(el, html`
    <div class="page-head"><div><h1>Data marketplace</h1>
      <div class="sub">Governed datasets from the Sales Order data product and the master data it depends on. Find data, read its definition and lineage, check freshness, then preview, request access and export. Every request is fulfilled by the Experience → Process / System APIs.</div>
      <div class="section-gap">${scopePill()} <span class="small muted">Regional and account filters on order datasets default to this scope.</span></div></div>
      <div class="identity card" style="padding:8px 12px"><div class="small muted">Consuming as</div>
        <div><strong id="who">${me.userId}</strong> <span class="small muted">${me.team}</span> <button class="btn sm" id="changeWho">Change</button></div></div>
    </div>
    <div id="tiles">${loading()}</div>
    <div class="card section-gap">
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <input id="q" type="search" placeholder="Search datasets, fields, tags… (e.g. customer, gtin, traceability)" style="flex:1;min-width:220px" value="${filters.q}" aria-label="Search">
        <select id="cls" aria-label="Classification" style="width:auto"><option value="">All classifications</option></select>
        <select id="typ" aria-label="Type" style="width:auto"><option value="">All types</option></select>
      </div>
      <div class="row section-gap" id="domains" role="group" aria-label="Domain"></div>
    </div>
    <div class="ds-grid section-gap" id="grid">${loading()}</div>
    <details class="section-gap small"><summary>Backend API calls used to compute this catalog</summary><div id="calls"></div></details>`);

  $("#changeWho", el).addEventListener("click", () => {
    const cur = identity();
    const userId = prompt("Your user id / e-mail (sent as x-user-id):", cur.userId);
    if (!userId) return;
    const team = prompt("Your team:", cur.team) || cur.team;
    setIdentity({ userId: userId.trim(), team });
    mount(el);
  });
  let t;
  $("#q", el).addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { filters.q = e.target.value; load(el); }, 250); });
  $("#cls", el).addEventListener("change", e => { filters.classification = e.target.value; load(el); });
  $("#typ", el).addEventListener("change", e => { filters.type = e.target.value; load(el); });
  await load(el, true);
}

async function load(el, first = false) {
  let res;
  try { res = await mp("/datasets", { query: filters }); }
  catch (e) { setHTML($("#grid", el), errorBox(e)); return; }
  if (first) {
    const all = res;
    const total = all.items.reduce((a, d) => a + (d.recordCount || 0), 0);
    const exports = all.items.reduce((a, d) => a + ((d.usage || {}).exports || 0), 0);
    setHTML($("#tiles", el), html`<div class="grid grid-4">
      <div class="tile"><div class="label">Datasets</div><div class="value">${all.total}</div><div class="hint">${all.domains.length} domains</div></div>
      <div class="tile"><div class="label">Certified data products</div><div class="value">${all.items.filter(d => d.certified).length}</div><div class="hint">Contract-governed output ports</div></div>
      <div class="tile"><div class="label">Records available</div><div class="value">${fmtNum(total)}</div><div class="hint">Live from the APIs</div></div>
      <div class="tile"><div class="label">Exports (audited)</div><div class="value">${fmtNum(exports)}</div><div class="hint">All consumers</div></div></div>`);
    $("#cls", el).insertAdjacentHTML("beforeend", all.classifications.map(c => `<option>${esc(c)}</option>`).join(""));
    $("#typ", el).insertAdjacentHTML("beforeend", all.types.map(c => `<option>${esc(c)}</option>`).join(""));
    const dom = $("#domains", el);
    setHTML(dom, html`<button class="fchip" aria-pressed="${!filters.domain}" data-dom="">All domains</button>${all.domains.map(d => html`<button class="fchip" aria-pressed="${filters.domain === d}" data-dom="${d}">${d}</button>`)}`);
    dom.addEventListener("click", e => {
      const b = e.target.closest("[data-dom]");
      if (!b) return;
      filters.domain = b.dataset.dom;
      $$("[data-dom]", dom).forEach(x => x.setAttribute("aria-pressed", String(x === b)));
      load(el);
    });
  }
  setHTML($("#calls", el), html`<pre class="json">${(res.fulfilledBy || []).join("\n")}</pre>`);
  const grid = $("#grid", el);
  if (!res.items.length) { setHTML(grid, html`<div class="empty card">No datasets match your search.</div>`); return; }
  setHTML(grid, html`${res.items.map(d => html`
    <a class="ds-card card" href="#/marketplace/${encodeURIComponent(d.id)}">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:8px">
        <div><div class="small muted">${d.domain} · ${d.object}</div><h3 style="margin:2px 0 0">${d.name}</h3></div>
        <div class="row" style="gap:4px">${d.certified ? html`<span class="badge ok" title="Certified data product">Certified</span>` : ""}${classBadge(d.classification)}</div>
      </div>
      <p class="small" style="margin:8px 0">${d.summary}</p>
      <div class="ds-facts small">
        <div><span class="muted">Records</span><strong>${d.recordCount == null ? "–" : fmtNum(d.recordCount)}</strong></div>
        <div><span class="muted">Refresh</span><strong>${d.refresh.frequency}</strong></div>
        <div><span class="muted">Freshness</span><strong>${freshDot(d.freshnessStatus)} ${d.freshnessStatus === "DELAYED" ? "Delayed" : "On time"} · SLA ${sla(d.refresh.freshnessSlaSeconds)}</strong></div>
        <div><span class="muted">Last change</span><strong>${ago(d.lastDataChangeAt)}</strong></div>
      </div>
      <div class="row small muted" style="margin-top:8px;gap:6px;flex-wrap:wrap">
        <span>${d.type}</span><span>·</span><span>${d.fieldCount} fields${d.piiFieldCount ? ` (${d.piiFieldCount} PII)` : ""}</span><span>·</span><span>${d.owner.team}</span>
      </div>
      <div class="row" style="margin-top:8px;gap:4px;flex-wrap:wrap">${d.tags.map(t => html`<span class="tag">#${t}</span>`)}</div>
    </a>`)}`);
}

/* =====================================================================================
 * Dataset detail
 * ===================================================================================*/
let ds = null, curTab = "overview";

async function mountDetail(el, id, tab) {
  curTab = TABS.some(t => t[0] === tab) ? tab : "overview";
  setHTML(el, html`<div class="page-head"><div><div class="small muted"><a href="#/marketplace">Data marketplace</a> /</div>
      <h1 id="dsTitle">${id}</h1><div class="sub" id="dsSub"></div></div>
      <div class="row"><button class="btn" id="dsReload">Refresh</button></div></div>
    <div id="dsTiles">${loading()}</div>
    <div class="section-gap tabs" role="tablist">${TABS.map(([k, l]) => html`<button role="tab" data-tab="${k}" aria-selected="${k === curTab}">${l}</button>`)}</div>
    <div id="dsTab"></div>`);
  $$(".tabs button", el).forEach(b => b.addEventListener("click", () => {
    curTab = b.dataset.tab;
    $$(".tabs button", el).forEach(x => x.setAttribute("aria-selected", String(x === b)));
    history.replaceState(null, "", `#/marketplace/${encodeURIComponent(id)}/${curTab}`);
    renderTab(el);
  }));
  $("#dsReload", el).addEventListener("click", () => loadDetail(el, id));
  await loadDetail(el, id);
}

async function loadDetail(el, id) {
  try { ds = await mp(`/datasets/${encodeURIComponent(id)}`); }
  catch (e) { setHTML($("#dsTiles", el), errorBox(e)); return; }
  $("#dsTitle", el).textContent = ds.name;
  setHTML($("#dsSub", el), html`${ds.domain} · ${ds.object} · ${ds.type} · v${ds.version} ${classBadge(ds.classification)} ${ds.certified ? html`<span class="badge ok">Certified</span>` : ""}`);
  const u = ds.usage || {};
  setHTML($("#dsTiles", el), html`<div class="grid grid-4">
    <div class="tile"><div class="label">Records</div><div class="value">${ds.recordCount == null ? "–" : fmtNum(ds.recordCount)}</div><div class="hint">${ds.grain}</div></div>
    <div class="tile"><div class="label">Refresh</div><div class="value" style="font-size:18px">${ds.refresh.frequency}</div><div class="hint">${ds.refresh.mode}</div></div>
    <div class="tile"><div class="label">Freshness</div><div class="value" style="font-size:18px">${freshDot(ds.freshnessStatus)} ${ds.freshnessStatus === "DELAYED" ? "Delayed" : "On time"}</div><div class="hint">SLA ${sla(ds.refresh.freshnessSlaSeconds)} · last change ${ago(ds.lastDataChangeAt)}</div></div>
    <div class="tile"><div class="label">Your access</div><div class="value" style="font-size:18px">${ds.myAccess.canExport ? "Granted" : "Approval needed"}</div><div class="hint">${u.exports || 0} exports · ${u.approvedConsumers || 0} approved consumers · ${u.pendingRequests || 0} pending</div></div></div>`);
  renderTab(el);
}

function renderTab(el) {
  const host = $("#dsTab", el);
  ({ overview, schema, lineage, preview, export: exportTab, access, consume }[curTab] || overview)(host, el);
}

/* ---------- overview ---------- */
function person(p) { return html`<div><strong>${p.name}</strong><div class="small muted">${p.team}</div><div class="small"><a href="mailto:${p.email}">${p.email}</a></div></div>`; }

function overview(host) {
  setHTML(host, html`<div class="grid grid-2">
    <div class="card"><h2>Definition</h2><p>${ds.description}</p>
      <div class="kv-grid small">
        <div class="muted">Business object</div><div>${ds.object}</div>
        <div class="muted">Grain</div><div>${ds.grain}</div>
        <div class="muted">Primary key</div><div class="mono">${ds.primaryKey.join(", ")}</div>
        <div class="muted">Classification</div><div>${classBadge(ds.classification)} ${ds.accessPolicy === "approval" ? "Owner approval required to export; PII masked in previews" : "Self-service (approved by policy)"}</div>
        <div class="muted">Retention</div><div>${ds.retention}</div>
        <div class="muted">Version</div><div>${ds.version} (semantic versioning; breaking changes ship as a new major version with notice)</div>
      </div>
      <h3 class="section-gap">Tags</h3><div class="row" style="gap:4px;flex-wrap:wrap">${ds.tags.map(t => html`<span class="tag">#${t}</span>`)}</div>
    </div>
    <div class="card"><h2>Ownership &amp; service levels</h2>
      <div class="grid grid-2"><div><div class="small muted">Owner</div>${person(ds.owner)}</div><div><div class="small muted">Steward</div>${person(ds.steward)}</div></div>
      <div class="kv-grid small section-gap">
        <div class="muted">Refresh mode</div><div>${ds.refresh.mode}</div>
        <div class="muted">Frequency</div><div>${ds.refresh.frequency}</div>
        <div class="muted">Freshness SLA</div><div>${sla(ds.refresh.freshnessSlaSeconds)}</div>
        <div class="muted">How it refreshes</div><div>${ds.refresh.detail}</div>
        <div class="muted">Last data change</div><div>${ds.lastDataChangeAt ? `${fmtDate(ds.lastDataChangeAt)} (${ago(ds.lastDataChangeAt)})` : "–"}</div>
        <div class="muted">Pipeline status</div><div>${freshDot(ds.freshnessStatus)} ${ds.freshnessDetail || ""}</div>
      </div>
      <h3 class="section-gap">Quality rules applied</h3>
      ${ds.qualityRules.length ? html`<div class="row" style="gap:4px;flex-wrap:wrap">${ds.qualityRules.map(r => html`<span class="tag mono">${r}</span>`)}</div>` : html`<div class="small muted">Inherited from the source system</div>`}
      <h3 class="section-gap">Known consumers</h3><ul class="small">${ds.consumers.map(c => html`<li>${c}</li>`)}</ul>
    </div></div>`);
}

/* ---------- schema ---------- */
function schema(host) {
  setHTML(host, html`<div class="card"><h2>Data definition <span class="small muted">${ds.schema.length} fields · key ${ds.primaryKey.join(", ")}</span></h2>
    <div class="table-wrap"><table><thead><tr><th>Field</th><th>Type</th><th>Key</th><th>Nullable</th><th>PII</th><th>Description</th><th>Mastered in</th></tr></thead>
    <tbody>${ds.schema.map(f => html`<tr><td class="mono">${f.name}</td><td class="mono small">${f.type}</td><td>${f.primaryKey ? "PK" : ""}</td>
      <td>${f.nullable ? "yes" : "no"}</td><td>${f.pii ? html`<span class="badge sev-WARNING">PII</span>` : (ds.maskedFields || []).includes(f.name) ? html`<span class="badge">Sensitive</span>` : ""}</td>
      <td>${f.description}${f.example !== undefined ? html`<div class="small muted">e.g. <span class="mono">${String(f.example)}</span></div>` : ""}</td><td class="small">${f.source || "Data product"}</td></tr>`)}</tbody></table></div>
    ${ds.consumption && ds.consumption.schemaRef ? html`<div class="small muted section-gap">Machine-readable contract: <span class="mono">${ds.consumption.schemaRef}</span> (JSON Schema 2020-12)</div>` : ""}</div>`);
}

/* ---------- lineage (SVG, left → right by stage) ---------- */
function lineage(host) {
  const { nodes, edges } = ds.lineage;
  const stages = [...new Set(nodes.map(n => n.stage))].sort((a, b) => a - b);
  const W = 170, NH = 56, GX = 46, GY = 16, PAD = 14;
  const cols = stages.map(s => nodes.filter(n => n.stage === s));
  const maxRows = Math.max(...cols.map(c => c.length));
  const height = PAD * 2 + maxRows * NH + (maxRows - 1) * GY;
  const pos = {};
  cols.forEach((col, ci) => {
    const colH = col.length * NH + (col.length - 1) * GY;
    const y0 = (height - colH) / 2;
    col.forEach((n, ri) => { pos[n.id] = { x: PAD + ci * (W + GX), y: y0 + ri * (NH + GY) }; });
  });
  const width = PAD * 2 + cols.length * W + (cols.length - 1) * GX;
  const wrap = (t, n = 24) => { const w = String(t).split(" "); const out = [""]; for (const x of w) { if ((out[out.length - 1] + " " + x).trim().length > n) out.push(x); else out[out.length - 1] = (out[out.length - 1] + " " + x).trim(); } return out.slice(0, 2); };
  const paths = edges.filter(e => pos[e.from] && pos[e.to]).map(e => {
    const a = pos[e.from], b = pos[e.to];
    const x1 = a.x + W, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2;
    const mx = (x1 + x2) / 2;
    return `<path class="ln-edge" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2 - 6},${y2}" marker-end="url(#arrow)"/>` +
      (e.label ? `<text class="ln-elabel" x="${mx}" y="${(y1 + y2) / 2 - 4}" text-anchor="middle">${esc(e.label)}</text>` : "");
  }).join("");
  const boxes = nodes.map(n => {
    const p = pos[n.id];
    const lines = wrap(n.label);
    return `<g class="ln-node ln-${n.kind}"><title>${esc(n.label)} - ${esc(n.detail || "")}</title>
      <rect x="${p.x}" y="${p.y}" width="${W}" height="${NH}" rx="8"/>
      ${lines.map((l, i) => `<text class="ln-label" x="${p.x + 10}" y="${p.y + 20 + i * 15}">${esc(l)}</text>`).join("")}
      <text class="ln-kind" x="${p.x + 10}" y="${p.y + NH - 7}">${esc(n.kind)}</text></g>`;
  }).join("");
  const legend = ["source", "system", "integration", "store", "dataset", "stream", "consumer"];
  setHTML(host, html`<div class="card"><h2>Lineage</h2>
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="small muted">Where this data comes from (left) and where it flows (right). Hover a node for details.</div>
      <button class="btn sm" id="lnZoom" aria-pressed="false">Actual size</button></div>
    <div class="row section-gap small" style="gap:10px;flex-wrap:wrap">${legend.map(k => html`<span class="row" style="gap:4px"><span class="ln-swatch ln-${k}"></span>${k}</span>`)}</div>
    <div class="lineage-wrap section-gap" id="lnWrap">${raw(`<svg class="lineage fit" viewBox="0 0 ${width} ${height}" data-w="${width}" role="img" aria-label="Lineage diagram for ${esc(ds.name)}">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="ln-arrow"/></marker></defs>
      ${paths}${boxes}</svg>`)}</div>
    <h3 class="section-gap">Nodes</h3>
    <div class="table-wrap"><table><thead><tr><th>Node</th><th>Kind</th><th>Detail</th><th>Feeds</th></tr></thead><tbody>
    ${nodes.slice().sort((a, b) => a.stage - b.stage).map(n => html`<tr><td>${n.label}</td><td>${n.kind}</td><td class="small">${n.detail || ""}</td>
      <td class="small">${edges.filter(e => e.from === n.id).map(e => (nodes.find(x => x.id === e.to) || {}).label).join(", ")}</td></tr>`)}</tbody></table></div></div>`);
  $("#lnZoom", host).addEventListener("click", e => {
    const svg = $("#lnWrap svg", host);
    const actual = svg.classList.toggle("fit") === false;
    svg.style.width = actual ? `${svg.dataset.w}px` : "";
    e.currentTarget.textContent = actual ? "Fit to width" : "Actual size";
    e.currentTarget.setAttribute("aria-pressed", String(actual));
  });
}

/* ---------- filters shared by preview / export ---------- */
function filterFields() {
  // Order datasets accept region / accountScope: default them from the global operating scope.
  const g = scope();
  const defaults = { region: g.region || "", accountScope: g.accountScope || "", site: g.site || "" };
  return (ds.filters || []).map(f => {
    const id = `f-${f.name}`;
    const def = defaults[f.name] || "";
    const label = v => (f.name === "region" ? regionName(v) || v : v);
    let input;
    if (f.type === "enum") input = html`<select id="${id}" data-filter="${f.name}"><option value="">Any</option>${(f.values || []).map(v => html`<option value="${v}" ${v === def ? raw("selected") : ""}>${label(v)}</option>`)}</select>`;
    else if (f.type === "boolean") input = html`<select id="${id}" data-filter="${f.name}"><option value="">Any</option><option value="true">Yes</option><option value="false">No</option></select>`;
    else input = html`<input id="${id}" data-filter="${f.name}" value="${def}" type="${f.type === "date" ? "date" : f.type === "datetime" ? "datetime-local" : "text"}" placeholder="${f.help || ""}">`;
    return html`<div class="field"><label for="${id}">${f.label}${def ? html` <span class="small muted">(from scope)</span>` : ""}</label>${input}</div>`;
  });
}
function readFilters(host) {
  const q = {};
  $$("[data-filter]", host).forEach(i => { if (i.value) q[i.dataset.filter] = i.type === "datetime-local" ? new Date(i.value).toISOString() : i.value; });
  return q;
}
function callsBox(calls, title = "Fulfilled in the backend by") {
  return html`<div class="fulfil section-gap"><div class="small"><strong>${title}</strong></div>
    <ol class="small mono">${(calls || []).map(c => html`<li>${c}</li>`)}</ol></div>`;
}

/* ---------- preview ---------- */
function preview(host) {
  setHTML(host, html`<div class="card"><h2>Preview</h2>
    <div class="fields">${filterFields()}<div class="field"><label for="pv-limit">Rows</label><input id="pv-limit" type="number" min="1" max="200" value="20"></div></div>
    <div class="row section-gap"><button class="btn primary" id="pvRun">Preview data</button></div>
    <div id="pvOut" class="section-gap"></div></div>`);
  const run = async () => {
    const out = $("#pvOut", host);
    setHTML(out, loading("Fetching through the APIs…"));
    try {
      const r = await mp(`/datasets/${encodeURIComponent(ds.id)}/preview`, { query: { ...readFilters(host), limit: $("#pv-limit", host).value } });
      setHTML(out, html`${r.maskedFields.length ? html`<div class="notice">Masked until your access is approved: <span class="mono">${r.maskedFields.join(", ")}</span></div>` : ""}
        <div class="small muted">${r.rowCount} row(s)</div>
        ${r.rows.length ? html`<div class="table-wrap section-gap"><table class="dense"><thead><tr>${r.columns.map(c => html`<th>${c}</th>`)}</tr></thead>
          <tbody>${r.rows.map(row => html`<tr>${r.columns.map(c => html`<td class="small">${cell(row[c])}</td>`)}</tr>`)}</tbody></table></div>` : html`<div class="empty">No rows</div>`}
        ${callsBox(r.fulfilledBy)}`);
    } catch (e) { setHTML(out, errorBox(e)); }
  };
  $("#pvRun", host).addEventListener("click", run);
  run();
}
function cell(v) {
  if (v === null || v === undefined) return html`<span class="muted">–</span>`;
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/* ---------- export ---------- */
function exportTab(host) {
  const blocked = !ds.myAccess.canExport;
  setHTML(host, html`<div class="grid grid-2">
    <div class="card"><h2>Retrieve / export</h2>
      ${blocked ? html`<div class="notice">This dataset is <strong>${ds.classification}</strong>. Request access on the <a href="#/marketplace/${encodeURIComponent(ds.id)}/access">Access</a> tab; the owner must approve it before you can export.</div>` : ""}
      <div class="fields section-gap">${filterFields()}
        <div class="field"><label for="ex-format">Format</label><select id="ex-format">${ds.formats.map(f => html`<option value="${f}">${{ csv: "CSV (Excel-friendly)", json: "JSON (with metadata)", ndjson: "NDJSON (one JSON per line)", xlsx: "Excel workbook (.xlsx)" }[f] || f}</option>`)}</select></div>
        <div class="field"><label for="ex-limit">Max rows</label><input id="ex-limit" type="number" min="1" max="${ds.maxExportRows}" value="10000"></div>
      </div>
      <h3 class="section-gap">Fields <button class="btn sm" id="exAll" type="button">All</button> <button class="btn sm" id="exNone" type="button">None</button></h3>
      <div class="field-checks">${ds.schema.map(f => html`<label class="check"><input type="checkbox" data-field="${f.name}" checked> <span class="mono small">${f.name}</span>${f.pii ? html` <span class="badge sev-WARNING">PII</span>` : ""}</label>`)}</div>
      <div class="row section-gap"><button class="btn primary" id="exRun" ${blocked ? raw("disabled") : ""}>Download</button>
        <span class="small muted">The request is audited (who, what, filters, rows).</span></div>
      <div id="exOut"></div>
    </div>
    <div class="card"><h2>Export history <span class="small muted">this dataset</span></h2><div id="exHist">${loading()}</div></div></div>`);
  $("#exAll", host).addEventListener("click", () => $$("[data-field]", host).forEach(c => { c.checked = true; }));
  $("#exNone", host).addEventListener("click", () => $$("[data-field]", host).forEach(c => { c.checked = false; }));
  $("#exRun", host).addEventListener("click", async e => {
    const btn = e.currentTarget;
    const fields = $$("[data-field]", host).filter(c => c.checked).map(c => c.dataset.field);
    if (!fields.length) { toast("Select at least one field", "error"); return; }
    const q = new URLSearchParams({ ...readFilters(host), format: $("#ex-format", host).value, limit: $("#ex-limit", host).value });
    if (fields.length !== ds.schema.length) q.set("fields", fields.join(","));
    btn.disabled = true;
    const r = await download("exp", `/marketplace/v1/datasets/${encodeURIComponent(ds.id)}/export?${q}`, { headers: H() });
    btn.disabled = false;
    if (!r.ok) { setHTML($("#exOut", host), errorBox({ message: (r.problem && (r.problem.detail || r.problem.title)) || `HTTP ${r.status}`, details: [] })); return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(r.blob); a.download = r.filename; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    setHTML($("#exOut", host), html`<div class="notice ok section-gap">Downloaded <strong>${r.filename}</strong> · ${r.headers["x-tfso-row-count"]} rows · ${fmtNum(r.blob.size)} bytes · export id <span class="mono">${r.headers["x-tfso-export-id"]}</span></div>
      ${callsBox((r.headers["x-tfso-fulfilled-by"] || "").split(" | ").filter(Boolean))}`);
    loadHistory(host);
  });
  loadHistory(host);
}
async function loadHistory(host) {
  try {
    const r = await mp("/exports", { query: { datasetId: ds.id } });
    setHTML($("#exHist", host), r.items.length ? html`<div class="table-wrap"><table class="dense"><thead><tr><th>When</th><th>Who</th><th>Format</th><th class="num">Rows</th><th>Filters</th></tr></thead>
      <tbody>${r.items.map(x => html`<tr><td class="nowrap small">${fmtDate(x.createdAt)}</td><td class="small">${x.requester}</td><td>${x.format}</td><td class="num">${fmtNum(x.rowCount)}</td>
        <td class="small mono">${Object.entries(x.filters || {}).map(([k, v]) => `${k}=${v}`).join(" ") || "–"}</td></tr>`)}</tbody></table></div>` : html`<div class="empty">No exports yet</div>`);
  } catch (e) { setHTML($("#exHist", host), errorBox(e)); }
}

/* ---------- access ---------- */
async function access(host, el) {
  const me = identity();
  setHTML(host, html`<div class="grid grid-2">
    <div class="card"><h2>Request access</h2>
      <div class="small muted">Policy: ${ds.accessPolicy === "approval" ? `owner approval required (${ds.owner.email})` : "approved automatically for internal consumers"}.</div>
      <div class="fields section-gap">
        <div class="field"><label>Requester</label><input value="${me.userId}" disabled></div>
        <div class="field"><label for="ar-team">Team</label><input id="ar-team" value="${me.team}"></div>
        <div class="field"><label for="ar-sys">Consuming system</label><input id="ar-sys" placeholder="e.g. Power BI, Databricks job, partner app"></div>
        <div class="field"><label for="ar-level">Access level</label><select id="ar-level"><option value="EXPORT">Read + export</option><option value="READ">Read (API)</option><option value="SUBSCRIBE">Subscribe to events</option></select></div>
      </div>
      <div class="field section-gap"><label for="ar-purpose">Business purpose</label><textarea id="ar-purpose" rows="3" placeholder="What will you use this data for?"></textarea></div>
      <div class="row section-gap"><button class="btn primary" id="arSend">Submit request</button></div>
    </div>
    <div class="card"><h2>Requests for this dataset</h2><div class="small muted">Data owners approve or reject pending requests here.</div><div id="arList" class="section-gap">${loading()}</div></div></div>`);
  $("#arSend", host).addEventListener("click", async () => {
    const purpose = $("#ar-purpose", host).value.trim();
    if (purpose.length < 3) { toast("Describe the business purpose", "error"); return; }
    try {
      const r = await mp(`/datasets/${encodeURIComponent(ds.id)}/access-requests`, { body: { purpose, team: $("#ar-team", host).value, consumerSystem: $("#ar-sys", host).value || null, accessLevel: $("#ar-level", host).value } });
      toast(`Request ${r.requestId}: ${r.status}`);
      await loadDetail(el, ds.id);
    } catch (e) { toast(e.message, "error"); }
  });
  try {
    const r = await mp("/access-requests", { query: { datasetId: ds.id } });
    const list = $("#arList", host);
    setHTML(list, r.items.length ? html`<div class="table-wrap"><table class="dense"><thead><tr><th>Request</th><th>Requester</th><th>Purpose</th><th>Status</th><th></th></tr></thead><tbody>
      ${r.items.map(x => html`<tr><td class="mono small">${x.requestId}<div class="muted">${fmtDate(x.requestedAt)}</div></td>
        <td class="small">${x.requester}<div class="muted">${x.team || ""} ${x.consumerSystem ? "· " + x.consumerSystem : ""}</div></td>
        <td class="small">${x.purpose}</td>
        <td><span class="badge ${x.status === "APPROVED" ? "ok" : x.status === "PENDING" ? "sev-WARNING" : "sev-ERROR"}">${x.status}</span>${x.decidedBy ? html`<div class="small muted">${x.decidedBy}</div>` : ""}</td>
        <td class="nowrap">${x.status === "PENDING" ? html`<button class="btn sm" data-dec="APPROVED" data-id="${x.requestId}">Approve</button> <button class="btn sm" data-dec="REJECTED" data-id="${x.requestId}">Reject</button>`
          : x.status === "APPROVED" ? html`<button class="btn sm" data-dec="REVOKED" data-id="${x.requestId}">Revoke</button>` : ""}</td></tr>`)}</tbody></table></div>` : html`<div class="empty">No requests yet</div>`);
    list.addEventListener("click", async e => {
      const b = e.target.closest("[data-dec]");
      if (!b) return;
      const note = b.dataset.dec === "APPROVED" ? "Approved by data owner" : prompt("Reason:") || "";
      try {
        await mp(`/access-requests/${b.dataset.id}/decision`, { body: { decision: b.dataset.dec, note }, headers: { "x-user-id": ds.owner.email } });
        toast(`${b.dataset.id} ${b.dataset.dec.toLowerCase()}`);
        await loadDetail(el, ds.id);
      } catch (err) { toast(err.message, "error"); }
    });
  } catch (e) { setHTML($("#arList", host), errorBox(e)); }
}

/* ---------- how to consume ---------- */
function consume(host) {
  const s = loadSettings();
  const base = new URL(s.apiBase || "/api", location.href).href.replace(/\/$/, "");
  const c = ds.consumption || {};
  const keyHdr = s.subscriptionKey ? "-H 'Ocp-Apim-Subscription-Key: <key>'" : "-H 'x-api-key: <key>'";
  const exportUrl = `${base}/exp/marketplace/v1/datasets/${ds.id}/export?format=csv`;
  setHTML(host, html`<div class="grid grid-2">
    <div class="card"><h2>Marketplace API</h2>
      <pre class="json">${`# Retrieve the whole dataset as CSV (audited)\ncurl ${keyHdr} -H 'x-user-id: ${identity().userId}' \\\n  '${exportUrl}' -o ${ds.id}.csv\n\n# Preview / data definition / lineage\nGET ${base}/exp/marketplace/v1/datasets/${ds.id}/preview?limit=20\nGET ${base}/exp/marketplace/v1/datasets/${ds.id}/schema\nGET ${base}/exp/marketplace/v1/datasets/${ds.id}/lineage`}</pre>
      <h3 class="section-gap">Source APIs</h3>
      <div class="table-wrap"><table><tbody>${(c.rest || []).map(r => html`<tr><td><span class="badge">${r.method}</span></td><td class="mono small">${base}${r.path}</td><td class="small">${r.description}</td></tr>`)}</tbody></table></div>
    </div>
    <div class="card"><h2>Other ways to consume</h2>
      <div class="kv-grid small">
        ${c.events ? html`<div class="muted">Event stream</div><div>Service Bus topic <span class="mono">${c.events.topic}</span>${c.events.eventHub ? html`, Event Hub <span class="mono">${c.events.eventHub}</span>` : ""}; CloudEvent type <span class="mono">${c.events.type}</span>. Subscribe to push updates (webhooks: <a href="#/webhooks">Webhooks</a>).</div>` : ""}
        ${c.lakehouse ? html`<div class="muted">Lakehouse</div><div>Unity Catalog table <span class="mono">${c.lakehouse.table}</span>${c.lakehouse.sharing ? html`; ${c.lakehouse.sharing}` : ""}</div>` : ""}
        ${c.edi ? html`<div class="muted">EDI</div><div>${c.edi}</div>` : ""}
        ${c.schemaRef ? html`<div class="muted">Contract</div><div class="mono">${c.schemaRef}</div>` : ""}
      </div>
      ${c.lakehouse ? html`<h3 class="section-gap">Databricks SQL</h3><pre class="json">${`SELECT *\nFROM ${c.lakehouse.table}\nLIMIT 100;`}</pre>` : ""}
    </div></div>`);
}
