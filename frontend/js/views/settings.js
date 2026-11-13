import { loadSettings, saveSettings, clearSettings } from "../api.js";
import { html, setHTML, $, toast } from "../ui.js";
import { refreshEnvChip } from "../app.js";

export async function mount(el) {
  const s = loadSettings();
  const cfg = window.TFSO_CONFIG || {};
  setHTML(el, html`
    <div class="page-head"><div><h1>Settings</h1><div class="sub">Overrides saved in this browser take precedence over <span class="mono">config.js</span>.</div></div></div>
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
      <p class="small muted section-gap">config.js defaults: apiBase <span class="mono">${cfg.apiBase || "(unset)"}</span>,
        apiKey ${cfg.apiKey ? "set" : "not set"}, subscriptionKey ${cfg.subscriptionKey ? "set" : "not set"}.</p>
      <p class="small muted">Local: <span class="mono">/api</span> (gateway on :8080). Azure: the APIM gateway URL; deploy.sh writes it into config.js.</p>
    </form>`);
  $("#f", el).addEventListener("submit", e => {
    e.preventDefault();
    const ok = saveSettings({ apiBase: $("#apiBase", el).value.trim(), apiKey: $("#apiKey", el).value.trim(), subscriptionKey: $("#subKey", el).value.trim() });
    refreshEnvChip();
    toast(ok ? "Settings saved" : "Could not save (browser storage blocked)", ok ? "ok" : "error");
  });
  $("#reset", el).addEventListener("click", () => { clearSettings(); refreshEnvChip(); mount(el); toast("Reset to config.js"); });
}
