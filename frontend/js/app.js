// Hash router. Each navigation renders into a fresh container so view event listeners never leak,
// and views can register cleanup (e.g. stop polling) with ctx.onLeave.
import { html, mount, loading, errorBox } from "./ui.js";
import { landingView, startView, pairView } from "./views/home.js";
import { connectView, scanView } from "./views/connect.js";
import { findingsView, questionsView, diagnosisView } from "./views/diagnose.js";
import { testsView } from "./views/tests.js";
import { initThemeToggle } from "./theme-toggle.js";
import { repairView, valueView, decisionView } from "./views/money.js";
import { reportView, historyView } from "./views/report.js";
import { privacyView, supportView, settingsView } from "./views/info.js";

const SID = "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})";
const routes = [
  [/^\/$/, landingView],
  [/^\/start$/, startView],
  [/^\/pair$/, pairView],
  [/^\/history$/, historyView],
  [/^\/privacy$/, privacyView],
  [/^\/support$/, supportView],
  [/^\/settings$/, settingsView],
  [new RegExp(`^/s/${SID}/connect$`), connectView],
  [new RegExp(`^/s/${SID}/scan$`), scanView],
  [new RegExp(`^/s/${SID}/findings$`), findingsView],
  [new RegExp(`^/s/${SID}/tests$`), testsView],
  [new RegExp(`^/s/${SID}/questions$`), questionsView],
  [new RegExp(`^/s/${SID}/diagnosis$`), diagnosisView],
  [new RegExp(`^/s/${SID}/repair$`), repairView],
  [new RegExp(`^/s/${SID}/value$`), valueView],
  [new RegExp(`^/s/${SID}/decision$`), decisionView],
  [new RegExp(`^/s/${SID}/report$`), reportView],
];

let leaveHandlers = [];

export function navigate(path) {
  if (location.hash === `#${path}`) render();
  else location.hash = path;
}

function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = h.split("?");
  return { path: path || "/", query: new URLSearchParams(qs || "") };
}

async function render() {
  leaveHandlers.forEach((fn) => { try { fn(); } catch { /* ignore */ } });
  leaveHandlers = [];
  const main = document.getElementById("app");
  const container = document.createElement("div");
  main.replaceChildren(container);
  const { path, query } = parseHash();

  document.body.classList.toggle("is-home", path === "/");
  document.querySelectorAll(".nav a").forEach((a) => {
    a.toggleAttribute("aria-current", a.getAttribute("href") === `#${path}`);
    if (a.hasAttribute("aria-current")) a.setAttribute("aria-current", "page");
  });

  const match = routes.map(([re, view]) => [path.match(re), view]).find(([m]) => m);
  if (!match) {
    mount(container, html`<div class="empty"><h2>Page not found</h2><p><a href="#/">Go to the start page</a></p></div>`);
    return;
  }
  const [m, view] = match;
  const ctx = {
    el: container,
    params: m.slice(1),
    query,
    onLeave: (fn) => leaveHandlers.push(fn),
    rerender: render,
    navigate,
  };
  mount(container, loading());
  try {
    await view(ctx);
  } catch (err) {
    console.error(err);
    if (container.isConnected) mount(container, errorBox(err));
  }
  if (container.isConnected) {
    const heading = container.querySelector("h1, h2");
    if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus({ preventScroll: true }); }
    window.scrollTo({ top: 0 });
  }
}

window.addEventListener("hashchange", render);
window.addEventListener("DOMContentLoaded", () => { initThemeToggle(); render(); });
