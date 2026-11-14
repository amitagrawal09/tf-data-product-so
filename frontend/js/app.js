// Hash router + shell behaviour.
import { loadSettings } from "./api.js";
import { setHTML, html, errorBox } from "./ui.js";
import * as dashboard from "./views/dashboard.js";
import * as newOrder from "./views/newOrder.js";
import * as orders from "./views/orders.js";
import * as orderDetail from "./views/orderDetail.js";
import * as exceptions from "./views/exceptions.js";
import * as webhooks from "./views/webhooks.js";
import * as explorer from "./views/explorer.js";
import * as marketplace from "./views/marketplace.js";
import * as settings from "./views/settings.js";
import { initScope } from "./scope.js";

const routes = [
  { re: /^#\/dashboard$/, nav: "dashboard", view: dashboard, title: "Dashboard" },
  { re: /^#\/new(?:\/(\w+))?$/, nav: "new", view: newOrder, title: "New order" },
  { re: /^#\/orders\/([^/?]+)(?:\/(\w+))?$/, nav: "orders", view: orderDetail, title: "Order" },
  { re: /^#\/orders(?:\?(.*))?$/, nav: "orders", view: orders, title: "Orders" },
  { re: /^#\/exceptions$/, nav: "exceptions", view: exceptions, title: "Exceptions" },
  { re: /^#\/webhooks$/, nav: "webhooks", view: webhooks, title: "Webhooks" },
  { re: /^#\/marketplace\/([^/?]+)(?:\/(\w+))?$/, nav: "marketplace", view: marketplace, title: "Dataset" },
  { re: /^#\/marketplace$/, nav: "marketplace", view: marketplace, title: "Data marketplace" },
  { re: /^#\/explorer$/, nav: "explorer", view: explorer, title: "API explorer" },
  { re: /^#\/settings$/, nav: "settings", view: settings, title: "Settings" },
];

let current = null;
const main = document.getElementById("main");

async function route() {
  const hash = location.hash || "#/dashboard";
  const r = routes.find(x => x.re.test(hash));
  if (!r) { location.hash = "#/dashboard"; return; }
  const params = hash.match(r.re).slice(1).map(p => (p ? decodeURIComponent(p) : p));
  if (current && current.unmount) { try { current.unmount(); } catch { /* ignore */ } }
  current = r.view;
  document.querySelectorAll(".sidenav a").forEach(a => {
    const on = a.dataset.route === r.nav;
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.title = `${r.title} · Order Operations Console`;
  closeNav();
  main.innerHTML = "";
  try {
    await r.view.mount(main, ...params);
  } catch (err) {
    console.error(err);
    setHTML(main, html`<h1>${r.title}</h1>${errorBox(err)}`);
  }
  main.focus({ preventScroll: true });
}

function closeNav() {
  document.getElementById("sidenav").classList.remove("open");
  document.getElementById("navToggle").setAttribute("aria-expanded", "false");
}

function initTheme() {
  let t = null;
  try { t = localStorage.getItem("tfso.theme"); } catch { /* ignore */ }
  if (t) document.documentElement.dataset.theme = t;
  document.getElementById("themeToggle").addEventListener("click", () => {
    const cur = document.documentElement.dataset.theme
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("tfso.theme", next); } catch { /* ignore */ }
  });
}

export function refreshEnvChip() {
  const s = loadSettings();
  const chip = document.getElementById("envChip");
  chip.textContent = s.apiBase || "(same origin)";
  chip.title = `API base: ${s.apiBase}`;
}

document.getElementById("navToggle").addEventListener("click", () => {
  const nav = document.getElementById("sidenav");
  const open = !nav.classList.contains("open");
  nav.classList.toggle("open", open);
  document.getElementById("navToggle").setAttribute("aria-expanded", String(open));
});
window.addEventListener("hashchange", route);
// Changing the operating scope re-renders the current page with the new national/regional/site filter.
let scopeTimer = null;
window.addEventListener("tfso:scope", () => { clearTimeout(scopeTimer); scopeTimer = setTimeout(route, 30); });
initTheme();
refreshEnvChip();
initScope().finally(route);
