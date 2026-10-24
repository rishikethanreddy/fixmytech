// Header control that cycles the colour theme: system -> light -> dark -> system.
import { theme } from "./store.js";

const ORDER = ["system", "light", "dark"];
const LABEL = { system: "Theme: match my device", light: "Theme: light", dark: "Theme: dark" };

function apply(value) {
  const root = document.documentElement;
  if (value === "light" || value === "dark") root.setAttribute("data-theme", value);
  else root.removeAttribute("data-theme");
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.dataset.mode = value;
    btn.setAttribute("aria-label", `${LABEL[value]}. Click to change.`);
    btn.title = LABEL[value];
  }
}

export function initThemeToggle() {
  apply(theme.get());
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    const next = ORDER[(ORDER.indexOf(theme.get()) + 1) % ORDER.length];
    theme.set(next);
    apply(next);
  });
}
