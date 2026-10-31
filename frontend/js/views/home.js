import { api } from "../api.js";
import { agentPairing, history } from "../store.js";
import { html, mount, callout, errorBox, formData, numOrNull, toast, $ } from "../ui.js";
import { isLoopback } from "../agent.js";

const ICON = {
  check: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`,
  usb: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M10.5 18.5h3"/></svg>`,
  test: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3"/><path d="M7.5 15h9"/></svg>`,
  chat: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>`,
  coin: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M14.5 9.5c-.5-1-1.5-1.5-2.5-1.5-1.7 0-2.8.9-2.8 2s1 1.7 2.8 2 2.8.9 2.8 2-1.1 2-2.8 2c-1.1 0-2.1-.5-2.6-1.5M12 6.5V8M12 16v1.5"/></svg>`,
  shield: html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></svg>`,
};

export async function landingView(ctx) {
  let notAssessable = [];
  try { notAssessable = (await api.checks()).not_assessable; } catch { /* the page still works */ }
  mount(ctx.el, html`
    <div class="hero-band"><section class="hero">
      <div>
        <div class="eyebrow">Android diagnosis &middot; repair costs &middot; resale value</div>
        <h1>Find out what's wrong with your phone, and whether it's worth fixing.</h1>
        <p class="lead">FixMyTech reads what your Android phone reports about itself, runs a few quick hands-on tests and
          only asks what the tests couldn't answer. Then it compares repairing, selling and replacing in plain numbers.</p>
        <div class="row">
          <a class="btn btn-primary btn-lg" href="#/start?mode=usb">Diagnose with a USB cable</a>
          <a class="btn btn-lg" href="#/start?mode=manual">Diagnose without connecting</a>
        </div>
        <div class="trust-row">
          <span>${ICON.check} Free and read-only</span><span>${ICON.check} Nothing installed on the phone</span>
          <span>${ICON.check} No personal data read</span>
        </div>
      </div>
      <div class="hero-visual stack-sm" aria-label="What a report contains">
        <div class="row-between"><strong>Example report</strong><span class="badge b-neutral">Preview</span></div>
        <div class="panel stack-sm">
          <div class="row-between"><span>Battery capacity 78%</span><span class="badge badge-dot b-observed">Measured on device</span></div>
          <div class="row-between"><span>Speaker test</span><span class="badge badge-dot b-ok">Passed</span></div>
          <div class="row-between"><span>"Drops from 30% to 5%"</span><span class="badge badge-dot b-reported">Your answer</span></div>
          <div class="row-between"><span>Battery wear</span><span class="badge b-ok">Strong support</span></div>
          <div class="row-between"><span>Battery replacement</span><span class="badge badge-dot b-estimate">Official price</span></div>
        </div>
        <p class="small muted">Every item says where it came from, so measurements, test results, answers and estimates are never mixed up.</p>
      </div>
    </section></div>

    <section class="stack">
      <div class="section-title"><h2>How it works</h2><p>About 10 minutes from start to a clear recommendation.</p></div>
      <div class="steps">
        <div class="step"><h4>Connect (optional)</h4><p class="small muted">A read-only USB scan reads battery health, storage, crash history, temperatures and more.</p></div>
        <div class="step"><h4>Run quick tests</h4><p class="small muted">Screen, touch, speaker, microphone, cameras and the charging port, each in under a minute.</p></div>
        <div class="step"><h4>Answer what's left</h4><p class="small muted">Only questions the scan and tests couldn't answer. Sometimes none at all.</p></div>
        <div class="step"><h4>Decide with numbers</h4><p class="small muted">Likely causes with evidence, official repair prices, resale value and a clear repair-or-replace comparison.</p></div>
      </div>
    </section>
    <br>
    <section class="grid grid-3" aria-label="Capabilities and limitations">
      <div class="card stack-sm"><div class="feature-icon">${ICON.usb}</div>
        <h3>What the scan checks</h3>
        <ul class="bullets small">
          <li>Battery status, temperature and (where exposed) capacity</li>
          <li>Whether a charger is actually charging</li>
          <li>Free storage and memory</li>
          <li>App and system crash history</li>
          <li>Thermal throttling and restart reasons</li>
        </ul>
      </div>
      <div class="card stack-sm"><div class="feature-icon">${ICON.test}</div>
        <h3>What tests and questions cover</h3>
        <ul class="bullets small">${notAssessable.slice(0, 6).map((x) => html`<li>${x}</li>`)}</ul>
        <p class="small muted">When something can only be confirmed in person, the report says so.</p>
      </div>
      <div class="card stack-sm"><div class="feature-icon">${ICON.shield}</div>
        <h3>Your privacy</h3>
        <p class="small">FixMyTech never unlocks, roots or modifies your phone, and never reads messages, photos or contacts.
          It never claims a part is defective unless a test actually shows it.</p>
        <p class="small"><a href="#/privacy">Privacy details</a></p>
      </div>
    </section>
  `);
}

export async function startView(ctx) {
  const mode = ctx.query.get("mode") === "manual" ? "manual" : "usb";
  const cfg = await api.config();
  const regionOptions = cfg.regions.length ? cfg.regions : [{ region: cfg.default_region, currency: "" }];
  mount(ctx.el, html`
    <div class="page-head">
      <div class="eyebrow">${mode === "usb" ? "USB diagnosis" : "Diagnosis without a cable"}</div>
      <h1>Start a diagnosis</h1>
      <p>${mode === "usb"
        ? "We'll connect to your Android phone through a local helper app on this computer. Device details below are optional: the scan reads the model automatically."
        : "We'll work from your answers. Tell us about the phone so repair estimates and resale values can be looked up."}</p>
    </div>
    <form class="card stack" id="start-form" novalidate>
      <fieldset class="stack-sm">
        <legend>How do you want to diagnose?</legend>
        <div class="options two-col">
          <label class="check"><input type="radio" name="mode" value="usb" ${mode === "usb" ? "checked" : ""}>
            <span><strong>USB scan + questions</strong><br><span class="small muted">Android phone, Windows computer, USB cable.</span></span></label>
          <label class="check"><input type="radio" name="mode" value="manual" ${mode === "manual" ? "checked" : ""}>
            <span><strong>Questions only</strong><br><span class="small muted">Any phone. Less evidence, so results are less certain.</span></span></label>
        </div>
      </fieldset>
      <fieldset class="stack-sm">
        <legend id="about-legend">${mode === "usb" ? "Your market" : "About the phone"}</legend>
        <p class="small muted" id="usb-note" ${mode === "usb" ? "" : "hidden"}>The USB scan reads the phone's manufacturer, model and storage automatically. Only the market is needed now.</p>
        <div class="form-grid">
          <div class="field" data-manual-only ${mode === "usb" ? "hidden" : ""}><label for="manufacturer">Manufacturer</label>
            <input type="text" id="manufacturer" name="manufacturer" maxlength="80" autocomplete="off" placeholder="e.g. Samsung"></div>
          <div class="field" data-manual-only ${mode === "usb" ? "hidden" : ""}><label for="model">Model</label>
            <input type="text" id="model" name="model" maxlength="80" autocomplete="off" placeholder="e.g. Galaxy S21"></div>
          <div class="field" data-manual-only ${mode === "usb" ? "hidden" : ""}><label for="storage_gb">Storage (GB)</label>
            <input type="number" id="storage_gb" name="storage_gb" min="1" max="4096" inputmode="numeric" placeholder="e.g. 128"></div>
          <div class="field" data-manual-only ${mode === "usb" ? "hidden" : ""}><label for="age_months">Age (months)</label>
            <input type="number" id="age_months" name="age_months" min="0" max="240" inputmode="numeric" placeholder="e.g. 30">
            <span class="hint">Roughly how long since it was bought new.</span></div>
          <div class="field"><label for="region">Market</label>
            <select id="region" name="region">${regionOptions.map((r) => html`<option value="${r.region}" ${r.region === cfg.default_region ? "selected" : ""}>${{ IN: "India", US: "United States" }[r.region] || r.region}${r.currency ? ` (${r.currency})` : ""}${r.has_price_data === false ? " - no repair price data yet" : ""}</option>`)}</select>
            <span class="hint">Used to look up official repair prices and resale values. No currency conversion is done.</span></div>
        </div>
      </fieldset>
      <fieldset class="stack-sm">
        <legend>Your consent</legend>
        <label class="check"><input type="checkbox" name="consent_processing" required>
          <span><strong>Process my diagnostic data to produce this report</strong> (required).<br>
          <span class="small muted">Device readings and your answers are sent to the FixMyTech server. No messages, photos, contacts, account
          data or serial numbers are collected. <a href="#/privacy">Details</a></span></span></label>
        <label class="check"><input type="checkbox" name="store_report">
          <span><strong>Keep my report for ${cfg.retention_days} days</strong> so I can come back to it.<br>
          <span class="small muted">Otherwise it is deleted after ${cfg.unsaved_retention_hours} hours. You can delete it at any time.</span></span></label>
      </fieldset>
      <div id="start-error"></div>
      <div class="row"><button class="btn btn-primary btn-lg" type="submit">Continue</button>
        <a class="btn btn-ghost" href="#/">Cancel</a></div>
    </form>
  `);

  // Phone details are only typed in for "questions only"; a USB scan reads them from the phone.
  $("#start-form", ctx.el).addEventListener("change", (ev) => {
    if (ev.target.name !== "mode") return;
    const usbMode = ev.target.value === "usb";
    ctx.el.querySelectorAll("[data-manual-only]").forEach((el) => { el.hidden = usbMode; });
    $("#usb-note", ctx.el).hidden = !usbMode;
    $("#about-legend", ctx.el).textContent = usbMode ? "Your market" : "About the phone";
  });

  $("#start-form", ctx.el).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = formData(ev.target);
    const errEl = $("#start-error", ctx.el);
    if (!f.consent_processing) {
      mount(errEl, callout("warn", "Consent is required", "FixMyTech cannot produce a report without processing your diagnostic data."));
      return;
    }
    const typed = f.mode === "manual";
    const device = {
      manufacturer: typed ? f.manufacturer?.trim() || null : null,
      model: typed ? f.model?.trim() || null : null,
      storage_gb: typed ? numOrNull(f.storage_gb) : null,
      age_months: typed ? numOrNull(f.age_months) : null,
      region: f.region,
    };
    const btn = ev.submitter;
    btn.disabled = true;
    try {
      const res = await api.createSession({ mode: f.mode, device,
        consent: { diagnostic_processing: true, store_report: Boolean(f.store_report) } });
      const label = [device.manufacturer, device.model].filter(Boolean).join(" ") || (f.mode === "usb" ? "USB diagnosis" : "Manual diagnosis");
      history.add({ id: res.session_id, token: res.access_token, mode: f.mode, label,
        created: new Date().toISOString(), expires: res.expires_at });
      ctx.navigate(f.mode === "usb" ? `/s/${res.session_id}/connect` : `/s/${res.session_id}/tests`);
    } catch (err) {
      mount(errEl, errorBox(err));
      btn.disabled = false;
    }
  });
}

// Landing point of the agent's pairing link (opened each time the agent starts): stores the token for this browser,
// then always goes to the start page. Earlier reports stay reachable from "My reports".
export async function pairView(ctx) {
  const url = ctx.query.get("agent") || "http://127.0.0.1:8765";
  const token = ctx.query.get("token");
  window.history.replaceState(null, "", `${location.pathname}#/pair`);
  if (!token || !isLoopback(url)) {
    mount(ctx.el, html`<div class="card">${callout("danger", "Invalid pairing link", "Open the link printed by the FixMyTech agent on this computer.")}</div>`);
    return;
  }
  agentPairing.set(url, token);
  toast("This browser is paired with the FixMyTech agent.");
  window.history.replaceState(null, "", `${location.pathname}#/`);
  ctx.rerender();
}
