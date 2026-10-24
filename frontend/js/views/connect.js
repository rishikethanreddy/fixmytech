import { api } from "../api.js";
import { agentClient, parsePairing, AgentError } from "../agent.js";
import { agentPairing, selectedDevice } from "../store.js";
import { html, mount, callout, errorBox, $, $$, toast, cap } from "../ui.js";
import { loadSession, stepper } from "./common.js";

const ENABLE_DEBUGGING = html`
  <ol class="bullets">
    <li>On the phone, open <strong>Settings &rsaquo; About phone</strong> and tap <strong>Build number</strong> seven times
      (on some phones it is under <em>Software information</em>). This enables Developer options.</li>
    <li>Open <strong>Settings &rsaquo; System &rsaquo; Developer options</strong> and turn on <strong>USB debugging</strong>.</li>
    <li>Connect the phone with a data-capable USB cable and unlock it.</li>
    <li>When asked <em>"Allow USB debugging?"</em>, check the computer's fingerprint and tap <strong>Allow</strong>.</li>
    <li>After the diagnosis you can turn USB debugging off again.</li>
  </ol>`;

const AGENT_SETUP = html`
  <ol class="bullets">
    <li>Install <a href="https://developer.android.com/tools/releases/platform-tools" target="_blank" rel="noopener noreferrer">Android SDK Platform-Tools</a> (contains <code>adb</code>).</li>
    <li>In the FixMyTech folder, open PowerShell and run:</li>
  </ol>
  <div class="cmd">.\\.venv\\Scripts\\python -m fixmytech.agent --web-url ${location.origin}</div>
  <p class="small muted">The agent prints a pairing link and opens it in your browser. It only listens on this computer
    and only runs a fixed set of read-only checks.</p>`;

export async function connectView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, session } = s;
  if (session.mode !== "usb") {
    mount(ctx.el, html`${stepper(id, "tests", session)}<div class="card">${callout("info", "This diagnosis doesn't use the USB connection", html`<a href="#/s/${id}/tests">Continue to the hands-on tests</a>.`)}</div>`);
    return;
  }
  const cfg = await api.config();
  let timer = null;
  ctx.onLeave(() => clearTimeout(timer));

  const shell = (body) => mount(ctx.el, html`
    ${stepper(id, "connect", session)}
    <div class="page-head">
      <h1>Connect your phone</h1>
      <p>FixMyTech talks to your phone through a helper app (the <em>agent</em>) running on this computer.
        Your browser cannot access USB devices on its own.</p>
    </div>
    ${session.has_scan ? html`<div class="callout callout-ok" role="note"><span class="callout-icon">&check;</span><div>A scan is already saved for this report.
      <a href="#/s/${id}/findings">View findings</a> or scan again below.</div></div><br>` : ""}
    <div class="grid grid-2">
      <div class="stack" id="conn-main">${body}</div>
      <aside class="stack">
        <details class="card card-tight" ${session.has_scan ? "" : "open"}><summary>Enable USB debugging</summary><div class="details-body">${ENABLE_DEBUGGING}</div></details>
        <details class="card card-tight"><summary>Set up the agent</summary><div class="details-body">${AGENT_SETUP}</div></details>
        <div class="card card-tight small muted">No USB cable or the phone won't connect?
          <a href="#/s/${id}/tests">Continue without the scan</a>. The diagnosis will rely on hands-on tests and your answers.</div>
      </aside>
    </div>`);

  async function refresh() {
    clearTimeout(timer);
    const agent = agentClient(cfg.agent_url);
    // 1. agent reachable?
    try {
      await agent.health();
    } catch {
      shell(html`<div class="card stack-sm">
        <div class="row"><span class="status-dot bad"></span><h3>Agent not detected</h3></div>
        <p>We couldn't reach the FixMyTech agent at <code>${agent.base}</code>.</p>${AGENT_SETUP}
        <div class="row"><button class="btn btn-primary" data-action="retry">Check again</button></div></div>`);
      timer = setTimeout(refresh, 4000);
      return;
    }
    // 2. paired?
    let paired = agent.paired;
    if (paired) {
      try { await agent.session(); } catch (e) { paired = !(e instanceof AgentError && e.status === 401); }
    }
    if (!paired) {
      agentPairing.clear();
      shell(html`<div class="card stack-sm">
        <div class="row"><span class="status-dot warn"></span><h3>Pair this tab with the agent</h3></div>
        <p>The agent is running but this browser tab isn't paired. Open the pairing link printed in the agent's window,
          or paste it here.</p>
        <form id="pair-form" class="stack-sm">
          <div class="field"><label for="pair">Pairing link or token</label>
            <input id="pair" name="pair" type="text" autocomplete="off" spellcheck="false" placeholder="${location.origin}/#/pair?agent=...&token=..."></div>
          <div><button class="btn btn-primary" type="submit">Pair</button></div>
        </form></div>`);
      $("#pair-form", ctx.el).addEventListener("submit", (ev) => {
        ev.preventDefault();
        const p = parsePairing($("#pair", ctx.el).value, agent.base);
        if (!p) { toast("That doesn't look like a pairing link or token."); return; }
        agentPairing.set(p.url, p.token);
        refresh();
      });
      return;
    }
    // 3. ADB + devices
    let adb, devices;
    try {
      adb = await agent.adb();
      devices = adb.installed && adb.server_running ? (await agent.devices()).devices : [];
    } catch (err) {
      shell(errorBox(err, "retry"));
      return;
    }
    const ready = devices.filter((d) => d.ready);
    shell(html`
      <div class="card stack-sm">
        <div class="row"><span class="status-dot ok"></span><strong>Agent connected</strong>
          <span class="badge b-neutral">${agent.base}</span></div>
        <div class="row"><span class="status-dot ${adb.installed && adb.server_running ? "ok" : "bad"}"></span>
          <strong>ADB</strong><span class="small muted">${adb.installed ? adb.version || "installed" : "not installed"}</span></div>
        ${!adb.installed ? callout("danger", "ADB is not installed", html`Install Android SDK Platform-Tools, or start the agent with
          <code>--adb-path C:\\path\\to\\adb.exe</code>.`) : ""}
        ${adb.installed && !adb.server_running ? callout("danger", "The ADB server could not start", "Another program may be using ADB (for example phone-management software). Close it and try again.") : ""}
      </div>
      <div class="card stack-sm">
        <div class="row-between"><h3>Devices</h3><button class="btn btn-sm" data-action="retry">Refresh</button></div>
        ${devices.length === 0 ? html`<div class="panel">
            <p><strong>No phone detected.</strong></p>
            <ul class="bullets small"><li>Use a data cable (some cables only charge).</li><li>Unlock the phone and check USB debugging is on.</li>
            <li>Try another USB port. Some phones need the OEM USB driver on Windows.</li></ul></div>` : ""}
        ${devices.map((d) => html`<div class="device-row ${d.ready ? "ready" : ""}">
          <span class="status-dot ${d.ready ? "ok" : "warn"}"></span>
          <div class="spacer"><strong>${d.model || "Android device"}</strong>
            <div class="small muted">${cap(d.state.replace("_", " "))}${d.ready ? "" : html` &middot; ${d.help}`}</div></div>
          ${d.ready ? html`<button class="btn btn-primary btn-sm" data-action="select" data-serial="${d.serial}">Select</button>` : ""}
        </div>`)}
        ${ready.length > 1 ? html`<p class="small muted">Several phones are connected. Choose the one to diagnose.</p>` : ""}
      </div>
      <div id="device-detail"></div>`);
    if (!ready.length) timer = setTimeout(refresh, 3000);
    if (ready.length === 1 && devices.length === 1) selectDevice(ready[0].serial);
  }

  async function selectDevice(serial) {
    clearTimeout(timer);
    const target = $("#device-detail", ctx.el);
    mount(target, html`<div class="card"><div class="loading"><div class="spinner"></div>Reading device details...</div></div>`);
    $$(".device-row", ctx.el).forEach((r) => r.classList.toggle("ready", Boolean(r.querySelector(`[data-serial="${CSS.escape(serial)}"]`))));
    try {
      const sum = await agentClient(cfg.agent_url).summary(serial);
      const v = sum.identity.value || {};
      mount(target, html`<div class="card stack">
        <div class="row-between"><h3>${[v.manufacturer, v.model].filter(Boolean).join(" ") || "Selected device"}</h3>
          <span class="badge badge-dot b-observed">Reported by device</span></div>
        <dl class="kv"><dt>Android</dt><dd>${v.android_release || "-"} (API ${v.sdk_int ?? "?"})</dd>
          <dt>Security patch</dt><dd>${v.security_patch || "-"}</dd><dt>Build</dt><dd>${v.build_type || "-"}</dd></dl>
        <details><summary>What the scan will check (${sum.capabilities.length} checks)</summary><div class="details-body">
          <div class="table-wrap"><table class="table"><thead><tr><th>Check</th><th>Evidence quality</th><th>Expected</th></tr></thead><tbody>
          ${sum.capabilities.map((c) => html`<tr><td>${c.title}</td><td>${cap(c.quality)}</td>
            <td>${c.expected_supported === false ? html`<span class="badge b-unavailable">Not supported</span>` : c.expected_supported === null ? "Unknown" : "Should work"}</td></tr>`)}
          </tbody></table></div>
          <p class="small muted">Some checks may still be blocked by the phone's security settings. Blocked checks are reported as
            unavailable, never as "healthy".</p></div></details>
        <details><summary>What the scan cannot check</summary><div class="details-body"><ul class="bullets small">${sum.not_assessable.map((x) => html`<li>${x}</li>`)}</ul></div></details>
        <form id="scan-form" class="stack-sm">
          <label class="check"><input type="checkbox" name="consent" required>
            <span>I agree to a <strong>read-only</strong> scan of this phone. It does not change settings, install apps or read personal content.</span></label>
          <div><button class="btn btn-primary btn-lg" type="submit">Run diagnostic scan</button></div>
        </form></div>`);
      $("#scan-form", ctx.el).addEventListener("submit", (ev) => {
        ev.preventDefault();
        if (!ev.target.consent.checked) { toast("Please confirm the scan first."); return; }
        ctx.navigate(`/s/${id}/scan?serial=${encodeURIComponent(serial)}`);
      });
    } catch (err) {
      mount(target, errorBox(err, "retry"));
    }
  }

  ctx.el.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-action]");
    if (!b) return;
    if (b.dataset.action === "retry") refresh();
    if (b.dataset.action === "select") selectDevice(b.dataset.serial);
  });
  await refresh();
}

export async function scanView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  const serial = ctx.query.get("serial");
  const cfg = await api.config();
  const agent = agentClient(cfg.agent_url);
  const checks = (await api.checks()).checks;
  let timer = null;
  let cancelled = false;
  ctx.onLeave(() => { cancelled = true; clearTimeout(timer); });

  const draw = (status, extra) => {
    const done = status?.done || 0;
    const total = status?.total || checks.length;
    mount(ctx.el, html`${stepper(id, "connect", session)}
      <div class="page-head"><h1>Scanning your phone</h1>
        <p>Keep the phone connected and unlocked. This usually takes under a minute.</p></div>
      <div class="card stack">
        <div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${done}"
          aria-label="Scan progress"><div id="bar"></div></div>
        <ul class="clean checklist">${checks.map((c, i) => html`<li class="${i < done ? "done" : i === done && status?.state === "running" ? "active" : ""}">
          <span class="tick" aria-hidden="true">${i < done ? "\u2713" : ""}</span>${c.title}</li>`)}</ul>
        ${extra || ""}
      </div>`);
    const bar = $("#bar", ctx.el);
    if (bar) bar.style.width = `${Math.round((done / total) * 100)}%`;
  };

  if (!serial) { mount(ctx.el, errorBox({ message: "No device selected." })); return; }
  selectedDevice.set(id, serial);
  draw(null);
  let scanId;
  try {
    scanId = (await agent.startScan(serial)).scan_id;
  } catch (err) {
    draw(null, html`${errorBox(err)}<div class="row"><a class="btn" href="#/s/${id}/connect">Back to connection</a></div>`);
    return;
  }

  const poll = async () => {
    if (cancelled) return;
    let st;
    try {
      st = await agent.scanStatus(scanId);
    } catch (err) {
      draw(null, html`${errorBox(err)}<div class="row"><a class="btn" href="#/s/${id}/connect">Back to connection</a></div>`);
      return;
    }
    if (st.state === "running") { draw(st); timer = setTimeout(poll, 700); return; }
    if (st.state === "failed") {
      draw(st, html`${callout("danger", "The scan failed", st.error || "Unknown error")}
        <div class="row"><a class="btn" href="#/s/${id}/connect">Back to connection</a></div>`);
      return;
    }
    draw(st, html`<div class="loading"><div class="spinner"></div>Saving results...</div>`);
    try {
      await sapi.putScan(st.result);
    } catch (err) {
      draw(st, errorBox(err));
      return;
    }
    if (!st.result.complete) toast("The phone disconnected during the scan. Partial results were saved.", 6000);
    ctx.navigate(`/s/${id}/findings`);
  };
  poll();
}
