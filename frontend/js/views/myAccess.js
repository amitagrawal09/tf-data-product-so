// My access: who I am, my Entra ID app roles, the domains and rows I can see, and my access requests.
import { api, items } from "../api.js";
import { html, setHTML, $, errorBox, loading, fmtDate, fmtNum, kpiCard } from "../ui.js";
import { me, domains, isExternal, matchesUser } from "../session.js";
import { initials, roleTag, scopeText } from "../auth.js";
import { subtitle } from "../app.js";
import { statusPill } from "./marketplace.js";

/** Contract §3 - app roles on the "Taylor Farms Data Mart API" Entra app registration. */
export const ROLE_INFO = {
  "DataMart.Admin": "Everything, including the Access & roles admin page",
  "DataMart.Steward": "Approve or reject access requests; see all domains' metadata",
  "DataMart.Export": "Export datasets (without it: preview only)",
  "PII.Read": "See personal data unmasked",
  "Orders.Read": "Order Status API and the Monitor pages (internal)",
  "Orders.Operate": "Create, correct, hold, cancel and resubmit orders; run the simulator",
  "Domain.SalesOrder.Read": "Read datasets in the Sales Order domain",
  "Domain.Customer.Read": "Read datasets in the Customer domain",
  "Domain.Product.Read": "Read datasets in the Product domain",
  "Domain.Logistics.Read": "Read datasets in the Logistics domain",
  "Domain.Vendor.Read": "Read datasets in the Vendor domain",
  "Domain.Procurement.Read": "Read datasets in the Procurement domain",
  "Domain.Finance.Read": "Read datasets in the Finance domain",
  "External.Customer": "External customer; rows restricted to your customer account(s)",
  "External.Vendor": "External vendor; rows restricted to your vendor account(s)",
};

const yes = (v, label) => html`<span class="pill ${v ? "good" : ""}" title="${label}">${v ? "Yes" : "No"}</span>`;

export async function mount(el) {
  const p = me();
  const sc = scopeText(p.scope);
  setHTML(el, html`
    <div class="page-head"><div><h1>My access</h1><div class="sub">${subtitle()}</div></div>
      <a class="btn" href="#/marketplace">Browse the catalog</a></div>
    <div class="card"><div class="access-summary">
      <span class="avatar">${initials(p.name || p.email || p.id)}</span>
      <div style="min-width:0;flex:1"><h2 style="margin:0">${p.name || p.id}</h2>
        <div class="muted" style="overflow-wrap:anywhere">${p.email || ""}</div>
        <div class="row small" style="margin-top:4px">
          <span class="badge ${isExternal(p) ? "aud-external-customer" : "aud-internal"}">${p.userType === "external" ? "External partner" : p.userType === "service" ? "Service principal" : "Internal"}</span>
          <span class="auth-pill" style="background:var(--gray-soft);color:var(--text);border-color:var(--border)">auth: ${p.authMode}</span>
          ${p.persona ? html`<span class="small muted">persona <span class="mono">${p.persona}</span></span>` : ""}
          <span class="small muted">id <span class="mono">${p.id}</span></span></div></div>
    </div></div>
    <div class="kpis section-gap">
      ${kpiCard({ label: "Domains", value: fmtNum(p.domains.length), state: p.domains.length ? "ok" : "breach", hint: "you can read" })}
      ${kpiCard({ label: "Export", value: p.canExport || isExternal(p) ? "Yes" : "No", state: p.canExport || isExternal(p) ? "ok" : "info", hint: isExternal(p) ? "your own rows" : "DataMart.Export" })}
      ${kpiCard({ label: "Personal data", value: p.piiAccess ? "Unmasked" : "Masked", hint: "PII.Read" })}
      ${kpiCard({ label: "Row scope", value: sc ? "Restricted" : "All rows", hint: sc || "No row-level restriction" })}
    </div>
    <div class="grid grid-2 section-gap">
      <div class="card"><div class="panel-head"><h2>My roles</h2><span class="panel-hint">Entra ID app roles on the Data Mart API</span></div>
        <div class="table-wrap"><table><thead><tr><th>Role</th><th>What it allows</th></tr></thead><tbody>
        ${p.roles.length ? p.roles.map(r => html`<tr><td>${roleTag(r)}</td><td class="small">${ROLE_INFO[r] || (r === "*" ? "Service principal: all APIs" : "Custom role")}</td></tr>`)
          : html`<tr><td colspan="2" class="empty">No app roles assigned. Ask your manager to assign one in Entra ID.</td></tr>`}</tbody></table></div>
        <h3 class="section-gap">Capabilities</h3>
        <div class="table-wrap"><table class="dense"><tbody>
          <tr><td>Monitor orders (Orders.Read)</td><td class="num">${yes(p.canReadOrders, "canReadOrders")}</td></tr>
          <tr><td>Operate orders (Orders.Operate)</td><td class="num">${yes(p.canOperateOrders, "canOperateOrders")}</td></tr>
          <tr><td>Export datasets</td><td class="num">${yes(p.canExport, "canExport")}</td></tr>
          <tr><td>See personal data unmasked</td><td class="num">${yes(p.piiAccess, "piiAccess")}</td></tr>
          <tr><td>Approve access requests (steward)</td><td class="num">${yes(p.isSteward || p.isAdmin, "isSteward")}</td></tr>
          <tr><td>Administer the data mart</td><td class="num">${yes(p.isAdmin, "isAdmin")}</td></tr>
        </tbody></table></div>
      </div>
      <div class="card"><div class="panel-head"><h2>Accessible domains</h2><span class="panel-hint">${sc ? `Rows limited to ${sc}` : "Unrestricted rows"}</span></div>
        <div id="doms">${loading()}</div></div>
    </div>
    <div class="card section-gap"><div class="panel-head"><h2>My access requests</h2><a class="small" href="#/marketplace">Request access from a dataset →</a></div>
      <div id="reqs">${loading()}</div></div>`);

  const doms = await domains();
  setHTML($("#doms", el), doms.length ? html`<div class="table-wrap"><table><thead><tr><th>Domain</th><th class="num">Datasets</th><th>Access</th></tr></thead><tbody>
    ${doms.map(d => html`<tr class="clickable" data-dom="${d.domainId}"><td><strong>${d.name}</strong><div class="small muted mono">${d.domainId}</div></td>
      <td class="num">${fmtNum(d.accessibleDatasets ?? 0)} / ${fmtNum(Array.isArray(d.datasets) ? d.datasets.length : d.datasets ?? 0)}</td>
      <td>${d.accessible === false || !p.domains.includes(d.domainId) && !(d.accessibleDatasets > 0)
        ? html`<span class="pill bad">Locked</span>` : html`<span class="pill good">Read</span>`}</td></tr>`)}</tbody></table></div>`
    : html`<div class="empty">${p.domains.length ? p.domains.join(", ") : "No domains"}</div>`);
  $("#doms", el).querySelectorAll("tr[data-dom]").forEach(tr => tr.addEventListener("click", () => { location.hash = `#/marketplace?domain=${encodeURIComponent(tr.dataset.dom)}`; }));

  const host = $("#reqs", el);
  try {
    const r = await api.exp("/marketplace/v1/access-requests", { query: { requester: p.email || p.id } });
    const list = items(r).filter(x => matchesUser(x.requester));
    setHTML(host, list.length ? html`<div class="table-wrap"><table><thead><tr><th>Request</th><th>Dataset</th><th>Level</th><th>Purpose</th><th>Status</th><th>Decision</th></tr></thead><tbody>
      ${list.map(x => html`<tr><td class="mono small">${x.requestId}<div class="muted">${fmtDate(x.requestedAt)}</div></td>
        <td><a class="mono" href="#/marketplace/${encodeURIComponent(x.datasetId)}">${x.datasetId}</a></td>
        <td class="small">${x.accessLevel || "–"}</td><td class="small">${x.purpose}</td><td>${statusPill(x.status)}</td>
        <td class="small">${x.decidedBy ? html`${x.decidedBy}<div class="muted">${fmtDate(x.decidedAt)}${x.decisionNote ? ` · ${x.decisionNote}` : ""}</div>` : "–"}</td></tr>`)}</tbody></table></div>`
      : html`<div class="empty">You have not requested access to any dataset.</div>`);
  } catch (e) { setHTML(host, errorBox(e)); }
}
