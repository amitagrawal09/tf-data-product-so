// Fetch wrapper for the Experience / Process / System APIs (direct, via local gateway, or via Azure APIM).
// Every request carries the signed-in user's identity (x-dev-persona in dev mode, Authorization: Bearer with
// Entra ID). HTTP 401 sends the user back to the sign-in page.
import { authHeaders, onUnauthorized } from "./auth.js";

const STORAGE_KEY = "tfso.settings";

export function loadSettings() {
  const base = Object.assign({ apiBase: "/api", subscriptionKey: "", apiKey: "" }, window.TFSO_CONFIG || {});
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved && typeof saved === "object") Object.assign(base, saved);
  } catch { /* storage unavailable */ }
  base.apiBase = String(base.apiBase || "").replace(/\/+$/, "");
  return base;
}

export function saveSettings(s) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(s)); return true; } catch { return false; }
}

export function clearSettings() {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
}

export function uuid() {
  if (crypto && crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export class ApiError extends Error {
  constructor(status, problem, correlationId) {
    const title = (problem && (problem.detail || problem.title)) || `HTTP ${status}`;
    super(title);
    this.status = status;
    this.problem = problem || {};
    this.correlationId = correlationId || (problem && problem.correlationId);
  }
  /** Flattened list of human-readable error lines from an RFC 7807 problem. */
  get details() {
    const p = this.problem || {};
    const out = [];
    if (p.title && p.detail && p.title !== p.detail) out.push(p.title);
    const errs = Array.isArray(p.errors) ? p.errors : Array.isArray(p.detail) ? p.detail : [];
    for (const e of errs) {
      if (typeof e === "string") out.push(e);
      else if (e) out.push([e.code || e.ruleId || (Array.isArray(e.loc) ? e.loc.join(".") : e.field), e.message || e.msg].filter(Boolean).join(": "));
    }
    return out;
  }
}

/* ---------- API call log ----------------------------------------------------------------
 * Every request made by the console (any page) is recorded with its full input (method, URL,
 * headers, body) and output (status, timing, headers, body). The API explorer renders this log.
 */
const CALL_LOG = [];
const LOG_LIMIT = 300;
const listeners = new Set();
let callSeq = 0;

export function callLog() { return CALL_LOG; }
export function onCall(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function clearCallLog() { CALL_LOG.length = 0; listeners.forEach(fn => fn(null)); }

function maskHeaders(h) {
  const out = {};
  for (const [k, v] of Object.entries(h || {})) {
    out[k] = /api-key|subscription-key|authorization/i.test(k) && v ? String(v).slice(0, 4) + "••••" : v;
  }
  return out;
}

function record(entry) {
  CALL_LOG.unshift(entry);
  if (CALL_LOG.length > LOG_LIMIT) CALL_LOG.pop();
  listeners.forEach(fn => { try { fn(entry); } catch { /* listener errors never break API calls */ } });
}

/** Render a call as a copy-paste cURL command (keys masked). */
export function toCurl(call) {
  const rq = call.request;
  const abs = new URL(rq.url, location.href).href;
  const parts = [`curl -X ${rq.method} '${abs}'`];
  for (const [k, v] of Object.entries(rq.headers || {})) parts.push(`-H '${k}: ${v}'`);
  if (rq.body !== undefined && rq.body !== null && rq.body !== "") {
    const b = typeof rq.body === "string" ? rq.body : JSON.stringify(rq.body);
    parts.push(`--data-raw '${b.replace(/'/g, "'\\''")}'`);
  }
  return parts.join(" \\\n  ");
}

/**
 * request("process", "/v1/orders", { query, body, headers, method, raw, source })
 * Returns parsed JSON (or text). With raw:true returns {status, ok, headers, data, ms, correlationId}.
 */
export async function request(layer, path, opts = {}) {
  const s = loadSettings();
  const method = (opts.method || (opts.body !== undefined ? "POST" : "GET")).toUpperCase();
  let url = layer ? `${s.apiBase}/${layer}${path}` : `${s.apiBase}${path}`;
  if (opts.query) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(opts.query)) if (v !== undefined && v !== null && v !== "") q.set(k, v);
    const qs = q.toString();
    if (qs) url += (url.includes("?") ? "&" : "?") + qs;
  }
  const correlationId = uuid();
  const headers = { "x-correlation-id": correlationId, Accept: "application/json, text/plain, */*" };
  if (s.apiKey) headers["x-api-key"] = s.apiKey;
  if (s.subscriptionKey) headers["Ocp-Apim-Subscription-Key"] = s.subscriptionKey;
  let body;
  if (opts.body !== undefined) {
    if (typeof opts.body === "string") { body = opts.body; headers["Content-Type"] = opts.contentType || "text/plain"; }
    else { body = JSON.stringify(opts.body); headers["Content-Type"] = "application/json"; }
  }
  if (opts.idempotent) headers["Idempotency-Key"] = uuid();
  Object.assign(headers, await authHeaders(), opts.headers || {});

  const call = {
    id: ++callSeq, at: new Date().toISOString(), layer: layer || (path.split("/")[1] || ""),
    source: opts.source || (location.hash || "#/").replace(/^#/, ""),
    request: { method, url, path: layer ? `/${layer}${path}` : path, headers: maskHeaders(headers), body: opts.body },
    response: null,
  };
  const t0 = performance.now();
  let res;
  try {
    res = await fetch(url, { method, headers, body });
  } catch (err) {
    const e = new ApiError(0, { title: "Network error", detail: `Could not reach ${url} (${err.message})` }, correlationId);
    call.response = { status: 0, ok: false, ms: Math.round(performance.now() - t0), headers: {}, body: e.problem };
    record(call);
    if (opts.raw) return { status: 0, ok: false, headers: {}, data: e.problem, ms: call.response.ms, correlationId, url, call };
    throw e;
  }
  const ms = Math.round(performance.now() - t0);
  const ctype = res.headers.get("content-type") || "";
  let data = null;
  if (res.status !== 204) {
    const text = await res.text();
    if (text && (ctype.includes("json") || /^\s*[{[]/.test(text))) {
      try { data = JSON.parse(text); } catch { data = text; }
    } else data = text;
  }
  const respCorr = res.headers.get("x-correlation-id") || correlationId;
  const h = {};
  res.headers.forEach((v, k) => { h[k] = v; });
  call.response = { status: res.status, ok: res.ok, ms, headers: h, body: data, correlationId: respCorr };
  record(call);
  if (res.status === 401 && !opts.noSignInRedirect) onUnauthorized();
  if (opts.raw) {
    return { status: res.status, ok: res.ok, headers: h, data, ms, correlationId: respCorr, url, call };
  }
  if (!res.ok) throw new ApiError(res.status, typeof data === "object" ? data : { title: `HTTP ${res.status}`, detail: String(data || res.statusText) }, respCorr);
  if (opts.withHeaders) return { data, etag: res.headers.get("etag"), headers: res.headers };
  return data;
}

export const api = {
  exp: (path, opts) => request("exp", path, opts),
  process: (path, opts) => request("process", path, opts),
  system: (path, opts) => request("system", path, opts),
};

/** Normalise list responses: accepts {items:[]}, {value:[]}, {data:[]} or a bare array. */
export function items(resp) {
  if (Array.isArray(resp)) return resp;
  if (!resp || typeof resp !== "object") return [];
  return resp.items || resp.value || resp.data || resp.results || [];
}

/**
 * Download a file (export) through the gateway: returns {ok, status, blob, filename, headers, problem}.
 * Recorded in the API call log like every other request (body shown as a size summary).
 */
export async function download(layer, path, opts = {}) {
  const s = loadSettings();
  const url = `${s.apiBase}/${layer}${path}`;
  const headers = { "x-correlation-id": uuid(), Accept: "*/*" };
  if (s.apiKey) headers["x-api-key"] = s.apiKey;
  if (s.subscriptionKey) headers["Ocp-Apim-Subscription-Key"] = s.subscriptionKey;
  Object.assign(headers, await authHeaders(), opts.headers || {});
  const call = { id: ++callSeq, at: new Date().toISOString(), layer, source: opts.source || (location.hash || "#/").replace(/^#/, ""),
                 request: { method: "GET", url, path: `/${layer}${path}`, headers: maskHeaders(headers), body: undefined }, response: null };
  const t0 = performance.now();
  let res;
  try { res = await fetch(url, { headers }); } catch (err) {
    call.response = { status: 0, ok: false, ms: Math.round(performance.now() - t0), headers: {}, body: { title: "Network error", detail: err.message } };
    record(call);
    return { ok: false, status: 0, problem: call.response.body, headers: {} };
  }
  const h = {};
  res.headers.forEach((v, k) => { h[k] = v; });
  const ms = Math.round(performance.now() - t0);
  if (!res.ok) {
    let problem;
    try { problem = await res.json(); } catch { problem = { title: `HTTP ${res.status}` }; }
    call.response = { status: res.status, ok: false, ms, headers: h, body: problem };
    record(call);
    if (res.status === 401) onUnauthorized();
    return { ok: false, status: res.status, problem, headers: h };
  }
  const blob = await res.blob();
  const m = /filename="?([^";]+)"?/i.exec(h["content-disposition"] || "");
  call.response = { status: res.status, ok: true, ms, headers: h, body: `(${h["content-type"] || "file"} · ${blob.size.toLocaleString()} bytes · ${h["x-tfso-row-count"] || "?"} rows)` };
  record(call);
  return { ok: true, status: res.status, blob, filename: m ? m[1] : "export", headers: h };
}

/* ---------- operating scope (national / regional / site, account scope) -------------------
 * Persisted per browser in localStorage `tfso.scope`; views pass scopeQuery() to the APIs.
 */
const SCOPE_KEY = "tfso.scope";
const EMPTY_SCOPE = { region: "", site: "", accountScope: "" };

export function scope() {
  try {
    const v = JSON.parse(localStorage.getItem(SCOPE_KEY) || "null");
    if (v && typeof v === "object") return { ...EMPTY_SCOPE, ...v };
  } catch { /* storage unavailable */ }
  return { ...EMPTY_SCOPE, ...(window.__tfsoScope || {}) };
}

/** Only the non-empty scope keys, ready to merge into an API query. */
export function scopeQuery() {
  const s = scope();
  const q = {};
  for (const k of ["region", "site", "accountScope"]) if (s[k]) q[k] = s[k];
  return q;
}

export function setScope(next) {
  const v = { ...scope(), ...next };
  if (next && "region" in next && !("site" in next)) v.site = "";
  window.__tfsoScope = v; // fallback when storage is blocked
  try { localStorage.setItem(SCOPE_KEY, JSON.stringify(v)); } catch { /* storage unavailable */ }
  window.dispatchEvent(new CustomEvent("tfso:scope", { detail: v }));
  return v;
}

let regionsPromise = null;
/** Regions with their sites (cached for the session); [] if the endpoint is unavailable. */
export function regions(force = false) {
  if (!regionsPromise || force) {
    regionsPromise = request("exp", "/portal/v1/catalog/regions")
      .then(r => items(r))
      .catch(() => { regionsPromise = null; return []; });
  }
  return regionsPromise;
}
