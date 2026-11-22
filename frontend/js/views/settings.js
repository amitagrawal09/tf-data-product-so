import { loadSettings, saveSettings, clearSettings } from "../api.js";
import { html, setHTML, $, toast } from "../ui.js";
import { refreshEnvChip, subtitle } from "../app.js";
import { authConfig, signOut } from "../auth.js";
import { me } from "../session.js";
import * as sso from "./ssoSettings.js";

export async function mount(el) {
  const s = loadSettings();
  const cfg = window.TFSO_CONFIG || {};
  const a = authConfig();
  setHTML(el, html`
    <div class="page-head"><div><h1>Settings</h1><div class="sub">${subtitle()}</div></div></div>
    <form class="card" id="f">
      <div class="fields">
        <div class="field" style="grid-column: span 2"><label for="apiBase">API base URL</label>
          <input id="apiBase" value="${s.apiBase}" placeholder="/api or https://apim-xxx.azure-api.net"></div>
        <div class="field"><label for="apiKey">API key (x-api-key)</label><input id="apiKey" value="${s.apiKey}" autocomplete="off"></div>
        <div class="field"><label for="subKey">APIM subscription key</label><input id="subKey" type="password" value="${s.subscriptionKey}" autocomplete="off"></div>
      </div>
      <div class="row section-gap">
        <button class="btn primary" type="submit">Save</button>
        <button class="btn" type="button" id="reset">Reset to config.js</button>
      </div>
      <p class="small muted">Overrides saved in this browser take precedence over <span class="mono">config.js</span>.</p>
      <p class="small muted section-gap">config.js defaults: apiBase <span class="mono">${cfg.apiBase || "(unset)"}</span>,
        apiKey ${cfg.apiKey ? "set" : "not set"}, subscriptionKey ${cfg.subscriptionKey ? "set" : "not set"}.</p>
      <p class="small muted">Local: <span class="mono">/api</span> (gateway on :8080). Azure: the APIM gateway URL; deploy.sh writes it into config.js.</p>
    </form>
    <div class="card section-gap"><div class="panel-head"><h2>Sign-in</h2><button class="btn sm" type="button" id="so">Sign out</button></div>
      <dl class="kv">
        <dt>Mode</dt><dd class="mono">${a.mode}</dd>
        <dt>Signed in as</dt><dd>${(me() || {}).email || (me() || {}).id || "–"}</dd>
        ${a.mode === "entra" ? html`<dt>Authority</dt><dd class="mono">${a.authority || "(not set)"}</dd>
          <dt>Client id</dt><dd class="mono">${a.clientId || "(not set)"}</dd>
          <dt>External ID</dt><dd class="mono">${a.externalAuthority || "(not configured)"}</dd>
          <dt>Scopes</dt><dd class="mono">${a.scopes.join(" ")}</dd>
          <dt>Redirect URI</dt><dd class="mono">${a.redirectUri}</dd>` : ""}
      </dl>
      <p class="small muted section-gap">${(me() || {}).isAdmin ? "Administrators connect Microsoft Entra ID below." : "Single sign-on is configured by a Data Mart administrator."} Sign-in settings cannot be overridden in the browser.</p></div>
    ${(me() || {}).isAdmin ? html`<div class="section-gap" id="ssoHost"></div>` : ""}`);
  $("#so", el).addEventListener("click", () => signOut());
  const ssoHost = $("#ssoHost", el);
  if (ssoHost) sso.mount(ssoHost);
  $("#f", el).addEventListener("submit", e => {
    e.preventDefault();
    const ok = saveSettings({ apiBase: $("#apiBase", el).value.trim(), apiKey: $("#apiKey", el).value.trim(), subscriptionKey: $("#subKey", el).value.trim() });
    refreshEnvChip();
    toast(ok ? "Settings saved" : "Could not save (browser storage blocked)", ok ? "ok" : "error");
  });
  $("#reset", el).addEventListener("click", () => { clearSettings(); refreshEnvChip(); mount(el); toast("Reset to config.js"); });
}
