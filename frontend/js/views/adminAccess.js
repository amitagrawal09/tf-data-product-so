// Access & roles (DataMart.Admin / DataMart.Steward): app role catalogue, role × domain matrix, dev personas and the
// approval queue for pending dataset access requests.
import { api, items } from "../api.js";
import { html, setHTML, $, errorBox, loading, fmtDate, toast, kpiCard, fmtNum, limitedTable, wireLimited } from "../ui.js";
import { me, matchesUser } from "../session.js";
import { roleTag, scopeText, authMode, initials } from "../auth.js";
import { subtitle } from "../app.js";
import { ROLE_INFO } from "./myAccess.js";

export async function mount(el) {
  const p = me();
  setHTML(el, html`
    <div class="page-head"><div><h1>Access &amp; roles</h1><div class="sub">${subtitle()}</div></div>
      <button class="btn" id="reload">Refresh</button></div>
    <div id="tiles"></div>
    <div class="card section-gap"><div class="panel-head"><h2>Approval queue</h2>
      <span class="panel-hint">Pending dataset access requests${p.isAdmin ? "" : " (you approve as data steward)"}</span></div>
      <div id="queue">${loading()}</div></div>
    <div class="card section-gap"><div class="panel-head"><h2>Role × domain</h2>
      <span class="panel-hint">Which app roles read which domains. Confidential datasets also need an approved request.</span></div>
      <div id="matrix">${loading()}</div></div>
    <div class="grid grid-2 section-gap">
      <div class="card"><div class="panel-head"><h2>App roles</h2><span class="panel-hint">Entra app registration "Taylor Farms Data Mart API"</span></div>
        <div id="roles">${loading()}</div></div>
      <div class="card"><div class="panel-head"><h2>Test personas</h2><span class="panel-hint">${authMode() === "dev" ? "dev sign-in mode only" : "not available with Entra ID"}</span></div>
        <div id="personas">${loading()}</div></div>
    </div>`);
  $("#reload", el).addEventListener("click", () => mount(el));
  await Promise.all([loadQueue(el), loadMatrix(el), loadRoles(el)]);
}

async function loadQueue(el) {
  const host = $("#queue", el);
  try {
    const all = items(await api.exp("/marketplace/v1/access-requests", { query: { status: "PENDING" } }));
    const list = all.filter(x => x.status === "PENDING" && matchesUser(x.requester, x.team));
    setHTML($("#tiles", el), html`<div class="kpis">
      ${kpiCard({ label: "Pending requests", value: fmtNum(list.length), state: list.length ? "breach" : "ok", hint: list.length ? "Awaiting a decision" : "Queue is clear" })}
      ${kpiCard({ label: "Oldest pending", value: list.length ? ageText(list.reduce((a, x) => (a && a < x.requestedAt ? a : x.requestedAt), null)) : "–", hint: "since requested" })}
    </div>`);
    setHTML(host, list.length ? limitedTable(html`<tr><th>Request</th><th>Dataset</th><th>Requester</th><th>Level</th><th>Purpose</th><th>Decision</th></tr>`,
      list.map(x => html`<tr data-row="${x.requestId}"><td class="mono small">${x.requestId}<div class="muted">${fmtDate(x.requestedAt)}</div></td>
        <td><a class="mono" href="#/marketplace/${encodeURIComponent(x.datasetId)}">${x.datasetId}</a></td>
        <td class="small">${x.requester}<div class="muted">${x.team || ""}${x.consumerSystem ? ` · ${x.consumerSystem}` : ""}</div></td>
        <td class="small">${x.accessLevel || "–"}</td><td class="small">${x.purpose}</td>
        <td class="nowrap"><input aria-label="Decision note for ${x.requestId}" data-note="${x.requestId}" placeholder="Note (optional)" style="min-height:30px;max-width:170px;margin-bottom:4px">
          <div class="row" style="gap:4px"><button class="btn sm primary" data-dec="APPROVED" data-id="${x.requestId}">Approve</button>
          <button class="btn sm danger" data-dec="REJECTED" data-id="${x.requestId}">Reject</button></div></td></tr>`), { limit: 10 })
      : html`<div class="empty">No pending requests 🎉</div>`);
    wireLimited(host);
    host.onclick = async e => {
      const b = e.target.closest("[data-dec]");
      if (!b) return;
      const note = (host.querySelector(`[data-note="${b.dataset.id}"]`) || {}).value || (b.dataset.dec === "APPROVED" ? "Approved by data steward" : "");
      if (b.dataset.dec === "REJECTED" && !note) { toast("Add a note explaining the rejection", "error"); return; }
      b.disabled = true;
      try {
        await api.exp(`/marketplace/v1/access-requests/${encodeURIComponent(b.dataset.id)}/decision`, { body: { decision: b.dataset.dec, note } });
        toast(`${b.dataset.id} ${b.dataset.dec.toLowerCase()}`, "ok");
        await loadQueue(el);
      } catch (err) { b.disabled = false; toast(err.message, "error"); }
    };
  } catch (e) { setHTML(host, errorBox(e)); }
}

function ageText(ts) {
  if (!ts) return "–";
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min`;
  if (s < 86400) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

/** Role → domain ids it reads; accepts {domains:{id:bool}} or {domains:[ids]}. */
function roleDomains(r) {
  const d = r.domains;
  if (Array.isArray(d)) return new Set(d);
  if (d && typeof d === "object") return new Set(Object.entries(d).filter(([, v]) => v).map(([k]) => k));
  return new Set();
}

let personasCache = [];
async function loadMatrix(el) {
  const host = $("#matrix", el);
  try {
    const m = await api.exp("/admin/v1/role-matrix");
    const doms = m.domains || [];
    const roles = (m.roles || []).map(r => (typeof r === "string" ? { role: r } : r));
    personasCache = items(m.personas);
    setHTML(host, html`<div class="table-wrap"><table class="matrix dense"><thead><tr><th>Role</th>${doms.map(d => html`<th class="cell">${d.name}</th>`)}</tr></thead>
      <tbody>${roles.map(r => {
        const set = roleDomains(r);
        return html`<tr><td>${roleTag(r.role)}</td>${doms.map(d => html`<td class="cell">${set.has(d.domainId)
          ? html`<span class="yes" title="${r.role} reads ${d.name}">✓</span>` : html`<span class="no" aria-label="no">·</span>`}</td>`)}</tr>`;
      })}</tbody></table></div>
      <div class="small muted section-gap">External customers and vendors read only the datasets shared with their audience, scoped to their own rows.</div>`);
  } catch (e) { setHTML(host, errorBox(e)); }
  renderPersonas(el);
}

async function renderPersonas(el) {
  const host = $("#personas", el);
  if (authMode() !== "dev") { setHTML(host, html`<div class="empty">Users sign in with Entra ID. Assign app roles to users and groups in the Entra admin center (Enterprise applications → Taylor Farms Data Mart API → Users and groups).</div>`); return; }
  let list = personasCache;
  if (!list.length) {
    try { list = items(await api.exp("/auth/v1/personas")); } catch (e) { setHTML(host, errorBox(e)); return; }
  }
  const cur = me().persona;
  setHTML(host, list.length ? html`<div class="table-wrap"><table class="dense"><thead><tr><th>Persona</th><th>Roles</th><th>Row scope</th></tr></thead><tbody>
    ${list.map(x => html`<tr${x.id === cur ? html` class="selected"` : ""}><td><div class="row" style="gap:8px;flex-wrap:nowrap"><span class="avatar" style="width:26px;height:26px;font-size:10px">${initials(x.name)}</span>
      <div><strong>${x.name}</strong>${x.id === cur ? html` <span class="badge ok">you</span>` : ""}<div class="small muted mono">${x.id}</div><div class="small muted">${x.title || ""}${x.userType === "external" ? " · external" : ""}</div></div></div></td>
      <td><div class="chips">${(x.roles || []).map(roleTag)}</div></td><td class="small">${scopeText(x.scope) || "–"}</td></tr>`)}</tbody></table></div>`
    : html`<div class="empty">No personas</div>`);
}

async function loadRoles(el) {
  const host = $("#roles", el);
  try {
    const list = items(await api.exp("/admin/v1/roles"));
    const order = { platform: 0, domain: 1, external: 2 };
    list.sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9));
    setHTML(host, html`<div class="table-wrap"><table class="dense"><thead><tr><th>Role</th><th>Kind</th><th>Meaning</th></tr></thead><tbody>
      ${list.map(r => html`<tr><td>${roleTag(r.role)}</td><td class="small">${r.kind || "–"}</td><td class="small">${r.description || ROLE_INFO[r.role] || ""}</td></tr>`)}</tbody></table></div>`);
  } catch (e) { setHTML(host, errorBox(e)); }
}
