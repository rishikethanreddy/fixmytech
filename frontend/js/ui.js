// Rendering helpers. `html` escapes every interpolated value unless it is wrapped with `raw`
// (or is itself the result of `html`), so device- or API-supplied text can never inject markup.

class Raw {
  constructor(s) { this.s = s; }
  toString() { return this.s; }
}

export const raw = (s) => new Raw(s);

export function esc(v) {
  return String(v)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function render(v) {
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(render).join("");
  if (v === null || v === undefined || v === false) return "";
  return esc(v);
}

export function html(strings, ...vals) {
  let out = "";
  strings.forEach((s, i) => { out += s + (i < vals.length ? render(vals[i]) : ""); });
  return new Raw(out);
}

export function mount(el, content) {
  el.innerHTML = render(content);
}

export function $(sel, root = document) { return root.querySelector(sel); }
export function $$(sel, root = document) { return [...root.querySelectorAll(sel)]; }

export function toast(message, ms = 3500) {
  const host = $(".toast-host");
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = message;
  host.appendChild(t);
  setTimeout(() => t.remove(), ms);
}

export const loading = (text = "Loading...") => html`<div class="loading" role="status"><div class="spinner"></div>${text}</div>`;

export function callout(kind, title, body) {
  const icon = { info: "i", warn: "!", danger: "!", ok: "✓", mock: "M" }[kind] || "i";
  return html`<div class="callout callout-${kind}" role="${kind === "danger" ? "alert" : "note"}">
    <span class="callout-icon" aria-hidden="true">${icon}</span>
    <div>${title ? html`<strong>${title}</strong>` : ""}${body ? html`<div>${body}</div>` : ""}</div>
  </div>`;
}

export function errorBox(err, retryAction) {
  const msg = err?.message || "Something went wrong.";
  const code = err?.code ? html`<div class="small muted">Code: <code>${err.code}</code>${err.requestId ? html` &middot; Ref: <code>${err.requestId}</code>` : ""}</div>` : "";
  return html`<div class="card stack-sm">
    ${callout("danger", "We couldn't complete that", html`${msg}${code}`)}
    ${retryAction ? html`<div><button class="btn" data-action="${retryAction}">Try again</button></div>` : ""}
  </div>`;
}

// Evidence provenance badges: the UI must always distinguish these.
const SOURCE_BADGE = {
  device_measured: ["b-observed", "Measured on device"],
  device_reported: ["b-observed", "Reported by device"],
  derived: ["b-inferred", "Derived"],
  customer_reported: ["b-reported", "Your answer"],
  customer_provided: ["b-reported", "Your details"],
  measured: ["b-observed", "Measured"],
  system_reported: ["b-observed", "System-reported"],
  inferred: ["b-inferred", "Inferred"],
  unavailable: ["b-unavailable", "Unavailable"],
  user_observed: ["b-reported", "Your test observation"],
  phone_measured: ["b-observed", "Measured on phone"],
  agent_measured: ["b-observed", "Measured by agent"],
};

export function sourceBadge(kind) {
  const [cls, label] = SOURCE_BADGE[kind] || ["b-neutral", kind];
  return html`<span class="badge badge-dot ${cls}">${label}</span>`;
}

export function valueSourceBadge(source) {
  if (source === "flipvalue_mock") return html`<span class="badge b-mock" title="Not real market data">MOCK FlipValue</span>`;
  if (source === "flipvalue_live") return html`<span class="badge badge-dot b-live">Live FlipValue</span>`;
  if (source === "customer_provided") return html`<span class="badge badge-dot b-reported">Entered by you</span>`;
  if (source === "customer_quote") return html`<span class="badge badge-dot b-reported">Your quote</span>`;
  if (source === "reference_estimate") return html`<span class="badge badge-dot b-estimate">Reference estimate</span>`;
  return "";
}

const LEVELS = { strong: 4, moderate: 3, weak: 2, minimal: 1 };
export function supportMeter(level) {
  const n = LEVELS[level] || 0;
  return html`<span class="row" aria-label="Support level: ${level}">
    <span class="meter l${n}" aria-hidden="true"><span></span><span></span><span></span><span></span></span>
    <span class="small"><strong>${cap(level)}</strong> support</span>
  </span>`;
}

export function cap(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }
export function humanize(key) { return cap(String(key).replaceAll("_", " ").replaceAll(".", " ")); }

export function money(v, currency) {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: n % 1 === 0 ? 0 : 2 }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency || ""}`;
  }
}

export function moneyRange(low, high, currency) {
  if (low === null || low === undefined) return "-";
  if (Number(low) === Number(high)) return money(low, currency);
  return `${money(low, currency)} to ${money(high, currency)}`;
}

export function dateTime(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formData(form) {
  const out = {};
  for (const [k, v] of new FormData(form).entries()) {
    if (k in out) out[k] = [].concat(out[k], v);
    else out[k] = v;
  }
  return out;
}

export function numOrNull(v) {
  if (v === undefined || v === null || String(v).trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
