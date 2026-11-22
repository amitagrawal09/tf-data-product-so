// Settings → Single sign-on (DataMart.Admin). Connects the data mart to Microsoft Entra ID without a redeploy:
//   1 register the apps in Entra ID   2 enter their IDs (draft)   3 check the tenant
//   4 test sign-in against the draft (token must carry DataMart.Admin)   5 enable for everyone
// Backend: /exp/admin/v1/sso* (docs/09-connect-entra-id.md).
import { api } from "../api.js";
import { html, raw, setHTML, $, toast, errorBox, loading } from "../ui.js";
import { startTestSignIn, takeTestSignIn, authConfig, signOut } from "../auth.js";

const F = [
  { k: "tenantId", label: "Directory (tenant) ID", ph: "00000000-0000-0000-0000-000000000000",
    help: "Entra admin center → Overview → Tenant ID" },
  { k: "apiClientId", label: "API application (client) ID", ph: "GUID of “Taylor Farms Data Mart API”",
    help: "App registrations → Taylor Farms Data Mart API → Application (client) ID" },
  { k: "spaClientId", label: "Console application (client) ID", ph: "GUID of “Taylor Farms Data Mart Console”",
    help: "App registrations → Taylor Farms Data Mart Console → Application (client) ID" },
];
const FX = [
  { k: "externalAuthority", label: "External ID authority", ph: "https://<tenant>.ciamlogin.com/<tenant-id>", help: "Customers & vendors (optional)" },
  { k: "externalIssuer", label: "External ID issuer", ph: "https://<tenant-id>.ciamlogin.com/<tenant-id>/v2.0", help: "Must match the token “iss”" },
  { k: "externalClientId", label: "External console client ID", ph: "Blank = same as console", help: "SPA app in the External ID tenant" },
  { k: "externalApiClientId", label: "External API client ID", ph: "GUID", help: "API app in the External ID tenant (token audience)" },
  { k: "claimCustomer", label: "Customer id claim", ph: "extension_customerId", help: "Row scope for customers" },
  { k: "claimVendor", label: "Vendor id claim", ph: "extension_vendorId", help: "Row scope for vendors" },
  { k: "claimRegions", label: "Regions claim", ph: "extension_regions", help: "Row scope for regional staff" },
];

const badge = (ok, yes = "OK", no = "Failed") => html`<span class="pill ${ok ? "good" : "bad"}">${ok ? yes : no}</span>`;
const stateBadge = st => ({
  NONE: html`<span class="badge">Not started</span>`,
  INVALID: html`<span class="badge sev-ERROR">Incomplete</span>`,
  UNVERIFIED: html`<span class="badge sev-WARNING">Saved · not tested</span>`,
  VERIFIED: html`<span class="badge ok">Tested · ready to enable</span>`,
})[st] || st;

function copyBtn(text, label = "Copy") {
  return html`<button class="btn sm" type="button" data-copy="${text}">${label}</button>`;
}

export async function mount(host) {
  setHTML(host, loading("Loading single sign-on settings…"));
  let data;
  try { data = await api.exp("/admin/v1/sso"); } catch (e) { setHTML(host, errorBox(e)); return; }

  // Coming back from a test sign-in → send the token to the backend for validation.
  const test = takeTestSignIn();
  let testResult = null;
  if (test) {
    if (test.error) testResult = { ok: false, message: `Microsoft sign-in failed: ${test.error}` };
    else {
      try {
        const v = await api.exp("/admin/v1/sso/verify", { body: { accessToken: test.access_token } });
        testResult = v.verification;
        data = await api.exp("/admin/v1/sso");
      } catch (e) { testResult = { ok: false, message: (e.problem && e.problem.detail) || e.message }; }
    }
  }
  render(host, data, testResult);
}

function render(host, data, testResult) {
  const eff = data.effective, draft = data.draft || {}, st = data.draftStatus || { state: "NONE" };
  const cfg = { ...pickEditable(eff), ...draft };
  const mode = (data.draft ? draft.mode : "entra") === "dev" ? "dev" : "entra";
  const redirect = authConfig().redirectUri;
  const ro = data.locked;
  const gm = cfg.groupRoleMap ? JSON.stringify(cfg.groupRoleMap, null, 2) : "";
  const v = (st.verification || testResult) || null;

  setHTML(host, html`
    <div class="card">
      <div class="panel-head"><h2>Single sign-on · Microsoft Entra ID</h2>
        <span>${eff.mode === "entra" ? html`<span class="pill good">SSO enabled</span>` : html`<span class="pill bad">Test personas</span>`}</span></div>
      <dl class="kv">
        <dt>Sign-in now</dt><dd>${eff.mode === "entra" ? html`Microsoft Entra ID · tenant <span class="mono">${eff.tenantId}</span>` : html`Test personas (<span class="mono">dev</span>) - anyone who can reach this console can pick any persona`}</dd>
        <dt>Configured by</dt><dd>${eff.source === "settings" ? html`Settings · ${eff.updatedBy || "?"} · ${eff.updatedAt || ""}` : "Deployment (environment variables)"}</dd>
        <dt>Environment</dt><dd class="mono">${data.env}${data.isProd ? " (production: Entra ID only)" : ""}</dd>
      </dl>
      ${ro ? html`<div class="alert warn section-gap">This deployment sets <span class="mono">TFSO_SSO_SETTINGS=locked</span>: single sign-on is managed by the deployment (<span class="mono">./deploy.sh azure</span>) and is read-only here.</div>` : ""}
      ${eff.source === "settings" && !ro ? html`<div class="row section-gap"><button class="btn sm" id="ssoReset" type="button">Revert to deployment settings</button>
        <span class="small muted">Break-glass from a terminal: <span class="mono">./deploy.sh sso-reset</span></span></div>` : ""}
    </div>

    <div class="card section-gap sso-step">
      <div class="panel-head"><h2><span class="step-no">1</span> Register two apps in Entra ID</h2><span class="small muted">once per tenant · ~10 min</span></div>
      <p class="small">Follow <strong>docs/09-connect-entra-id.md</strong> (portal, step by step) or run <span class="mono">scripts/entra-setup.sh --console-url ${redirect}</span> in Azure Cloud Shell, which does all of this for you.</p>
      <ol class="small steps">
        <li><strong>Taylor Farms Data Mart API</strong> - Expose an API → Application ID URI <span class="mono">api://&lt;client-id&gt;</span>, scope <span class="mono">access_as_user</span>; Manifest → <span class="mono">"requestedAccessTokenVersion": 2</span>; paste the app roles below into <span class="mono">appRoles</span>.</li>
        <li><strong>Taylor Farms Data Mart Console</strong> - Authentication → Add a platform → <strong>Single-page application</strong> with this redirect URI:
          <div class="row" style="margin:6px 0"><span class="mono copy-box">${redirect}</span>${copyBtn(redirect)}</div>
          API permissions → My APIs → Data Mart API → <span class="mono">access_as_user</span> → Grant admin consent.</li>
        <li>Enterprise applications → <strong>Taylor Farms Data Mart API</strong> → Users and groups → assign <strong>yourself</strong> <span class="mono">DataMart.Admin</span> (and groups to the other roles).</li>
      </ol>
      <details class="section-gap"><summary>App roles for the API manifest (${data.appRoles.length}) - same ids as the setup script</summary>
        <div class="row" style="margin:8px 0">${copyBtn(JSON.stringify(data.appRoles, null, 2), "Copy appRoles JSON")}
          <button class="btn sm" type="button" id="dlRoles">Download</button></div>
        <pre class="json" style="max-height:260px">${JSON.stringify(data.appRoles, null, 2)}</pre></details>
    </div>

    <form class="card section-gap sso-step" id="ssoForm">
      <div class="panel-head"><h2><span class="step-no">2</span> Enter the IDs</h2>${stateBadge(st.state)}</div>
      <div class="fields">
        <div class="field"><label for="sso-mode">Sign-in method</label>
          <select id="sso-mode" ${ro ? "disabled" : ""}>
            <option value="entra" ${mode === "entra" ? "selected" : ""}>Microsoft Entra ID (single sign-on)</option>
            ${data.isProd ? "" : html`<option value="dev" ${mode === "dev" ? "selected" : ""}>Test personas (non-production)</option>`}
          </select></div>
        ${F.map(f => field(f, cfg, ro))}
      </div>
      <details class="section-gap" ${cfg.externalAuthority || cfg.groupRoleMap ? "open" : ""}><summary>Customers &amp; vendors (Entra External ID), claims and group mapping - optional</summary>
        <div class="fields section-gap">${FX.map(f => field(f, cfg, ro))}
          <div class="field" style="grid-column:1/-1"><label for="sso-groupRoleMap">Group → role mapping (JSON, optional)</label>
            <textarea id="sso-groupRoleMap" rows="3" placeholder='{"<group object id>": "Domain.Finance.Read"}' ${ro ? "disabled" : ""}>${gm}</textarea>
            <span class="small muted">Only needed if you send group claims instead of assigning groups to app roles.</span></div>
        </div></details>
      ${st.errors && st.errors.length ? html`<div class="alert warn section-gap">${st.errors.map(e => html`<div>${e.message}</div>`)}</div>` : ""}
      <div class="row section-gap"><button class="btn primary" type="submit" ${ro ? "disabled" : ""}>Save draft</button>
        <span class="small muted">Saving does not change how anyone signs in yet.</span></div>
    </form>

    <div class="card section-gap sso-step">
      <div class="panel-head"><h2><span class="step-no">3</span> Check the connection</h2>
        <button class="btn sm" id="ssoCheck" type="button" ${!data.draft || ro ? "disabled" : ""}>Check</button></div>
      <div id="checks" class="small muted">The server looks up your tenant's sign-in metadata to confirm the IDs and network access.</div>
    </div>

    <div class="card section-gap sso-step">
      <div class="panel-head"><h2><span class="step-no">4</span> Test sign-in</h2>
        <button class="btn sm" id="ssoTest" type="button" ${st.state === "NONE" || st.state === "INVALID" || cfg.mode !== "entra" || ro ? "disabled" : ""}>Sign in with Microsoft to test</button></div>
      <p class="small muted">Signs you in against the saved draft in this tab only and checks the token: signature, issuer, audience, and that it carries <span class="mono">DataMart.Admin</span> - so you cannot lock yourself out.</p>
      ${v ? html`<div class="alert ${v.ok ? "ok" : "warn"} section-gap">${badge(v.ok, "Passed", "Not passed")} ${v.message || ""}
        ${v.roles && v.roles.length ? html`<div class="small" style="margin-top:6px">Roles in token: ${v.roles.map(r => html`<span class="mono">${r}</span> `)}</div>` : ""}</div>` : ""}
    </div>

    <div class="card section-gap sso-step">
      <div class="panel-head"><h2><span class="step-no">5</span> Enable for everyone</h2>
        <button class="btn primary" id="ssoEnable" type="button" ${st.canActivate ? "" : "disabled"}>${cfg.mode === "entra" ? "Enable single sign-on" : "Switch to test personas"}</button></div>
      <p class="small muted">All three APIs enforce the new settings within ~10 seconds; the console then asks everyone to sign in with Microsoft. ${cfg.mode === "entra" && !st.canActivate ? "Pass the test sign-in first." : ""}</p>
    </div>`);

  host.querySelectorAll("[data-copy]").forEach(b => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copied", "ok"); } catch { toast("Copy blocked by the browser - select and copy instead", "error"); }
  }));
  const dl = $("#dlRoles", host);
  if (dl) dl.addEventListener("click", () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data.appRoles, null, 2)], { type: "application/json" }));
    a.download = "datamart-app-roles.json"; a.click();
  });

  const modeSel = $("#sso-mode", host);
  $("#ssoForm", host).addEventListener("submit", async e => {
    e.preventDefault();
    const body = { mode: modeSel.value };
    for (const f of [...F, ...FX]) body[f.k] = ($(`#sso-${f.k}`, host).value || "").trim() || null;
    const raw = ($("#sso-groupRoleMap", host).value || "").trim();
    if (raw) { try { body.groupRoleMap = JSON.parse(raw); } catch { toast("Group mapping is not valid JSON", "error"); return; } }
    else body.groupRoleMap = null;
    try {
      await api.exp("/admin/v1/sso", { method: "PUT", body });
      toast("Draft saved - now check and test it", "ok");
      mount(host);
    } catch (err) { toast((err.problem && err.problem.detail) || err.message, "error"); }
  });

  const chk = $("#ssoCheck", host);
  if (chk) chk.addEventListener("click", async () => {
    setHTML($("#checks", host), loading("Checking…"));
    try {
      const r = await api.exp("/admin/v1/sso/check", { method: "POST" });
      setHTML($("#checks", host), html`<table class="dense"><tbody>${r.checks.map(c => html`<tr><td>${badge(c.ok)}</td><td><strong>${c.name}</strong><div class="small muted" style="overflow-wrap:anywhere">${c.detail}</div></td></tr>`)}</tbody></table>`);
    } catch (err) { setHTML($("#checks", host), errorBox(err)); }
  });

  const tst = $("#ssoTest", host);
  if (tst) tst.addEventListener("click", () => startTestSignIn(st.public).catch(err => toast(err.message, "error")));

  const en = $("#ssoEnable", host);
  if (en) en.addEventListener("click", async () => {
    const msg = cfg.mode === "entra"
      ? "Enable Microsoft Entra ID single sign-on for everyone? Test personas will stop working and you will sign in again with Microsoft."
      : "Switch back to test personas? Anyone who can reach this console will be able to pick any persona.";
    if (!confirm(msg)) return;
    try {
      await api.exp("/admin/v1/sso/activate", { method: "POST" });
      toast("Saved - signing you in again", "ok");
      setTimeout(() => signOut(), 800);
    } catch (err) { toast((err.problem && err.problem.detail) || err.message, "error"); }
  });

  const rs = $("#ssoReset", host);
  if (rs) rs.addEventListener("click", async () => {
    if (!confirm("Revert to the deployment's sign-in settings?")) return;
    try { await api.exp("/admin/v1/sso/reset", { method: "POST" }); toast("Reverted - signing you in again", "ok"); setTimeout(() => signOut(), 800); }
    catch (err) { toast((err.problem && err.problem.detail) || err.message, "error"); }
  });
}

function pickEditable(eff) {
  const o = {};
  ["mode", "tenantId", "apiClientId", "spaClientId", "externalIssuer", "externalAuthority", "externalClientId",
    "externalApiClientId", "claimCustomer", "claimVendor", "claimRegions", "groupRoleMap"].forEach(k => { o[k] = eff[k]; });
  return o;
}

function field(f, cfg, ro) {
  return html`<div class="field"><label for="sso-${f.k}">${f.label}</label>
    <input id="sso-${f.k}" value="${cfg[f.k] || ""}" placeholder="${f.ph}" autocomplete="off" spellcheck="false" ${ro ? "disabled" : ""}>
    <span class="small muted">${f.help}</span></div>`;
}

export { raw };
