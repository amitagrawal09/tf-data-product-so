// Global operating scope: National (all regions) → Region → Site, plus National / Regional accounts.
// Rendered in the filter card under the header.
import { scope, setScope, regions } from "./api.js";
import { html, setHTML } from "./ui.js";

let regionList = [];

export const ACCOUNT_LABEL = { "": "All accounts", NATIONAL: "National accounts", REGIONAL: "Regional accounts" };

export function regionName(id) {
  const r = regionList.find(x => x.regionId === id);
  return r ? r.name : id || "";
}
export function siteName(id) {
  for (const r of regionList) {
    const s = (r.sites || []).find(x => x.warehouse === id || x.siteId === id);
    if (s) return s.name || s.warehouse;
  }
  return id || "";
}
/** Region a site belongs to (used when a site is chosen directly). */
export function regionOfSite(id) {
  const r = regionList.find(x => (x.sites || []).some(s => s.warehouse === id || s.siteId === id));
  return r ? r.regionId : "";
}
export function regionsLoaded() { return regionList; }

/** "West · Salinas Processing · National accounts" */
export function scopeLabel(s = scope()) {
  const parts = [s.region ? regionName(s.region) : "National (all regions)"];
  if (s.site) parts.push(siteName(s.site));
  if (s.accountScope) parts.push(ACCOUNT_LABEL[s.accountScope] || s.accountScope);
  return parts.join(" · ");
}

/** Small pill for scoped pages. */
export function scopePill() {
  const s = scope();
  const scoped = Boolean(s.region || s.site || s.accountScope);
  return html`<span class="scope-pill${scoped ? " scoped" : ""}" title="Operating scope - change it in the filter bar">Scope: ${scopeLabel(s)}</span>`;
}

function controls(prefix) {
  const opts = regionList.map(r => html`<optgroup label="${r.name}">
      <option value="region:${r.regionId}">${r.name} region</option>
      ${(r.sites || []).map(site => html`<option value="site:${site.warehouse || site.siteId}">· ${site.name || site.warehouse}${site.city ? ` (${site.city})` : ""}</option>`)}
    </optgroup>`);
  return html`<div class="fc-field"><label for="${prefix}-region">Scope</label>
      <select id="${prefix}-region" aria-label="Operating scope: national, region or site">
        <option value="">National (all regions)</option>${opts}</select></div>
    <div class="fc-field"><label for="${prefix}-acct">Accounts</label>
      <select id="${prefix}-acct" aria-label="Account scope">
        ${Object.entries(ACCOUNT_LABEL).map(([k, l]) => html`<option value="${k}">${l}</option>`)}</select></div>`;
}

function render() {
  for (const [hostId, prefix] of [["scopeBar", "sb"], ["scopeNav", "sn"]]) {
    const host = document.getElementById(hostId);
    if (!host) continue;
    const s = scope();
    setHTML(host, controls(prefix));
    const sel = host.querySelector(`#${prefix}-region`);
    sel.value = s.site ? `site:${s.site}` : s.region ? `region:${s.region}` : "";
    host.querySelector(`#${prefix}-acct`).value = s.accountScope || "";
    host.querySelector(`#${prefix}-region`).addEventListener("change", e => {
      const v = e.target.value;
      if (!v) setScope({ region: "", site: "" });
      else if (v.startsWith("region:")) setScope({ region: v.slice(7), site: "" });
      else { const site = v.slice(5); setScope({ region: regionOfSite(site), site }); }
    });
    host.querySelector(`#${prefix}-acct`).addEventListener("change", e => setScope({ accountScope: e.target.value }));
  }
}

/** Load regions, render the selectors and keep them in sync with scope changes. */
export async function initScope() {
  render();
  regionList = await regions();
  render();
  window.addEventListener("tfso:scope", render);
}

/** Programmatic drill-down (dashboard rows). */
export function drillTo({ region, site }) {
  if (site) setScope({ region: region || regionOfSite(site), site });
  else setScope({ region: region || "", site: "" });
}
