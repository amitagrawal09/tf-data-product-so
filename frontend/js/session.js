// Signed-in principal (GET /exp/auth/v1/me, contract §2), the domains the user can see, and the global
// filter card values (range / user). Used for navigation visibility, route guards and page subtitles.
import { api, items } from "./api.js";

let principal = null;
let domainList = null;

export async function loadMe() {
  principal = await api.exp("/auth/v1/me", { source: "session" });
  principal.roles = principal.roles || [];
  principal.domains = principal.domains || [];
  principal.scope = Object.assign({ customerIds: [], vendorIds: [], regions: [] }, principal.scope || {});
  return principal;
}

/** The principal of the signed-in user (null before loadMe()). */
export function me() { return principal; }

export const isExternal = (p = principal) => Boolean(p && p.userType === "external");
export const isCustomer = (p = principal) => Boolean(p && (p.roles.includes("External.Customer") || (isExternal(p) && p.scope.customerIds.length)));
export const isVendor = (p = principal) => Boolean(p && (p.roles.includes("External.Vendor") || (isExternal(p) && p.scope.vendorIds.length)));
export const isRowScoped = (p = principal) => Boolean(p && (p.scope.customerIds.length || p.scope.vendorIds.length || p.scope.regions.length));

/** Marketplace domains with the user's accessible dataset counts (cached for the session). */
export async function domains(force = false) {
  if (domainList && !force) return domainList;
  try { domainList = items(await api.exp("/marketplace/v1/domains", { source: "session" })); }
  catch { domainList = []; }
  return domainList;
}
export function domainName(id) {
  const d = (domainList || []).find(x => x.domainId === id);
  return d ? d.name : id;
}

/* ---------- filter card (environment / range / user) ---------------------------------- */
const FILTER_KEY = "tfso.filters";
export const RANGES = { "24h": ["Last 24 h", 1], "7d": ["Last 7 d", 7], "30d": ["Last 30 d", 30], "90d": ["Last 90 d", 90] };

export function filters() {
  let v = {};
  try { v = JSON.parse(localStorage.getItem(FILTER_KEY) || "{}") || {}; } catch { v = window.__tfsoFilters || {}; }
  return { range: RANGES[v.range] ? v.range : "30d", user: v.user || "" };
}
export function setFilters(next) {
  const v = { ...filters(), ...next };
  window.__tfsoFilters = v;
  try { localStorage.setItem(FILTER_KEY, JSON.stringify(v)); } catch { /* storage blocked */ }
  window.dispatchEvent(new CustomEvent("tfso:filters", { detail: v }));
  return v;
}
export const rangeLabel = (r = filters().range) => RANGES[r][0];
/** First day of the selected range as YYYY-MM-DD (for APIs that accept fromDate). */
export function rangeFromDate(r = filters().range) {
  const d = new Date(Date.now() - RANGES[r][1] * 86400_000);
  return d.toISOString().slice(0, 10);
}
/** Case-insensitive "user contains" filter for tables with a person column. */
export function matchesUser(...values) {
  const u = filters().user.trim().toLowerCase();
  if (!u) return true;
  return values.some(v => String(v || "").toLowerCase().includes(u));
}
export function environmentName() {
  return ((window.TFSO_CONFIG && window.TFSO_CONFIG.environment) || "LOCAL").toUpperCase();
}
