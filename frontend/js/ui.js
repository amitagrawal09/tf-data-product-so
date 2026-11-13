// Small DOM + formatting helpers shared by the views.

export function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Tagged template that escapes interpolations unless wrapped with raw(). */
export function html(strings, ...vals) {
  let out = "";
  strings.forEach((s, i) => {
    out += s;
    if (i < vals.length) {
      const v = vals[i];
      if (v && v.__raw) out += v.value;
      else if (Array.isArray(v)) out += v.map(x => (x && x.__raw ? x.value : esc(x))).join("");
      else out += esc(v);
    }
  });
  return { __raw: true, value: out, toString() { return out; } };
}
export const raw = value => ({ __raw: true, value: String(value ?? "") });

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

export function setHTML(el, content) { el.innerHTML = content && content.__raw ? content.value : String(content ?? ""); }

export function toast(message, kind = "") {
  const host = document.getElementById("toasts");
  if (!host) return;
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.setAttribute("role", kind === "error" ? "alert" : "status");
  t.textContent = message;
  host.appendChild(t);
  setTimeout(() => t.remove(), kind === "error" ? 7000 : 4000);
}

export function errorBox(err) {
  if (!err) return "";
  const lines = err.details || [];
  return html`<div class="alert error" role="alert"><strong>${err.status ? `HTTP ${err.status} – ` : ""}${err.message}</strong>
    ${lines.length ? html`<ul>${lines.map(l => html`<li>${l}</li>`)}</ul>` : ""}
    ${err.correlationId ? html`<div class="small mono">correlationId: ${err.correlationId}</div>` : ""}</div>`;
}

export function fmtDate(v) {
  if (!v) return "–";
  const d = new Date(v);
  if (isNaN(d)) return String(v);
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(v))) return d.toLocaleDateString(undefined, { timeZone: "UTC" });
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
export function fmtMoney(v, cur = "USD") {
  if (v === null || v === undefined || v === "") return "–";
  const n = typeof v === "object" ? Number(v.amount ?? v.value) : Number(v);
  const c = (typeof v === "object" && v.currency) || cur || "USD";
  if (isNaN(n)) return String(v);
  try { return n.toLocaleString(undefined, { style: "currency", currency: c }); } catch { return n.toFixed(2); }
}
export function fmtNum(v, digits = 0) {
  if (v === null || v === undefined || v === "") return "–";
  const n = Number(v);
  if (isNaN(n)) return String(v);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}
export function fmtDuration(sec) {
  if (sec === null || sec === undefined || isNaN(Number(sec))) return "";
  sec = Number(sec);
  if (sec < 1) return `${Math.round(sec * 1000)} ms`;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 1 : 0)} s`;
  if (sec < 3600) return `${Math.floor(sec / 60)} m ${Math.round(sec % 60)} s`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} m`;
  return `${Math.floor(sec / 86400)} d ${Math.round((sec % 86400) / 3600)} h`;
}
export function today(offsetDays = 0) {
  const d = new Date();
  d.setDate(d.getDate() + offsetDays);
  return d.toISOString().slice(0, 10);
}
export function humanize(s) {
  return String(s ?? "").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase()).replace(/\B\w+/g, w => w.toLowerCase());
}

/** Status → category fallback when the API omits statusCategory (mirrors blueprint §5). */
const CATEGORY = {
  RECEIVED: "RECEIVED", INGESTED: "RECEIVED", VALIDATED: "RECEIVED", ENRICHED: "RECEIVED", PUBLISHED: "RECEIVED", SUBMITTED_TO_ERP: "RECEIVED",
  EXCEPTION: "ACTION_REQUIRED", ERP_REJECTED: "ACTION_REQUIRED", CREDIT_HOLD: "ACTION_REQUIRED", ON_HOLD: "ACTION_REQUIRED",
  ACCEPTED: "CONFIRMED", RELEASED: "IN_FULFILLMENT", PICKED: "IN_FULFILLMENT", PACKED: "IN_FULFILLMENT",
  PARTIALLY_SHIPPED: "SHIPPED", SHIPPED: "SHIPPED", DELIVERED: "DELIVERED", INVOICED: "INVOICED", CLOSED: "CLOSED",
  CANCELLED: "CANCELLED", REJECTED: "CANCELLED",
};
export const STATUSES = Object.keys(CATEGORY);
export const CATEGORIES = ["RECEIVED", "ACTION_REQUIRED", "CONFIRMED", "IN_FULFILLMENT", "SHIPPED", "DELIVERED", "INVOICED", "CLOSED", "CANCELLED"];
export const CHANNELS = ["EDI", "EMAIL", "PORTAL", "MANUAL", "PARTNER_API"];
export function categoryOf(status, cat) { return cat || CATEGORY[status] || ""; }

export function statusBadge(status, cat) {
  if (!status) return html`<span class="badge">–</span>`;
  return html`<span class="badge cat-${categoryOf(status, cat)}" title="${humanize(categoryOf(status, cat))}">${humanize(status)}</span>`;
}
export function sevBadge(sev) { return html`<span class="badge sev-${sev || "INFO"}">${sev || "INFO"}</span>`; }

export function json(v) { return html`<pre class="json">${JSON.stringify(v, null, 2)}</pre>`; }

/** Pretty print an X12 interchange: one segment per line, segment id highlighted. */
export function x12(content) {
  const text = String(content ?? "");
  if (!/^\s*(ISA|GS|ST)[^a-zA-Z0-9]/.test(text)) return html`<pre class="json">${text}</pre>`;
  const isa = text.trimStart();
  const elemSep = isa[3] || "*";
  const segTerm = isa.startsWith("ISA") && isa.length > 105 ? isa[105] : "~";
  const segs = isa.split(segTerm).map(s => s.replace(/^[\r\n]+/, "")).filter(s => s.trim());
  const body = segs.map(s => {
    const i = s.indexOf(elemSep);
    const id = i > 0 ? s.slice(0, i) : s;
    return `<span class="seg-id">${esc(id)}</span>${esc(i > 0 ? s.slice(i) : "")}${esc(segTerm)}`;
  }).join("\n");
  return raw(`<pre class="x12">${body}</pre>`);
}

export function kv(obj, keys) {
  const rows = (keys || Object.keys(obj || {})).map(k => {
    const [key, label, fmt] = Array.isArray(k) ? k : [k, humanize(k)];
    let v = key.split(".").reduce((o, p) => (o == null ? o : o[p]), obj);
    if (v === undefined || v === null || v === "") return "";
    if (fmt) v = fmt(v);
    else if (typeof v === "object") v = JSON.stringify(v);
    return html`<dt>${label}</dt><dd>${v}</dd>`;
  });
  return html`<dl class="kv">${rows}</dl>`;
}

export function loading(text = "Loading…") { return html`<div class="muted"><span class="spinner"></span> ${text}</div>`; }

/** Read a nested property defensively, trying several candidate paths. */
export function pick(obj, ...paths) {
  for (const p of paths) {
    const v = p.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);
    if (v !== undefined && v !== null && v !== "") return v;
  }
  return undefined;
}

export function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
