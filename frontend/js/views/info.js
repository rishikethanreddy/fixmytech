import { api } from "../api.js";
import { agentPairing, history, settings } from "../store.js";
import { html, mount, callout, errorBox, $, toast, dateTime } from "../ui.js";
import { isLoopback } from "../agent.js";

export async function privacyView(ctx) {
  const cfg = await api.config();
  mount(ctx.el, html`
    <div class="page-head"><h1>Privacy and data</h1><p>What FixMyTech collects, why, and how long it is kept.</p></div>
    <div class="grid grid-2">
      <div class="card stack-sm"><h3>Collected from the phone (USB scan only)</h3><ul class="bullets small">
        <li>Manufacturer, model, Android version, security patch level</li>
        <li>Battery level, temperature, voltage, OS health flag, charger state and, where exposed, capacity estimate and cycle count</li>
        <li>Storage and memory totals and free space</li>
        <li>Counts of app and system crashes in the last 7 days (counts only, no app names or crash contents)</li>
        <li>Thermal status, uptime and the category of the last restart reason</li>
        <li>Number of cameras, whether a touchscreen is detected, airplane mode / Wi-Fi / SIM state</li></ul></div>
      <div class="card stack-sm"><h3>Never collected</h3><ul class="bullets small">
        <li>Messages, photos, contacts, call logs, location, accounts or passwords</li>
        <li>Serial number, IMEI or phone number (the agent sends only a salted, one-way device reference)</li>
        <li>Wi-Fi network names, installed app lists, raw system logs</li></ul>
        <p class="small muted">Raw command output stays on your computer. The agent sends only normalized readings, after removing
          anything that looks like personal data.</p></div>
      <div class="card stack-sm"><h3>Retention and deletion</h3>
        <p class="small">Reports you chose to keep are deleted after <strong>${cfg.retention_days} days</strong>; others after
          <strong>${cfg.unsaved_retention_hours} hours</strong>. You can delete a report at any time from the report page or
          <a href="#/history">My reports</a>.</p>
        <p class="small">Each report is protected by a private key stored only in your browser. FixMyTech staff cannot link reports to you.</p></div>
      <div class="card stack-sm"><h3>Sharing with FlipValue</h3>
        <p class="small">Only if you request a resale estimate, FixMyTech sends FlipValue the manufacturer, model, storage, age,
          market, a condition grade, likely fault categories and the repair type being valued. Nothing that identifies you or the
          phone is sent.</p></div>
      <div class="card stack-sm"><h3>The local agent</h3>
        <p class="small">The agent runs on your computer and listens only on 127.0.0.1. It requires a pairing token, accepts
          requests only from this website, and can only run a fixed list of read-only commands. It cannot run commands you or a
          website type in, install apps, change settings or unlock the phone.</p></div>
    </div>`);
}

export async function supportView(ctx) {
  const faq = [
    ["The agent is \"not detected\"", html`Start it from the FixMyTech folder with <code>.\\.venv\\Scripts\\python -m fixmytech.agent</code>
      and keep its window open. If you changed its port, update the agent address in <a href="#/settings">Settings</a>.`],
    ["ADB is not installed", html`Install Android SDK Platform-Tools from developer.android.com and either add the folder to your PATH or start the
      agent with <code>--adb-path C:\\platform-tools\\adb.exe</code>.`],
    ["My phone shows as \"unauthorized\"", "Unlock the phone. You should see \"Allow USB debugging?\". Tap Allow. If nothing appears, unplug and reconnect, or in Developer options choose \"Revoke USB debugging authorizations\" and reconnect."],
    ["My phone shows as \"offline\"", "Reconnect the cable, try another USB port, or restart the phone. Close other phone-management software that may be using ADB."],
    ["No device is listed at all", "Use a data cable (many cables only charge), check USB debugging is on, and on Windows install your manufacturer's USB driver if the phone does not appear in Device Manager."],
    ["Some checks say \"blocked\" or \"not supported\"", "Phones restrict what a computer can read. Blocked checks are reported honestly as unavailable and never treated as healthy. The questions cover the gap."],
    ["The phone disconnected during the scan", "Partial results are saved and marked incomplete. Reconnect and run the scan again from the Connect step."],
    ["Resale valuation failed", "The valuation service may be unavailable. Your diagnosis and repair estimates still work, and you can enter resale values yourself in the Decision step."],
    ["I have an iPhone", "USB diagnosis supports Android only. Use \"Diagnose without connecting\"; repair estimates and comparisons still work."],
  ];
  mount(ctx.el, html`
    <div class="page-head"><h1>Help and troubleshooting</h1></div>
    <div class="stack-sm">${faq.map(([q, a]) => html`<details class="card card-tight"><summary>${q}</summary><div class="details-body small">${a}</div></details>`)}</div>`);
}

export async function settingsView(ctx) {
  const [cfg, pricing] = await Promise.all([api.config(), api.pricingStatus().catch(() => null)]);
  const current = settings.get().agentUrl || cfg.agent_url;
  const paired = agentPairing.get();
  mount(ctx.el, html`
    <div class="page-head"><h1>Settings</h1></div>
    <div class="grid grid-2">
      <form class="card stack-sm" id="agent-form"><h3>Local agent</h3>
        <div class="field"><label for="agent-url">Agent address</label>
          <input id="agent-url" name="url" type="url" value="${current}">
          <span class="hint">Must be on this computer (http://127.0.0.1:PORT).</span></div>
        <p class="small">Pairing: ${paired ? html`<strong>paired</strong> with ${paired.url}` : "not paired in this tab"}</p>
        <div class="row"><button class="btn btn-primary" type="submit">Save</button>
          <button class="btn" type="button" data-action="unpair">Forget pairing</button></div>
      </form>
      <section class="card stack-sm" id="partner-card" aria-labelledby="partner-h"><h3 id="partner-h">Partner service (FlipValue)</h3>
        <p class="small">Resale values come from the FlipValue service and need your API key. Without a key, the resale step shows no numbers.</p>
        <div id="partner-body"><div class="loading"><div class="spinner"></div>Loading...</div></div></section>
      <div class="card stack-sm"><h3>This browser</h3>
        <p class="small">${history.list().length} report key(s) stored in this browser.</p>
        <div><button class="btn btn-danger" data-action="clear">Forget all report keys</button></div>
        <p class="small muted">This does not delete reports from the server. They expire under the retention policy.</p></div>
      <div class="card stack-sm"><h3>Server</h3>
        <dl class="kv small"><dt>Resale valuation</dt><dd>${cfg.valuation_configured ? "Configured" : "Needs an API key"}</dd>
          <dt>Default market</dt><dd>${cfg.default_region}</dd>
          <dt>Demo pricing</dt><dd>${cfg.pricing_demo ? "ON (development)" : "Off"}</dd></dl></div>
      <div class="card stack-sm"><h3>Repair price sources</h3>
        ${pricing ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Source</th><th>Status</th><th>Last retrieved</th></tr></thead><tbody>
          ${pricing.catalog.map((c) => {
            const st = pricing.sources[c.source_id];
            return html`<tr><td><a href="${c.url}" target="_blank" rel="noopener noreferrer">${c.organization}</a>
              <div class="small muted">${c.source_type.replaceAll("_", " ")} · ${c.region}</div></td>
              <td class="small">${!c.automated ? (c.usage_policy === "prohibited" ? "Not collected (site terms)" : "Not automated")
                : st?.last_error ? html`<span class="badge b-danger">Last refresh failed</span>` : st ? html`<span class="badge b-ok">${st.records} prices</span>` : "Not loaded"}
                ${c.automated && !c.enabled_for_display ? html`<div class="muted">Not approved for display</div>` : ""}</td>
              <td class="small">${st?.last_success_at ? dateTime(st.last_success_at) : "Never"}</td></tr>`;
          })}</tbody></table></div>` : html`<p class="small muted">Pricing status is unavailable.</p>`}
        <p class="small muted">Prices are refreshed by the operator with <code>python -m fixmytech.pricing.ingest</code>; they are never fetched during your session.</p></div>
    </div>`);
  $("#agent-form", ctx.el).addEventListener("submit", (ev) => {
    ev.preventDefault();
    const url = ev.target.url.value.trim();
    if (!isLoopback(url)) { toast("The agent address must start with http://127.0.0.1 or http://localhost"); return; }
    settings.set({ agentUrl: url });
    toast("Saved.");
  });
  partnerSection(ctx);
  ctx.el.addEventListener("click", (ev) => {
    const a = ev.target.closest("[data-action]")?.dataset.action;
    if (a === "unpair") { agentPairing.clear(); toast("Pairing forgotten."); ctx.rerender(); }
    if (a === "clear" && window.confirm("Forget all report keys stored in this browser?")) { history.clear(); toast("Cleared."); ctx.rerender(); }
  });
}

// The API key is typed here once and sent to the server, which keeps it. It is never shown again (only its last four
// characters), and it is not stored in this browser.
async function partnerSection(ctx) {
  const box = $("#partner-body", ctx.el);
  let adminToken = null;
  try { adminToken = sessionStorage.getItem("fmt.admin") || null; } catch { /* ignore */ }

  async function load(message) {
    let status;
    try {
      status = await api.partner.get(adminToken);
    } catch (err) {
      if (["admin_token_required", "admin_not_configured", "admin_local_only"].includes(err.code)) {
        mount(box, html`${callout("warn", "Settings are protected", err.message)}
          ${err.code === "admin_token_required" ? html`<form class="stack-sm" id="admin-form"><div class="field"><label for="admin-token">Administrator token</label>
            <input id="admin-token" type="password" autocomplete="off"></div><div><button class="btn" type="submit">Unlock</button></div></form>` : ""}`);
        $("#admin-form", ctx.el)?.addEventListener("submit", (ev) => {
          ev.preventDefault();
          adminToken = $("#admin-token", ctx.el).value.trim();
          try { sessionStorage.setItem("fmt.admin", adminToken); } catch { /* ignore */ }
          load();
        });
        return;
      }
      mount(box, errorBox(err));
      return;
    }
    mount(box, html`
      ${message || ""}
      <div class="row"><span class="status-dot ${status.configured ? "ok" : "warn"}"></span>
        <strong>${status.configured ? "Configured" : "Not configured"}</strong>
        ${status.key_hint ? html`<span class="badge b-neutral">Key ${status.key_hint}</span>` : ""}
        ${status.origin === "environment" ? html`<span class="badge b-neutral">From the server environment</span>` : ""}</div>
      ${status.base_url ? html`<p class="small muted">Service address: ${status.base_url}</p>` : ""}
      ${status.development_mock ? callout("mock", "Development mock active", "Numbers are made up for testing. Add a real key to replace it.") : ""}
      <form id="partner-form" class="stack-sm" autocomplete="off">
        <div class="field"><label for="p-url">Service address</label>
          <input id="p-url" name="base_url" type="url" placeholder="https://api.flipvalue.example" value="${status.base_url || ""}" required>
          <span class="hint">Must start with https://.</span></div>
        <div class="field"><label for="p-key">API key</label>
          <input id="p-key" name="api_key" type="password" autocomplete="new-password" spellcheck="false"
            placeholder="${status.key_hint ? "Enter a new key to replace the saved one" : "Paste the key from your partner"}" required>
          <span class="hint">Stored on the server only. It is not shown again and never appears in reports.</span></div>
        <div class="row"><button class="btn btn-primary" type="submit">Save key</button>
          <button class="btn" type="button" data-partner="test" ${status.configured ? "" : "disabled"}>Test connection</button>
          <button class="btn btn-danger" type="button" data-partner="remove" ${status.base_url ? "" : "disabled"}>Remove key</button></div>
      </form>
      <div id="partner-result" role="status"></div>`);
    $("#partner-form", ctx.el).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = ev.target;
      ev.submitter.disabled = true;
      try {
        await api.partner.save({ base_url: f.base_url.value.trim(), api_key: f.api_key.value.trim() }, adminToken);
        await load(callout("ok", "Saved", 'Use "Test connection" to check that the service accepts the key.'));
      } catch (err) {
        mount($("#partner-result", ctx.el), errorBox(err));
        ev.submitter.disabled = false;
      }
    });
    box.querySelector('[data-partner="test"]')?.addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      mount($("#partner-result", ctx.el), html`<div class="loading"><div class="spinner"></div>Testing...</div>`);
      try {
        const r = await api.partner.test(adminToken);
        mount($("#partner-result", ctx.el), r.ok ? callout("ok", "Connection works", r.message)
          : callout("danger", "The connection failed", html`${r.message} <span class="small muted">(${r.code})</span>`));
      } catch (err) { mount($("#partner-result", ctx.el), errorBox(err)); }
      ev.target.disabled = false;
    });
    box.querySelector('[data-partner="remove"]')?.addEventListener("click", async () => {
      if (!window.confirm("Remove the saved API key? Resale values will stop until a new key is added.")) return;
      try { await api.partner.remove(adminToken); await load(callout("info", "Key removed")); } catch (err) { toast(err.message); }
    });
  }
  await load();
}
