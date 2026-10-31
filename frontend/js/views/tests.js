// "Hands-on tests" step: phone-browser tests, the USB charging-port test and quick manual checks.
// Results feed the diagnosis and let the Questions step skip what a test already answered.
import { api } from "../api.js";
import { prepareAgent, AgentError } from "../agent.js";
import { html, mount, callout, errorBox, toast, $, dateTime, humanize } from "../ui.js";
import { loadSession, stepper, nextLink, agentProblem } from "./common.js";

const LABEL = { pass: "Passed", partial: "Problem found", fail: "Failed", not_run: "Skipped" };
const BADGE = { pass: "b-ok", partial: "b-warn", fail: "b-danger", not_run: "b-neutral" };
const KIND = { phone_measured: "Measured on the phone", agent_measured: "Measured by the agent", user_observed: "Your observation" };
const POWER = { usb: "USB (computer)", ac: "Wall charger", wireless: "Wireless", none: "Not connected", lost: "Phone not responding", unknown: "Unknown" };

function statusChip(r) {
  if (!r) return html`<span class="badge b-neutral">Not done</span>`;
  return html`<span class="badge badge-dot ${BADGE[r.outcome]}">${LABEL[r.outcome]}</span>`;
}

function resultLine(r) {
  if (!r) return "";
  const m = Object.entries(r.measures || {}).filter(([, v]) => v !== null && v !== undefined);
  return html`<div class="result-line small">
    <div class="muted">${r.outcome === "not_run" ? humanize(r.not_run_reason || "skipped") : KIND[r.outcome_kind] || ""} &middot; ${dateTime(r.recorded_at)}</div>
    ${r.detail ? html`<div>${r.detail}</div>` : ""}
    ${m.length ? html`<div class="muted">${m.map(([k, v]) => `${humanize(k)}: ${v === true ? "yes" : v === false ? "no" : v}`).join(" · ")}</div>` : ""}</div>`;
}

function summaryOf(tests) {
  const avail = tests.filter((t) => t.available);
  const done = avail.filter((t) => t.result);
  const passed = avail.filter((t) => t.result?.outcome === "pass");
  const problems = avail.filter((t) => ["fail", "partial"].includes(t.result?.outcome));
  return { total: avail.length, done: done.length, passed: passed.length, problems, complete: avail.length > 0 && done.length === avail.length };
}

// The flow's sections, in order: phone-browser tests, each agent test on its own, then the quick manual checks.
// Tests that can't run in this mode (e.g. agent tests without USB) are left out of the flow.
function sectionsOf(tests) {
  const avail = tests.filter((t) => t.available);
  const out = [];
  const phone = avail.filter((t) => t.method === "phone_browser");
  if (phone.length) out.push({ key: "phone", kind: "phone", title: "On your phone", tests: phone });
  avail.filter((t) => t.method === "agent_assisted")
    .forEach((t) => out.push({ key: `agent:${t.id}`, kind: "agent", title: t.title, tests: [t] }));
  const manual = avail.filter((t) => t.method === "manual");
  if (manual.length) out.push({ key: "manual", kind: "manual", title: "Quick checks", tests: manual });
  return out;
}

function sectionDone(sec) { return sec.tests.every((t) => t.result); }

function liveReadout(live, total) {
  if (!live) return html`<p class="small muted">Starting... keep the cable plugged in.</p>`;
  const charging = ["charging", "full"].includes(live.status);
  return html`<div class="live-grid" role="status">
    <div class="live-cell"><span class="small muted">Power source</span><strong>${POWER[live.plugged] || humanize(live.plugged || "unknown")}</strong></div>
    <div class="live-cell"><span class="small muted">Charging</span><strong class="${charging ? "text-ok" : ""}">${charging ? "Yes" : humanize(live.status || "no")}</strong></div>
    <div class="live-cell"><span class="small muted">Battery</span><strong>${live.level ?? "-"}%</strong></div>
    <div class="live-cell"><span class="small muted">Drops so far</span><strong class="${live.drops_so_far ? "text-danger" : ""}">${live.drops_so_far}</strong></div>
  </div>
  <p class="small">Second ${live.second} of ${live.of || total}. Gently wiggle the plug up, down and sideways (don't force it).</p>`;
}

export async function testsView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  const cfg = await api.config();
  const usb = session.mode === "usb";
  // "Open on my phone" forwards this computer's local web server to the phone (adb reverse), so it only makes
  // sense when the site itself runs on this computer. A hosted site gives a link to type on the phone instead.
  const siteIsLocal = ["127.0.0.1", "localhost"].includes(location.hostname);
  let data = null;
  let skippedQuestions = 0;
  let phoneLink = null;
  let poll = null;
  let running = false;
  let batteryPct = null;
  // The tests run as a flow of sections, one at a time. A section that becomes complete (every test has a result,
  // or the customer skipped it) slides on to the next one by itself.
  let current = null;
  let wasDone = {};
  let advanceTimer = null;
  let slideDir = "";
  let lastSnapshot = "";
  const consented = new Set(); // ticks survive redraws (results are re-polled every few seconds)
  ctx.onLeave(() => { clearInterval(poll); clearTimeout(advanceTimer); });

  if (usb && session.has_scan) {
    try {
      const obs = await sapi.observations();
      batteryPct = obs.observations.find((o) => o.check_id === "battery.status")?.value?.level_pct ?? null;
    } catch { /* the warning is optional */ }
  }

  mount(ctx.el, html`
    <div id="tests-stepper">${stepper(id, "tests", session)}</div>
    <div class="page-head row-between">
      <div><h1>Hands-on tests</h1>
        <p>Short tests check what a USB scan can't see: screen, touch, speaker, microphone, cameras, buttons and the charging port.
          Each takes under a minute. A skipped test never counts as a pass.</p></div>
      <div id="tests-next"></div>
    </div>
    <div id="tests-summary"></div>
    <div id="tests-body"><div class="loading"><div class="spinner"></div>Loading tests...</div></div>`);
  const body = $("#tests-body", ctx.el);

  async function refresh() {
    try {
      const [t, nq, fresh] = await Promise.all([sapi.tests(), sapi.nextQuestion(), sapi.get()]);
      data = t;
      skippedQuestions = nq.skipped_by_tests.length;
      mount($("#tests-stepper", ctx.el), stepper(id, "tests", fresh));
    } catch (err) { mount(body, errorBox(err)); return; }
    const sections = sectionsOf(data.tests);
    const done = Object.fromEntries(sections.map((sec) => [sec.key, sectionDone(sec)]));
    if (current === null) {
      const firstOpen = sections.findIndex((sec) => !done[sec.key]);
      current = firstOpen === -1 ? sections.length : firstOpen;
    } else {
      const sec = sections[current];
      if (sec && done[sec.key] && wasDone[sec.key] === false) scheduleAdvance(sections, done);
    }
    wasDone = done;
    // Only redraw when something changed, so a half-filled section isn't reset by the background poll.
    const snapshot = JSON.stringify([data, skippedQuestions, current]);
    if (!running && snapshot !== lastSnapshot) { lastSnapshot = snapshot; draw(); }
  }

  function scheduleAdvance(sections, done) {
    clearTimeout(advanceTimer);
    const next = sections.findIndex((sec, i) => i > current && !done[sec.key]);
    const target = next === -1 ? sections.length : next;
    toast(target < sections.length ? `Section complete. Next: ${sections[target].title}.` : "All test sections are complete.");
    advanceTimer = setTimeout(() => { if (!running && body.isConnected) goTo(target); }, 1100);
  }

  function goTo(i) {
    clearTimeout(advanceTimer);
    slideDir = i < current ? "from-left" : "from-right";
    current = i;
    draw();
    $("#tests-summary", ctx.el)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function drawSummary(sum, sections) {
    const pct = sum.total ? Math.round((sum.done / sum.total) * 100) : 0;
    const allPassed = sum.complete && sum.passed === sum.total;
    const atEnd = current >= sections.length;
    mount($("#tests-summary", ctx.el), html`
      ${sum.complete && atEnd ? html`<div class="complete-banner ${allPassed ? "is-ok" : "is-warn"}" role="status">
          <span class="complete-icon" aria-hidden="true">${allPassed ? "✓" : "!"}</span>
          <div><strong>${allPassed ? "All tests passed. This section is complete." : "All tests done. This section is complete."}</strong>
            <div class="small">${allPassed ? "No hardware problem showed up in the hands-on tests."
              : sum.problems.length ? `Problems found: ${sum.problems.map((t) => t.title).join(", ")}.` : "Some tests were skipped."}</div></div>
        </div>` : ""}
      <div class="card flow-progress">
        <div class="flow-head">
          <div><div class="small muted">${atEnd ? "Hands-on tests" : `Section ${current + 1} of ${sections.length}`}</div>
            <strong class="flow-title">${atEnd ? "All sections done" : sections[current].title}</strong></div>
          <div class="flow-stats">
            <span class="flow-stat"><strong>${sum.done}/${sum.total}</strong> done</span>
            <span class="flow-stat is-ok"><strong>${sum.passed}</strong> passed</span>
            ${sum.problems.length ? html`<span class="flow-stat is-warn"><strong>${sum.problems.length}</strong> with a problem</span>` : ""}
          </div>
        </div>
        <div class="progress progress-thin" role="progressbar" aria-label="Tests done" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><div class="pbar"></div></div>
        <ol class="flow-steps" aria-label="Test sections">
          ${sections.map((sec, i) => {
            const st = sectionDone(sec) ? "done" : "";
            return html`<li><button type="button" class="flow-step ${st} ${i === current ? "is-here" : ""}" data-goto="${i}" ${i === current ? html`aria-current="step"` : ""}>
              <span class="num" aria-hidden="true">${st ? "✓" : i + 1}</span>
              <span class="flow-step-text"><span class="flow-step-name">${sec.title}</span>
                <span class="flow-step-count">${sec.tests.filter((t) => t.result).length} of ${sec.tests.length} done</span></span></button></li>`;
          })}
          <li><button type="button" class="flow-step ${sum.complete ? "done" : ""} ${atEnd ? "is-here" : ""}" data-goto="${sections.length}" ${atEnd ? html`aria-current="step"` : ""}>
            <span class="num" aria-hidden="true">${sum.complete ? "✓" : sections.length + 1}</span>
            <span class="flow-step-text"><span class="flow-step-name">Summary</span><span class="flow-step-count">Review results</span></span></button></li>
        </ol>
        ${skippedQuestions ? html`<p class="small muted">Your test results already answer ${skippedQuestions} question${skippedQuestions === 1 ? "" : "s"}, so you won't be asked ${skippedQuestions === 1 ? "it" : "them"}.</p>` : ""}
      </div>`);
    $("#tests-summary .pbar", ctx.el).style.width = `${pct}%`;
    mount($("#tests-next", ctx.el), nextLink(`#/s/${id}/questions`, "Continue to questions"));
  }

  function flowNav(sec) {
    const open = sec.tests.filter((t) => !t.result).length;
    return html`<div class="row flow-nav no-print">
      ${current > 0 ? html`<button class="btn btn-ghost" data-goto="${current - 1}">&larr; Back</button>` : ""}
      <span class="spacer"></span>
      ${open ? html`<button class="btn" data-skip-section="${sec.key}">Skip this section</button>`
        : html`<button class="btn btn-primary" data-goto="${current + 1}">Next section &rarr;</button>`}
    </div>`;
  }

  function phoneSection(sec) {
    const phoneTests = sec.tests;
    const phoneDone = phoneTests.filter((t) => t.result).length;
    return html`<section class="card stack test-section" aria-labelledby="phone-h">
        <div class="section-head"><span class="section-icon" aria-hidden="true">${phoneIcon}</span>
          <div class="spacer"><h2 id="phone-h">On your phone</h2><p class="small muted">Open a test page in the phone's browser. Nothing is installed.</p></div>
          ${phoneDone === phoneTests.length ? html`<span class="badge badge-dot b-ok">Completed</span>` : html`<span class="badge b-neutral">${phoneDone}/${phoneTests.length}</span>`}</div>
        ${phoneLink ? html`<div class="panel stack-sm">
            ${phoneLink.openedOnPhone ? callout("ok", "Tests opened on your phone", "Unlock the phone; the test page is open in its browser. Results appear here automatically.") : ""}
            <p class="small">${phoneLink.openedOnPhone ? "If you close it, open this address in the phone's browser again" : "On your phone, open this address in the browser"}
              (valid until ${dateTime(phoneLink.expires_at)}; a new link replaces this one):</p>
            <div class="cmd">${phoneLink.openedOnPhone && siteIsLocal ? `http://localhost:${location.port}` : location.origin}${phoneLink.path}</div>
            ${phoneLink.openedOnPhone ? "" : html`<p class="small muted">Camera, microphone and motion tests need a secure connection, which a typed link only has if the site uses https.
              Use 'Open hands-on tests on your phone (USB)' for those.</p>`}
          </div>` : ""}
        <div class="row">
          <button class="btn btn-primary" data-t="open-usb">Open hands-on tests on your phone (USB)</button>
          <button class="btn" data-t="link">Show a link to type on the phone</button>
          <button class="btn btn-ghost btn-sm" data-t="refresh">Refresh results</button>
        </div>
        <div id="phone-status"></div>
        <ul class="clean test-list">${phoneTests.map((t) => html`<li class="test-row">
          <div class="spacer"><div class="test-title">${t.title}${t.recommended && !t.result ? html` <span class="badge b-warn">Recommended</span>` : ""}</div>
            ${resultLine(t.result)}</div>
          <div class="row">${statusChip(t.result)}${t.result ? "" : html`<button class="btn btn-sm btn-ghost" data-skip="${t.id}">Skip</button>`}</div></li>`)}</ul>
        ${flowNav(sec)}
      </section>`;
  }

  function agentSection(sec) {
    const t = sec.tests[0];
    return html`<section class="card stack test-section" aria-labelledby="t-${t.id}">
        <div class="section-head"><span class="section-icon" aria-hidden="true">${plugIcon}</span>
          <div class="spacer"><h2 id="t-${t.id}">${t.title}</h2><p class="small muted">${t.summary}</p></div>${statusChip(t.result)}</div>
        ${resultLine(t.result)}
        <ol class="bullets small">${t.instructions.map((i) => html`<li>${i}</li>`)}</ol>
        ${batteryPct !== null && batteryPct >= 80 ? callout("warn", `Battery at ${batteryPct}% when scanned`,
          "Many phones pause charging above about 80% to protect the battery. The test still checks that the connection stays stable, but if charging is paused the result may be inconclusive. For a full check, repeat it when the battery is lower.") : ""}
        <label class="check"><input type="checkbox" data-consent="${t.id}" ${consented.has(t.id) ? "checked" : ""}><span class="small">I agree: the agent reads the phone's charging status once a second for 45 seconds. Nothing is changed on the phone.</span></label>
        <div class="row"><button class="btn btn-primary" data-agent="${t.id}">${t.result ? "Run the test again" : "Start the test"}</button></div>
        <div data-agent-status="${t.id}"></div>
        ${flowNav(sec)}
      </section>`;
  }

  function manualSection(sec) {
    const manual = sec.tests;
    return html`<section class="card stack test-section" aria-labelledby="manual-h">
        <div class="section-head"><span class="section-icon" aria-hidden="true">${handIcon}</span>
          <div class="spacer"><h2 id="manual-h">Quick checks you do yourself</h2><p class="small muted">Try it, then tell us what happened.</p></div>
          ${manual.every((t) => t.result) ? html`<span class="badge badge-dot b-ok">Completed</span>` : html`<span class="badge b-neutral">${manual.filter((t) => t.result).length}/${manual.length}</span>`}</div>
        ${manual.map((t) => html`<article class="test-row test-row-block">
          <div class="row-between"><div class="test-title">${t.title}${t.recommended && !t.result ? html` <span class="badge b-warn">Recommended</span>` : ""}</div>${statusChip(t.result)}</div>
          <p class="small">${t.summary}</p>
          <ul class="bullets small">${t.instructions.map((i) => html`<li>${i}</li>`)}</ul>
          ${resultLine(t.result)}
          <div class="row">${t.outcomes.map((o) => html`<button class="btn btn-sm ${t.result?.outcome === o.value ? "is-selected" : ""}" data-manual="${t.id}" data-outcome="${o.value}">${o.label}</button>`)}
            <button class="btn btn-sm btn-ghost" data-manual="${t.id}" data-outcome="not_run">Skip</button></div>
        </article>`)}
        ${flowNav(sec)}
      </section>`;
  }

  function finishSection(sections, sum, unavailable) {
    return html`<section class="card stack test-section" aria-labelledby="fin-h">
        <h2 id="fin-h">${sum.complete ? "Hands-on tests finished" : "Some sections still have open tests"}</h2>
        <ul class="clean test-list">${sections.map((sec, i) => html`<li class="test-row">
          <div class="spacer"><div class="test-title">${sec.title}</div>
            <div class="small muted">${sec.tests.map((t) => `${t.title}: ${t.result ? LABEL[t.result.outcome] : "not done"}`).join(" · ")}</div></div>
          <button class="btn btn-sm btn-ghost" data-goto="${i}">${sectionDone(sec) ? "Review" : "Open"}</button></li>`)}</ul>
        ${unavailable.length ? html`<p class="small muted">Not available in this mode: ${unavailable.map((t) => t.title).join(", ")}.
          ${unavailable[0].unavailable_reason || ""}</p>` : ""}
        <div class="row no-print">${nextLink(`#/s/${id}/questions`, "Continue to questions")}
          <a class="btn btn-ghost" href="#/s/${id}/diagnosis">Skip to results</a></div>
      </section>`;
  }

  function draw() {
    const tests = data.tests;
    const sum = summaryOf(tests);
    const sections = sectionsOf(tests);
    current = Math.min(current ?? 0, sections.length);
    drawSummary(sum, sections);
    const sec = sections[current];
    const view = !sec ? finishSection(sections, sum, tests.filter((t) => !t.available))
      : sec.kind === "phone" ? phoneSection(sec) : sec.kind === "agent" ? agentSection(sec) : manualSection(sec);
    mount(body, html`<div class="flow-slide ${slideDir}">${view}</div>`);
    slideDir = "";
  }

  async function runAgentTest(tid, button) {
    if (!consented.has(tid)) { toast("Please tick the box to agree to the test first."); return; }
    const status = $(`[data-agent-status="${tid}"]`, body);
    button.disabled = true;
    running = true;
    mount(status, html`<div class="loading"><div class="spinner"></div>Checking the agent and the phone...</div>`);
    try {
      const { agent, serial } = await prepareAgent(cfg.agent_url, id, "connector_test");
      const total = 45;
      const { job_id: jobId } = await agent.connectorTest(serial, total);
      for (;;) {
        const st = await agent.job(jobId);
        if (!body.isConnected) return;
        if (st.state === "running") {
          const done = st.live?.second ?? st.done ?? 0;
          mount(status, html`<div class="panel stack-sm live-panel">
            <div class="progress"><div class="pbar"></div></div>${liveReadout(st.live, total)}</div>`);
          $(".pbar", status).style.width = `${Math.round((done / total) * 100)}%`;
          await new Promise((r) => setTimeout(r, 1000));
          continue;
        }
        if (st.state === "failed") {
          mount(status, callout("danger", "The test couldn't finish", html`${st.error || ""} Keep the phone connected and unlocked, then run it again.`));
          return;
        }
        await sapi.putAgentTest(tid, st.result);
        toast("Charging port test saved.");
        running = false;
        await refresh();
        return;
      }
    } catch (err) {
      mount(status, err instanceof AgentError ? agentProblem(err, id) : errorBox(err));
    } finally {
      running = false;
      button.disabled = false;
    }
  }

  $("#tests-summary", ctx.el).addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-goto]");
    if (b && !running) goTo(Number(b.dataset.goto));
  });

  body.addEventListener("change", (ev) => {
    const c = ev.target.closest("[data-consent]");
    if (c) { if (c.checked) consented.add(c.dataset.consent); else consented.delete(c.dataset.consent); }
  });

  body.addEventListener("click", async (ev) => {
    const b = ev.target.closest("button");
    if (!b || running) return;
    try {
      if (b.dataset.goto) { goTo(Number(b.dataset.goto)); return; }
      if (b.dataset.skipSection) {
        const sec = sectionsOf(data.tests).find((x) => x.key === b.dataset.skipSection);
        b.disabled = true;
        for (const t of sec.tests.filter((x) => !x.result)) {
          await sapi.putTest(t.id, { outcome: "not_run", not_run_reason: "skipped" });
        }
        await refresh();
        return;
      }
      if (b.dataset.t === "refresh") { await refresh(); return; }
      if (b.dataset.t === "link" || b.dataset.t === "open-usb") {
        b.disabled = true;
        const link = await sapi.phoneLink();
        phoneLink = { ...link, openedOnPhone: false };
        if (b.dataset.t === "open-usb") {
          try {
            const { agent, serial } = await prepareAgent(cfg.agent_url, id, "phone_tests");
            const port = Number(location.port || (location.protocol === "https:" ? 443 : 80));
            await agent.openPhoneTests(serial, siteIsLocal ? { port } : { siteOrigin: location.origin }, link.token);
            phoneLink.openedOnPhone = true;
          } catch (err) {
            draw();
            mount($("#phone-status", body), err instanceof AgentError ? agentProblem(err, id) : errorBox(err));
            return;
          }
        }
        clearInterval(poll);
        poll = setInterval(refresh, 5000);
        draw();
        return;
      }
      if (b.dataset.skip) {
        await sapi.putTest(b.dataset.skip, { outcome: "not_run", not_run_reason: "skipped" });
        await refresh();
        return;
      }
      if (b.dataset.manual) {
        const outcome = b.dataset.outcome;
        await sapi.putTest(b.dataset.manual, outcome === "not_run" ? { outcome, not_run_reason: "skipped" } : { outcome });
        toast("Result saved.");
        await refresh();
        return;
      }
      if (b.dataset.agent) await runAgentTest(b.dataset.agent, b);
    } catch (err) {
      b.disabled = false;
      toast(err.message, 6000);
    }
  });

  await refresh();
}

const phoneIcon = html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M10.5 18.5h3"/></svg>`;
const plugIcon = html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0z"/><path d="M12 17v5"/></svg>`;
const handIcon = html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11V5a1.5 1.5 0 0 1 3 0v6M12 10V3.5a1.5 1.5 0 0 1 3 0V11M15 10.5V6a1.5 1.5 0 0 1 3 0v8a7 7 0 0 1-7 7h-.5a6.5 6.5 0 0 1-5.4-2.9L3 14.5a1.6 1.6 0 0 1 2.6-1.9L9 15V8a1.5 1.5 0 0 1 3 0"/></svg>`;
