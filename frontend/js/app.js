// Shell: sign-in → principal → navigation (role-aware) → hash router with route guards.
import { loadSettings, api, items } from "./api.js";
import { setHTML, html, raw, errorBox, deniedBox, loading } from "./ui.js";
import { initAuth, signOut, initials, authMode } from "./auth.js";
import { loadMe, me, domains, isExternal, isCustomer, filters, setFilters, environmentName, rangeLabel } from "./session.js";
import * as dashboard from "./views/dashboard.js";
import * as newOrder from "./views/newOrder.js";
import * as orders from "./views/orders.js";
import * as orderDetail from "./views/orderDetail.js";
import * as exceptions from "./views/exceptions.js";
import * as webhooks from "./views/webhooks.js";
import * as explorer from "./views/explorer.js";
import * as marketplace from "./views/marketplace.js";
import * as settings from "./views/settings.js";
import * as myAccess from "./views/myAccess.js";
import * as adminAccess from "./views/adminAccess.js";
import * as myOrders from "./views/myOrders.js";
import { initScope, scopeLabel } from "./scope.js";

/* ---------- guards (contract §3) ---------- */
const G = {
  any: () => true,
  readOrders: p => p.canReadOrders,
  operate: p => p.canOperateOrders,
  internal: p => p.userType !== "external",
  admin: p => p.isAdmin,
  adminOrSteward: p => p.isAdmin || p.isSteward,
  myOrders: p => isCustomer(p) || p.canReadOrders,
};
const catalog = { mount: (el, qs) => marketplace.mountCatalog(el, qs), unmount: marketplace.unmount };

const routes = [
  { re: /^#\/dashboard$/, nav: "dashboard", view: dashboard, title: "Operations overview", guard: G.readOrders },
  { re: /^#\/new(?:\/(\w+))?$/, nav: "new", view: newOrder, title: "New order", guard: G.operate },
  { re: /^#\/orders\/([^/?]+)(?:\/(\w+))?$/, nav: "orders", view: orderDetail, title: "Order", guard: G.readOrders },
  { re: /^#\/orders(?:\?(.*))?$/, nav: "orders", view: orders, title: "Orders", guard: G.readOrders },
  { re: /^#\/exceptions$/, nav: "exceptions", view: exceptions, title: "Exceptions", guard: G.readOrders },
  { re: /^#\/webhooks$/, nav: "webhooks", view: webhooks, title: "Webhooks", guard: G.admin },
  { re: /^#\/marketplace\/([^/?]+)(?:\/(\w+))?$/, nav: "marketplace", view: marketplace, title: "Dataset", guard: G.any },
  { re: /^#\/marketplace(?:\?(.*))?$/, nav: "marketplace", view: catalog, title: "Catalog", guard: G.any },
  { re: /^#\/my-access$/, nav: "my-access", view: myAccess, title: "My access", guard: G.any },
  { re: /^#\/my-orders(?:\/([^/?]+))?(?:\?(.*))?$/, nav: "my-orders", view: myOrders, title: "My orders", guard: G.myOrders },
  { re: /^#\/admin\/access$/, nav: "admin-access", view: adminAccess, title: "Access & roles", guard: G.adminOrSteward },
  { re: /^#\/explorer$/, nav: "explorer", view: explorer, title: "API explorer", guard: G.internal },
  { re: /^#\/settings$/, nav: "settings", view: settings, title: "Settings", guard: G.any },
];

function homeHash(p = me()) {
  if (p && p.canReadOrders) return "#/dashboard";
  if (p && isCustomer(p)) return "#/my-orders";
  return "#/marketplace";
}

let current = null;
const main = document.getElementById("main");

async function route() {
  const hash = location.hash && location.hash !== "#" && location.hash !== "#/" ? location.hash : homeHash();
  const r = routes.find(x => x.re.test(hash));
  if (!r) { location.hash = homeHash(); return; }
  const params = hash.match(r.re).slice(1).map(p => (p ? decodeURIComponent(p) : p));
  if (current && current.unmount) { try { current.unmount(); } catch { /* ignore */ } }
  current = r.view;
  markNav(r.nav, hash);
  document.title = `${r.title} · Enterprise Data Mart`;
  closeNav();
  main.innerHTML = "";
  if (!r.guard(me())) {
    current = null;
    setHTML(main, html`<div class="page-head"><div><h1>${r.title}</h1><div class="sub">${subtitle()}</div></div></div>
      ${deniedBox(`Your roles (${me().roles.join(", ") || "none"}) do not include access to ${r.title}.`)}`);
    return;
  }
  try {
    await r.view.mount(main, ...params);
  } catch (err) {
    console.error(err);
    setHTML(main, html`<h1>${r.title}</h1>${errorBox(err)}`);
  }
  main.focus({ preventScroll: true });
}

/** "LOCAL · Last 30 d · National (all regions)" - shared page subtitle. */
export function subtitle() {
  return `${environmentName()} · ${rangeLabel()} · ${isExternal() ? "Your organisation's rows" : scopeLabel()}`;
}

/* ---------- navigation ---------- */
const I = {
  overview: '<path d="M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"/>',
  orders: '<path d="M6 2h9l5 5v15H6z M14 2v6h6 M9 13h8 M9 17h6"/>',
  exceptions: '<path d="M12 3l10 18H2z M12 10v5 M12 18v.5"/>',
  plus: '<circle cx="12" cy="12" r="9"/><path d="M12 8v8M8 12h8"/>',
  catalog: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  domain: '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
  access: '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 3.6-7 8-7s8 3 8 7"/>',
  box: '<path d="M3 7l9-4 9 4-9 4z M3 7v10l9 4 9-4V7 M12 11v10"/>',
  api: '<path d="M8 6l-6 6 6 6M16 6l6 6-6 6M14 4l-4 16"/>',
  hook: '<path d="M18 16a4 4 0 1 1-4-4 M9 8a4 4 0 1 1 4 4 M6 16a4 4 0 1 0 4-4"/>',
  shield: '<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6z M9 12l2 2 4-4"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};
const icon = k => raw(`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[k]}</svg>`);
const link = (href, nav, label, ic, extra = "") => html`<a href="${href}" data-route="${nav}">${icon(ic)}<span>${label}</span>${extra ? raw(extra) : ""}</a>`;

async function buildNav() {
  const p = me();
  const nav = document.getElementById("sidenav");
  const doms = (await domains()).filter(d => d.accessible !== false && (d.accessibleDatasets ?? 1) > 0);
  const monitor = p.canReadOrders ? html`<div class="nav-section">Monitor</div>
    ${link("#/dashboard", "dashboard", "Overview", "overview")}
    ${link("#/orders", "orders", "Orders", "orders")}
    ${link("#/exceptions", "exceptions", "Exceptions", "exceptions")}
    ${p.canOperateOrders ? link("#/new", "new", "New order", "plus") : ""}` : "";
  const mart = html`<div class="nav-section">Data Mart</div>
    ${link("#/marketplace", "marketplace", "Catalog", "catalog")}
    ${doms.map(d => html`<a class="sub" href="#/marketplace?domain=${encodeURIComponent(d.domainId)}" data-route="domain:${d.domainId}"><span>${d.name}</span>
      ${d.accessibleDatasets != null ? html`<span class="count">${d.accessibleDatasets}</span>` : ""}</a>`)}
    ${isCustomer(p) ? link("#/my-orders", "my-orders", "My orders", "box") : ""}
    ${link("#/my-access", "my-access", "My access", "access")}`;
  const tools = !isExternal(p) ? html`<div class="nav-section">Tools</div>
    ${link("#/explorer", "explorer", "API explorer", "api")}
    ${p.isAdmin ? link("#/webhooks", "webhooks", "Webhooks", "hook") : ""}` : "";
  const admin = html`<div class="nav-section">Administration</div>
    ${p.isAdmin || p.isSteward ? link("#/admin/access", "admin-access", "Access & roles", "shield") : ""}
    ${link("#/settings", "settings", "Settings", "gear")}`;
  setHTML(nav, html`${monitor}${mart}${tools}${admin}
    <div class="nav-foot">
      <div>Sources: Process · System APIs · Databricks</div>
      <div class="row"><span class="auth-pill">auth: ${p.authMode || authMode()}</span><span>v2.0</span></div>
    </div>`);
}

function markNav(navKey, hash) {
  const dom = /[?&]domain=([^&]+)/.exec(hash);
  const key = navKey === "marketplace" && dom ? `domain:${decodeURIComponent(dom[1])}` : navKey;
  document.querySelectorAll(".sidenav a").forEach(a => {
    const on = a.dataset.route === key;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}

function closeNav() {
  document.getElementById("sidenav").classList.remove("open");
  document.getElementById("navScrim").hidden = true;
  document.getElementById("navToggle").setAttribute("aria-expanded", "false");
}

export function refreshEnvChip() {
  const s = loadSettings();
  const chip = document.getElementById("envChip");
  chip.textContent = environmentName();
  chip.title = `Environment ${environmentName()} · API base ${s.apiBase || "(same origin)"}`;
}

/* ---------- header: user chip ---------- */
function initUserMenu() {
  const p = me();
  document.getElementById("userInitials").textContent = initials(p.name || p.email || p.id);
  document.getElementById("userEmail").textContent = p.email || p.name || p.id;
  setHTML(document.getElementById("userHead"), html`<strong>${p.name || p.id}</strong>
    <div class="small muted">${p.email || ""}</div>
    <div class="small muted">${p.userType === "external" ? "External partner" : p.userType === "service" ? "Service principal" : "Taylor Farms employee"} · auth ${p.authMode || authMode()}</div>`);
  const chip = document.getElementById("userChip");
  const drop = document.getElementById("userDrop");
  const set = open => { drop.hidden = !open; chip.setAttribute("aria-expanded", String(open)); };
  chip.addEventListener("click", e => { e.stopPropagation(); set(drop.hidden); });
  document.addEventListener("click", e => { if (!drop.hidden && !e.target.closest("#userMenu")) set(false); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !drop.hidden) { set(false); chip.focus(); } });
  drop.addEventListener("click", e => { if (e.target.closest("a")) set(false); });
  document.getElementById("signOut").addEventListener("click", () => signOut());
}

/* ---------- filter card ---------- */
let datasetIndex = null;
async function searchTarget(q) {
  const term = q.trim();
  if (!term) return null;
  if (/^SO-[\w-]+$/i.test(term)) return me().canReadOrders ? `#/orders/${encodeURIComponent(term.toUpperCase())}` : `#/my-orders/${encodeURIComponent(term.toUpperCase())}`;
  if (!datasetIndex) {
    try { datasetIndex = items(await api.exp("/marketplace/v1/datasets", { source: "search" })); } catch { datasetIndex = []; }
  }
  const t = term.toLowerCase();
  const exact = datasetIndex.find(d => d.id.toLowerCase() === t || String(d.name).toLowerCase() === t);
  if (exact) return `#/marketplace/${encodeURIComponent(exact.id)}`;
  if (datasetIndex.some(d => d.id.toLowerCase().includes(t) || String(d.name).toLowerCase().includes(t))) return `#/marketplace?q=${encodeURIComponent(term)}`;
  if (me().canReadOrders) return `#/orders?q=${encodeURIComponent(term)}`;
  if (isCustomer()) return `#/my-orders?q=${encodeURIComponent(term)}`;
  return `#/marketplace?q=${encodeURIComponent(term)}`;
}

function initFilterCard() {
  const env = environmentName();
  setHTML(document.getElementById("fEnv"), html`<option>${env}</option>`);
  const f = filters();
  const range = document.getElementById("fRange");
  const user = document.getElementById("fUser");
  range.value = f.range;
  user.value = f.user;
  const apply = () => setFilters({ range: range.value, user: user.value.trim() });
  document.getElementById("fApply").addEventListener("click", apply);
  user.addEventListener("keydown", e => { if (e.key === "Enter") apply(); });
  document.getElementById("fSearchForm").addEventListener("submit", async e => {
    e.preventDefault();
    const input = document.getElementById("fSearch");
    const target = await searchTarget(input.value);
    if (target) { input.value = ""; if (location.hash === target) route(); else location.hash = target; }
  });
  // Row-scoped external users have no operating scope to choose.
  if (isExternal()) document.getElementById("scopeBar").hidden = true;
}

/* ---------- start-up ---------- */
document.getElementById("navToggle").addEventListener("click", () => {
  const nav = document.getElementById("sidenav");
  const open = !nav.classList.contains("open");
  nav.classList.toggle("open", open);
  document.getElementById("navScrim").hidden = !open;
  document.getElementById("navToggle").setAttribute("aria-expanded", String(open));
});
document.getElementById("navScrim").addEventListener("click", closeNav);

let rerouteTimer = null;
const reroute = () => { clearTimeout(rerouteTimer); rerouteTimer = setTimeout(route, 30); };

async function start() {
  await initAuth();
  document.getElementById("signin").hidden = true;
  const app = document.getElementById("app");
  app.hidden = false;
  setHTML(main, loading("Signing you in…"));
  refreshEnvChip();
  try {
    await loadMe();
  } catch (err) {
    if (err.status === 401) return; // api.js already sent the user back to sign-in
    document.getElementById("filterCard").hidden = true;
    setHTML(main, html`<h1>Could not load your profile</h1>${errorBox(err)}
      <div class="row section-gap"><button class="btn primary" id="retry">Retry</button><button class="btn" id="out">Sign out</button></div>`);
    main.querySelector("#retry").addEventListener("click", () => location.reload());
    main.querySelector("#out").addEventListener("click", () => signOut());
    return;
  }
  initUserMenu();
  initFilterCard();
  await buildNav();
  window.addEventListener("hashchange", route);
  // Changing the operating scope or the filter card re-renders the current page.
  window.addEventListener("tfso:scope", reroute);
  window.addEventListener("tfso:filters", reroute);
  await initScope().catch(() => {});
  route();
}
start();
