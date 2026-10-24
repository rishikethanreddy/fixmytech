// Guided hardware tests that run in the phone's own browser.
// Measurements (touch coverage, microphone level, camera start, sensor events) are taken by the browser;
// pass/fail judgements are the customer's and are labelled as such. Nothing is recorded or uploaded except
// the small result summary shown on screen.
import { html, mount, callout, $, toast } from "./ui.js";

const token = location.pathname.split("/").filter(Boolean).pop() || "";
const overlay = document.getElementById("overlay");
const app = document.getElementById("app");

async function call(method, path, body) {
  const res = await fetch(`/api/v1/phone/${encodeURIComponent(token)}${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
    credentials: "omit", cache: "no-store",
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.error?.message || `Request failed (${res.status})`);
  return data;
}

const CAN = {
  secure_context: () => window.isSecureContext,
  touch: () => "ontouchstart" in window || navigator.maxTouchPoints > 0,
  webaudio: () => Boolean(window.AudioContext || window.webkitAudioContext),
  microphone: () => Boolean(navigator.mediaDevices?.getUserMedia) && window.isSecureContext,
  camera: () => Boolean(navigator.mediaDevices?.getUserMedia) && window.isSecureContext,
  vibrate: () => "vibrate" in navigator,
  motion: () => "DeviceMotionEvent" in window && window.isSecureContext,
};
const missing = (t) => t.requires.filter((r) => CAN[r] && !CAN[r]());
const LABEL = { pass: "Passed", partial: "Problem found", fail: "Failed", not_run: "Not run" };
const BADGE = { pass: "b-ok", partial: "b-warn", fail: "b-danger", not_run: "b-neutral" };

let state = null;

async function load() {
  try {
    state = await call("GET", "");
  } catch (err) {
    mount(app, html`<div class="card stack">${callout("warn", "This test link can't be used", err.message)}
      <p class="small">Create a new link from the FixMyTech page on your computer.</p></div>`);
    return;
  }
  render();
}

function render() {
  const done = state.tests.filter((t) => t.result).length;
  mount(app, html`
    <div class="page-head"><h1>Test ${state.device_label}</h1>
      <p>Each test takes under a minute. Results appear in your report on the computer. ${done} of ${state.tests.length} done.</p></div>
    ${window.isSecureContext ? "" : html`${callout("info", "Camera, microphone and motion tests are unavailable on this link",
      "Your browser only allows them on a secure connection. Use 'Open tests on the phone' from the computer (USB), or skip them.")}<br>`}
    <div class="stack-sm">${state.tests.map((t) => {
      const miss = missing(t);
      return html`<article class="card stack-sm">
        <div class="row-between"><h3>${t.title}</h3>
          ${t.result ? html`<span class="badge ${BADGE[t.result.outcome]}">${LABEL[t.result.outcome]}</span>`
            : t.recommended ? html`<span class="badge b-warn">Recommended</span>` : ""}</div>
        <p class="small">${t.summary}</p>
        ${miss.length ? html`<p class="small muted">Not possible in this browser (${miss.join(", ").replaceAll("_", " ")}).</p>
          <div><button class="btn btn-sm" data-skip="${t.id}" data-reason="unsupported">Mark as not possible</button></div>`
          : html`<div class="row"><button class="btn btn-primary" data-run="${t.id}">${t.result ? "Run again" : "Start"}</button>
              <button class="btn btn-sm btn-ghost" data-skip="${t.id}" data-reason="skipped">Skip</button></div>`}
      </article>`;
    })}</div>
    <p class="small muted">When you're done, return to the computer and continue to the diagnosis.</p>`);
}

app.addEventListener("click", async (ev) => {
  const run = ev.target.closest("[data-run]");
  const skip = ev.target.closest("[data-skip]");
  if (run) startTest(state.tests.find((t) => t.id === run.dataset.run));
  if (skip) await submit(skip.dataset.skip, { outcome: "not_run", not_run_reason: skip.dataset.reason });
});

async function submit(testId, result) {
  try {
    await call("PUT", `/tests/${encodeURIComponent(testId)}`, result);
    toast("Result saved.");
    closeOverlay();
    await load();
  } catch (err) {
    toast(err.message, 6000);
  }
}

function openOverlay(content, cls = "") {
  overlay.className = `test-overlay ${cls}`;
  overlay.hidden = false;
  mount(overlay, content);
}

let cleanup = [];
function closeOverlay() {
  cleanup.forEach((fn) => { try { fn(); } catch { /* ignore */ } });
  cleanup = [];
  overlay.hidden = true;
  overlay.className = "test-overlay";
  overlay.replaceChildren();
}

// Shows the test's outcome choices; `measures` are attached to whatever the customer picks.
function askOutcome(test, measures = {}, note = "", preselect = null) {
  openOverlay(html`<div class="overlay-panel stack">
    <h2>${test.title}</h2>${note ? html`<p class="small">${note}</p>` : ""}
    <p>What did you see?</p>
    <div class="options">${test.outcomes.map((o) => html`<button class="btn ${o.value === preselect ? "btn-primary" : ""}"
      data-outcome="${o.value}">${o.label}</button>`)}</div>
    <button class="btn btn-ghost" data-outcome="cancel">Cancel</button></div>`);
  overlay.onclick = (ev) => {
    const b = ev.target.closest("[data-outcome]");
    if (!b) return;
    overlay.onclick = null;
    if (b.dataset.outcome === "cancel") return closeOverlay();
    submit(test.id, { outcome: b.dataset.outcome, measures });
  };
}

function startTest(test) {
  const runner = RUNNERS[test.id];
  if (!runner) return askOutcome(test);
  runner(test).catch((err) => {
    const denied = err?.name === "NotAllowedError" || err?.name === "SecurityError";
    closeOverlay();
    openOverlay(html`<div class="overlay-panel stack">${callout("warn", denied ? "Permission was not granted" : "The test could not run",
      denied ? "You can allow access in the browser settings and try again." : String(err?.message || err))}
      <button class="btn btn-primary" data-r="1">Record as not run</button><button class="btn" data-r="0">Back</button></div>`);
    overlay.onclick = (ev) => {
      const b = ev.target.closest("[data-r]");
      if (!b) return;
      overlay.onclick = null;
      if (b.dataset.r === "1") submit(test.id, { outcome: "not_run", not_run_reason: denied ? "permission_denied" : "error",
        detail: String(err?.message || err).slice(0, 200) });
      else closeOverlay();
    };
  });
}

const RUNNERS = {
  async display_colors(test) {
    const colors = ["#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000", "#808080"];
    let i = 0;
    openOverlay(html`<div class="fullscreen-color" role="button" aria-label="Next colour"><span class="color-hint">Tap for next colour (1/${colors.length})</span></div>`, "fs");
    const el = overlay.firstElementChild;
    const paint = () => { el.style.background = colors[i]; el.firstElementChild.textContent = `Tap for next colour (${i + 1}/${colors.length})`; };
    paint();
    try { await overlay.requestFullscreen?.(); } catch { /* optional */ }
    cleanup.push(() => document.fullscreenElement && document.exitFullscreen?.());
    el.onclick = () => {
      i += 1;
      if (i < colors.length) return paint();
      if (document.fullscreenElement) document.exitFullscreen?.();
      askOutcome(test);
    };
  },

  async touch_grid(test) {
    const cols = 5, rows = 8;
    let maxTouches = 0;
    const touched = new Set();
    openOverlay(html`<div class="touch-grid">${Array.from({ length: cols * rows }, (_, k) => html`<div class="cell" data-k="${k}"></div>`)}</div>
      <button class="btn btn-primary touch-done">Finish</button>`, "fs");
    const grid = overlay.querySelector(".touch-grid");
    const mark = (x, y) => {
      const cell = document.elementFromPoint(x, y)?.closest(".cell");
      if (cell && !touched.has(cell.dataset.k)) { touched.add(cell.dataset.k); cell.classList.add("hit"); }
    };
    const onTouch = (e) => { e.preventDefault(); maxTouches = Math.max(maxTouches, e.touches.length); for (const t of e.touches) mark(t.clientX, t.clientY); };
    const onPointer = (e) => { if (e.buttons || e.pointerType === "touch") mark(e.clientX, e.clientY); };
    grid.addEventListener("touchstart", onTouch, { passive: false });
    grid.addEventListener("touchmove", onTouch, { passive: false });
    grid.addEventListener("pointermove", onPointer);
    grid.addEventListener("pointerdown", onPointer);
    overlay.querySelector(".touch-done").onclick = () => {
      const total = cols * rows;
      const coverage = Math.round((touched.size / total) * 1000) / 10;
      askOutcome(test, { cells_total: total, cells_touched: touched.size, coverage_pct: coverage,
        max_simultaneous_touches: maxTouches }, `${touched.size} of ${total} squares registered a touch (${coverage}%).`,
        coverage >= 100 ? "pass" : "fail");
    };
  },

  async speaker(test) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    cleanup.push(() => ctx.close());
    openOverlay(html`<div class="overlay-panel stack"><h2>${test.title}</h2><p>Playing three tones...</p></div>`);
    for (const f of [440, 1000, 3000]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = 0.25;
      osc.frequency.value = f;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      await new Promise((r) => setTimeout(r, 700));
      osc.stop();
      await new Promise((r) => setTimeout(r, 250));
    }
    askOutcome(test);
  },

  async microphone(test) {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    cleanup.push(() => stream.getTracks().forEach((t) => t.stop()));
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const ctx = new Ctx();
    cleanup.push(() => ctx.close());
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    let peak = -120, samples = 0;
    openOverlay(html`<div class="overlay-panel stack"><h2>${test.title}</h2><p>Speak now (5 seconds). Nothing is recorded.</p>
      <div class="progress" aria-label="Microphone level"><div id="mic-level"></div></div><p class="small" id="mic-db"></p></div>`);
    const bar = $("#mic-level", overlay), label = $("#mic-db", overlay);
    const end = performance.now() + 5000;
    await new Promise((resolve) => {
      const tick = () => {
        analyser.getFloatTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += v * v;
        const db = 20 * Math.log10(Math.sqrt(sum / buf.length) || 1e-6);
        peak = Math.max(peak, db);
        samples += 1;
        bar.style.width = `${Math.max(0, Math.min(100, (db + 70) * 1.6))}%`;
        label.textContent = `Level ${db.toFixed(0)} dBFS (peak ${peak.toFixed(0)})`;
        if (performance.now() < end) requestAnimationFrame(tick); else resolve();
      };
      tick();
    });
    stream.getTracks().forEach((t) => t.stop());
    const p = Math.round(peak * 10) / 10;
    askOutcome(test, { peak_level_dbfs: p, samples }, `Peak level ${p} dBFS.`, p >= -40 ? "pass" : "fail");
  },

  camera_rear: (test) => cameraTest(test, "environment"),
  camera_front: (test) => cameraTest(test, "user"),

  async vibration(test) {
    const ok = navigator.vibrate([300, 150, 300, 150, 600]);
    openOverlay(html`<div class="overlay-panel stack"><h2>${test.title}</h2><p>${ok ? "Vibrating now..." : "The browser refused to vibrate."}</p></div>`);
    await new Promise((r) => setTimeout(r, 1800));
    askOutcome(test, {}, ok ? "" : "Your browser did not accept the vibration request.");
  },

  async motion_sensors(test) {
    if (typeof DeviceMotionEvent.requestPermission === "function") {
      const r = await DeviceMotionEvent.requestPermission();
      if (r !== "granted") throw Object.assign(new Error("Motion access denied"), { name: "NotAllowedError" });
    }
    let events = 0, g = null;
    openOverlay(html`<div class="overlay-panel stack"><h2>${test.title}</h2><p>Tilt and turn the phone for 4 seconds.</p>
      <p class="mono" id="motion-val">waiting for readings...</p></div>`);
    const out = $("#motion-val", overlay);
    const onMotion = (e) => {
      const a = e.accelerationIncludingGravity;
      if (!a || a.x === null) return;
      events += 1;
      g = Math.sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
      out.textContent = `x ${a.x.toFixed(1)}  y ${a.y.toFixed(1)}  z ${a.z.toFixed(1)}  |g| ${g.toFixed(2)} m/s²`;
    };
    window.addEventListener("devicemotion", onMotion);
    cleanup.push(() => window.removeEventListener("devicemotion", onMotion));
    await new Promise((r) => setTimeout(r, 4000));
    window.removeEventListener("devicemotion", onMotion);
    askOutcome(test, { events_received: events, gravity_ms2: g === null ? null : Math.round(g * 100) / 100 },
      events ? `${events} readings received.` : "No readings were received.", events > 5 ? "pass" : "fail");
  },
};

async function cameraTest(test, facing) {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { exact: facing } }, audio: false });
  } catch (err) {
    if (err?.name === "OverconstrainedError") {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing }, audio: false });
    } else {
      throw err;
    }
  }
  cleanup.push(() => stream.getTracks().forEach((t) => t.stop()));
  openOverlay(html`<div class="camera-wrap"><video class="camera-preview" autoplay playsinline muted></video>
    <button class="btn btn-primary camera-done">Done looking</button></div>`, "fs");
  const video = overlay.querySelector("video");
  video.srcObject = stream;
  await new Promise((r) => { video.onloadedmetadata = r; setTimeout(r, 3000); });
  const measures = { stream_started: stream.getVideoTracks().some((t) => t.readyState === "live"),
    width: video.videoWidth || null, height: video.videoHeight || null };
  overlay.querySelector(".camera-done").onclick = () => {
    stream.getTracks().forEach((t) => t.stop());
    askOutcome(test, measures, measures.stream_started ? `Preview ${measures.width}x${measures.height}.` : "The camera stream did not start.",
      measures.stream_started ? null : "fail");
  };
}

load();
