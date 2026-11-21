// Sign-in for the Enterprise Data Mart console.
//   dev   → sign-in page with a persona picker (GET /exp/auth/v1/personas); persona sent as x-dev-persona.
//   entra → Microsoft Entra ID single sign-on: OAuth 2.0 authorization code flow with PKCE (no library),
//           workforce tenant and, optionally, Entra External ID for customers / vendors. Access token sent as
//           Authorization: Bearer and refreshed 60 s before it expires.
//   apikey→ legacy: no user sign-in (the gateway / config supplies a key).
import { html, raw, setHTML, errorBox } from "./ui.js";

const PERSONA_KEY = "tfso.persona";
const TOKEN_KEY = "tfso.token";       // {access_token, refresh_token, expires_at, authority, clientId, scopes}
const PKCE_KEY = "tfso.pkce";         // {verifier, state, authority, clientId, scopes, returnHash}

const ss = {
  get(k) { try { return JSON.parse(sessionStorage.getItem(k) || "null"); } catch { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch { /* storage blocked */ } },
  del(k) { try { sessionStorage.removeItem(k); } catch { /* storage blocked */ } },
};

/** Effective auth configuration (contract §7) with safe defaults. */
export function authConfig() {
  const cfg = (window.TFSO_CONFIG && window.TFSO_CONFIG.auth) || {};
  const here = location.origin + location.pathname;
  const mode = String(cfg.mode || "dev").toLowerCase();
  const tenant = cfg.tenantId || "";
  let authority = String(cfg.authority || "").replace(/\/+$/, "");
  if (!authority || authority.includes("<tenantId>")) authority = tenant ? `https://login.microsoftonline.com/${tenant}` : "";
  return {
    mode: ["dev", "entra", "apikey"].includes(mode) ? mode : "dev",
    tenantId: tenant, clientId: cfg.clientId || "", authority,
    externalAuthority: String(cfg.externalAuthority || "").replace(/\/+$/, ""),
    externalClientId: cfg.externalClientId || cfg.clientId || "",
    scopes: Array.isArray(cfg.scopes) && cfg.scopes.length ? cfg.scopes : ["openid", "profile", "offline_access"],
    externalScopes: Array.isArray(cfg.externalScopes) && cfg.externalScopes.length ? cfg.externalScopes : null,
    redirectUri: cfg.redirectUri || here,
    postLogoutRedirectUri: cfg.postLogoutRedirectUri || here,
  };
}
export function authMode() { return authConfig().mode; }
export function currentPersona() { return ss.get(PERSONA_KEY); }

/* ---------- request headers ---------------------------------------------------------- */
let refreshing = null;

/** Headers that identify the signed-in user on every API request and download. */
export async function authHeaders() {
  const mode = authMode();
  if (mode === "dev") {
    const p = currentPersona();
    return p ? { "x-dev-persona": p } : {};
  }
  if (mode === "entra") {
    let t = ss.get(TOKEN_KEY);
    if (t && t.expires_at - Date.now() < 60_000 && t.refresh_token) t = await refreshToken().catch(() => null);
    return t && t.access_token ? { Authorization: `Bearer ${t.access_token}` } : {};
  }
  return {};
}

/** Called by api.js on HTTP 401: the session is gone or invalid → back to the sign-in page. */
let bounced = false;
export function onUnauthorized() {
  if (bounced) return;
  bounced = true;
  ss.del(PERSONA_KEY);
  ss.del(TOKEN_KEY);
  try { sessionStorage.setItem("tfso.signin.reason", "Your session has ended or is not valid. Please sign in again."); } catch { /* ignore */ }
  location.reload();
}

/* ---------- PKCE helpers ----------------------------------------------------------------- */
function b64url(bytes) {
  let s = "";
  new Uint8Array(bytes).forEach(b => { s += String.fromCharCode(b); });
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function randomString(n = 48) {
  const a = new Uint8Array(n);
  crypto.getRandomValues(a);
  return b64url(a);
}
async function challenge(verifier) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return b64url(digest);
}

async function startEntraSignIn(external = false) {
  const c = authConfig();
  const authority = external ? c.externalAuthority : c.authority;
  const clientId = external ? c.externalClientId : c.clientId;
  const scopes = (external && c.externalScopes) || c.scopes;
  if (!authority || !clientId) throw new Error("Entra sign-in is not configured (auth.authority / auth.clientId in config.js).");
  const verifier = randomString(48);
  const state = randomString(16);
  ss.set(PKCE_KEY, { verifier, state, authority, clientId, scopes, returnHash: location.hash || "" });
  const q = new URLSearchParams({
    client_id: clientId, response_type: "code", redirect_uri: c.redirectUri, response_mode: "query",
    scope: scopes.join(" "), code_challenge: await challenge(verifier), code_challenge_method: "S256",
    state, prompt: "select_account",
  });
  location.assign(`${authority}/oauth2/v2.0/authorize?${q}`);
}

async function tokenRequest(authority, form) {
  const res = await fetch(`${authority}/oauth2/v2.0/token`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(form),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.access_token) throw new Error(data.error_description || data.error || `Token endpoint returned HTTP ${res.status}`);
  return data;
}

function storeToken(data, ctx) {
  const prev = ss.get(TOKEN_KEY) || {};
  const t = {
    access_token: data.access_token,
    refresh_token: data.refresh_token || prev.refresh_token || null,
    id_token: data.id_token || prev.id_token || null,
    expires_at: Date.now() + (Number(data.expires_in) || 3600) * 1000,
    authority: ctx.authority, clientId: ctx.clientId, scopes: ctx.scopes,
  };
  ss.set(TOKEN_KEY, t);
  scheduleRefresh();
  return t;
}

/** Complete the redirect (?code=&state=) → exchange the code for tokens. */
async function completeEntraRedirect() {
  const p = new URLSearchParams(location.search);
  if (!p.has("code") && !p.has("error")) return false;
  const pkce = ss.get(PKCE_KEY);
  const clean = () => history.replaceState(null, "", location.pathname + ((pkce && pkce.returnHash) || location.hash || ""));
  if (p.has("error")) { clean(); ss.del(PKCE_KEY); throw new Error(p.get("error_description") || p.get("error")); }
  if (!pkce || p.get("state") !== pkce.state) { clean(); ss.del(PKCE_KEY); throw new Error("Sign-in response did not match the request (state mismatch). Please try again."); }
  const c = authConfig();
  const data = await tokenRequest(pkce.authority, {
    client_id: pkce.clientId, grant_type: "authorization_code", code: p.get("code"),
    redirect_uri: c.redirectUri, code_verifier: pkce.verifier, scope: pkce.scopes.join(" "),
  });
  ss.del(PKCE_KEY);
  storeToken(data, pkce);
  clean();
  return true;
}

function refreshToken() {
  if (refreshing) return refreshing;
  const t = ss.get(TOKEN_KEY);
  if (!t || !t.refresh_token) return Promise.reject(new Error("No refresh token"));
  refreshing = tokenRequest(t.authority, {
    client_id: t.clientId, grant_type: "refresh_token", refresh_token: t.refresh_token, scope: (t.scopes || []).join(" "),
  }).then(d => storeToken(d, t)).finally(() => { refreshing = null; });
  return refreshing;
}

let refreshTimer = null;
function scheduleRefresh() {
  clearTimeout(refreshTimer);
  const t = ss.get(TOKEN_KEY);
  if (!t || !t.refresh_token) return;
  const wait = Math.max(5_000, t.expires_at - Date.now() - 60_000);
  refreshTimer = setTimeout(() => { refreshToken().catch(() => onUnauthorized()); }, wait);
}

/* ---------- sign out ------------------------------------------------------------------- */
export function signOut() {
  const mode = authMode();
  const t = ss.get(TOKEN_KEY);
  ss.del(PERSONA_KEY); ss.del(TOKEN_KEY); ss.del(PKCE_KEY);
  try { localStorage.removeItem("tfso.consumer"); } catch { /* ignore */ }
  if (mode === "entra") {
    const c = authConfig();
    const authority = (t && t.authority) || c.authority;
    if (authority) {
      location.assign(`${authority}/oauth2/v2.0/logout?post_logout_redirect_uri=${encodeURIComponent(c.postLogoutRedirectUri)}`);
      return;
    }
  }
  location.hash = "";
  location.reload();
}

/* ---------- sign-in page --------------------------------------------------------------- */
const ROLE_KIND = r => (/^External\./.test(r) ? "external" : /^Domain\./.test(r) ? "domain" : /^DataMart\.(Admin|Steward)$/.test(r) ? "platform" : "");
export const roleTag = r => html`<span class="role-tag ${ROLE_KIND(r)}">${r}</span>`;
export function initials(name) {
  const parts = String(name || "?").replace(/\(.*?\)/g, "").trim().split(/[\s.@_-]+/).filter(Boolean);
  return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
const msLogo = raw(`<span class="ms-logo" aria-hidden="true"><i style="background:#f25022"></i><i style="background:#7fba00"></i><i style="background:#00a4ef"></i><i style="background:#ffb900"></i></span>`);

function shell(body) {
  const host = document.getElementById("signin");
  host.hidden = false;
  document.getElementById("app").hidden = true;
  document.title = "Sign in · Enterprise Data Mart";
  const env = (window.TFSO_CONFIG && window.TFSO_CONFIG.environment) || "LOCAL";
  setHTML(host, html`
    <header class="signin-top">
      <span class="wordmark" aria-hidden="true"><span class="wm-taylor">Taylor</span><span class="wm-farms">FARMS</span></span>
      <span class="brand-text"><strong>Enterprise Data Mart</strong><small>Taylor Farms · Sales, Supply Chain &amp; Finance data</small></span>
      <span class="env-chip" style="margin-left:auto">${env}</span>
    </header>
    <div class="signin-main"><div class="signin-card">
      <div class="signin-hero"><h1>Sign in</h1>
        <div class="muted">Governed data products for Sales Order, Customer, Product, Logistics, Vendor, Procurement and Finance. Employees sign in with their Taylor Farms Microsoft account; customers and vendors with their partner account. What you can see is decided by your Entra ID app roles.</div>
      </div>
      <div class="signin-body" id="signinBody">${body}</div>
    </div></div>
    <div class="signin-foot">Access is audited. Unauthorised use is prohibited.</div>`);
  return host;
}

function reasonNotice() {
  let r = null;
  try { r = sessionStorage.getItem("tfso.signin.reason"); sessionStorage.removeItem("tfso.signin.reason"); } catch { /* ignore */ }
  return r ? html`<div class="alert warn" role="status" style="margin-bottom:14px">${r}</div>` : "";
}

async function devSignIn() {
  const s = (await import("./api.js")).loadSettings();
  const host = shell(html`${reasonNotice()}
    <div class="row" style="gap:12px;align-items:center">
      <button class="ms-btn" disabled title="Single sign-on is used in Azure environments (auth.mode = entra)">${msLogo} Sign in with Microsoft</button>
      <span class="small muted">Entra ID single sign-on is enabled in Azure environments. This environment runs in <span class="mono">dev</span> mode.</span>
    </div>
    <div class="divider">or choose a test persona</div>
    <div class="small muted">Each persona carries the Entra ID app roles and row scope it would get in production. The persona is sent as <span class="mono">x-dev-persona</span>.</div>
    <div class="persona-grid" id="personas"><div class="muted small">Loading personas…</div></div>`);
  let list = [];
  try {
    const headers = { Accept: "application/json" };
    if (s.subscriptionKey) headers["Ocp-Apim-Subscription-Key"] = s.subscriptionKey;
    const res = await fetch(`${s.apiBase}/exp/auth/v1/personas`, { headers });
    if (!res.ok) throw Object.assign(new Error(`Could not load test personas (HTTP ${res.status}). Is the backend running in dev auth mode?`), { status: res.status });
    const data = await res.json();
    list = Array.isArray(data) ? data : data.items || [];
  } catch (e) {
    setHTML(host.querySelector("#personas"), errorBox(e));
    return new Promise(() => {}); // stay on the sign-in page
  }
  const grid = host.querySelector("#personas");
  setHTML(grid, html`${list.map(p => html`
    <button class="persona ${p.userType === "external" ? "ext" : ""}" data-persona="${p.id}" type="button">
      <span class="avatar">${initials(p.name)}</span>
      <span style="min-width:0"><strong>${p.name}</strong>
        <span class="p-title">${p.title || ""}${p.userType === "external" ? " · external" : ""}</span>
        <span class="small muted" style="display:block;overflow-wrap:anywhere">${p.email || p.id}</span>
        <span class="p-roles">${(p.roles || []).map(roleTag)}</span>
        ${scopeText(p.scope) ? html`<span class="p-scope">Row scope: <strong>${scopeText(p.scope)}</strong></span>` : ""}</span>
    </button>`)}`);
  return new Promise(resolve => {
    grid.addEventListener("click", e => {
      const b = e.target.closest("[data-persona]");
      if (!b) return;
      ss.set(PERSONA_KEY, b.dataset.persona);
      resolve(true);
    });
  });
}

export function scopeText(sc) {
  if (!sc) return "";
  const parts = [];
  if (sc.customerIds && sc.customerIds.length) parts.push(`customers ${sc.customerIds.join(", ")}`);
  if (sc.vendorIds && sc.vendorIds.length) parts.push(`vendors ${sc.vendorIds.join(", ")}`);
  if (sc.regions && sc.regions.length) parts.push(`regions ${sc.regions.join(", ")}`);
  return parts.join(" · ");
}

function entraSignIn(err) {
  const c = authConfig();
  const host = shell(html`${reasonNotice()}${err ? html`<div style="margin-bottom:14px">${errorBox(err)}</div>` : ""}
    <div class="row" style="gap:12px">
      <button class="ms-btn" id="msSignIn" type="button">${msLogo} Sign in with Microsoft</button>
      <span class="small muted">Taylor Farms employees and contractors</span>
    </div>
    ${c.externalAuthority ? html`<div class="divider">customers and vendors</div>
      <button class="btn" id="extSignIn" type="button">Sign in with your partner account</button>
      <span class="small muted">Entra External ID</span>` : ""}
    <p class="small muted section-gap">You will be redirected to Microsoft to sign in, then returned here. Tokens are kept in this browser tab only and cleared when you sign out.</p>`);
  const go = external => startEntraSignIn(external).catch(e => entraSignIn(e));
  host.querySelector("#msSignIn").addEventListener("click", () => go(false));
  const ext = host.querySelector("#extSignIn");
  if (ext) ext.addEventListener("click", () => go(true));
  return new Promise(() => {}); // the page navigates away to Microsoft
}

/**
 * Make sure a user is signed in before the console routes. Resolves when signed in; otherwise shows the
 * sign-in page (and, for Entra, redirects away).
 */
export async function initAuth() {
  const mode = authMode();
  if (mode === "apikey") return true;
  if (mode === "dev") {
    if (currentPersona()) return true;
    return devSignIn();
  }
  // entra
  try {
    if (await completeEntraRedirect()) return true;
  } catch (e) {
    return entraSignIn(e);
  }
  const t = ss.get(TOKEN_KEY);
  if (t && t.access_token) {
    if (t.expires_at - Date.now() > 60_000) { scheduleRefresh(); return true; }
    if (t.refresh_token) { try { await refreshToken(); return true; } catch { /* fall through */ } }
  }
  ss.del(TOKEN_KEY);
  return entraSignIn();
}

