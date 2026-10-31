import { html, mount, callout, errorBox, sourceBadge, supportMeter, humanize, cap, dateTime, money, moneyRange, $, toast } from "../ui.js";
import { loadSession, stepper, nextLink, agentProblem } from "./common.js";
import { api } from "../api.js";
import { prepareAgent, AgentError } from "../agent.js";

// Human labels and units for observation fields. Unknown fields fall back to a humanized key.
const FIELDS = {
  manufacturer: ["Manufacturer"], brand: ["Brand"], model: ["Model"], android_release: ["Android version"],
  sdk_int: ["API level"], security_patch: ["Security patch"], build_type: ["Build type"],
  level_pct: ["Charge level", "%"], temperature_c: ["Temperature", "°C"], voltage_mv: ["Voltage", "mV"],
  health: ["OS health flag"], status: ["Charging status"], present: ["Battery present"], plugged: ["Power source"],
  max_charging_current_ma: ["Max charging current", "mA"], technology: ["Chemistry"], cycle_count: ["Charge cycles"],
  values_overridden: ["Values overridden"], capacity_ratio_pct: ["Estimated capacity vs design", "%"],
  design_capacity_mah: ["Design capacity", "mAh"], learned_capacity_mah: ["Learned capacity", "mAh"],
  min_learned_capacity_mah: ["Min learned capacity", "mAh"], max_learned_capacity_mah: ["Max learned capacity", "mAh"],
  charge_full_uah: ["Full charge", "µAh"], charge_full_design_uah: ["Design full charge", "µAh"],
  capacity_source: ["Capacity source"], battery_status: ["Battery status"], assessment: ["Assessment"],
  total_gb: ["Total", "GB"], used_gb: ["Used", "GB"], free_gb: ["Free", "GB"], free_pct: ["Free", "%"],
  total_mb: ["Total RAM", "MB"], available_mb: ["Available RAM", "MB"], available_pct: ["Available", "%"],
  throttling_status: ["Throttling"], throttling_code: ["Throttling level"], max_cpu_c: ["CPU max", "°C"],
  max_gpu_c: ["GPU max", "°C"], max_battery_c: ["Battery sensor", "°C"], max_skin_c: ["Skin", "°C"],
  max_usb_port_c: ["USB port", "°C"], app_crashes: ["App crashes"], app_native_crashes: ["App native crashes"],
  app_anrs: ["App freezes (ANR)"], system_app_crashes: ["System app crashes"], system_app_anrs: ["System app freezes"],
  system_server_crashes: ["Core system crashes"], tombstones: ["Native crash dumps"], kernel_panics: ["Kernel crash logs"],
  history_days_available: ["History available", "days"], uptime_hours: ["Uptime", "h"],
  last_boot_reason_class: ["Last restart reason"], abnormal_restarts_recent: ["Abnormal restarts (7 days)"],
  restarts_recorded: ["Restarts recorded"], camera_devices: ["Cameras reported"],
  touchscreen_registered: ["Touchscreen detected"], airplane_mode: ["Airplane mode"], wifi_enabled: ["Wi-Fi enabled"],
  sim_ready: ["SIM ready"], sim_absent: ["No SIM"], sim_slots_reported: ["SIM slots"], mobile_network_type: ["Network type"],
  sensor_count: ["Sensors reported"], has_accelerometer: ["Accelerometer"], has_gyroscope: ["Gyroscope"],
  has_magnetic_field: ["Compass"], has_proximity: ["Proximity"], has_light: ["Light sensor"], has_pressure: ["Barometer"],
  has_step_counter: ["Step counter"], has_gravity: ["Gravity sensor"], write_latency_ms: ["Write latency", "ms"],
  recent_write_speed_kbps: ["Recent write speed", "kB/s"], verified_boot_state: ["Verified boot"],
  bootloader_locked: ["Bootloader locked"], release_keys: ["Official signing"], debuggable_build: ["Debuggable build"],
  fatal_exceptions: ["App crashes (log)"], anr_events: ["Freezes (log)"], native_crashes: ["Native crashes"],
  low_memory_kills: ["Low-memory kills"], watchdog_events: ["Watchdog resets"], thermal_shutdown_events: ["Thermal shutdowns"],
  kernel_panic_events: ["Kernel panics"], text_megabytes: ["Report size", "MB"],
};

const ACRONYMS = { usb: "USB", ac: "AC (wall charger)", lte: "LTE", nr: "5G (NR)", "li-ion": "Li-ion" };

function fmtValue(v) {
  if (v === true) return "Yes";
  if (v === false) return "No";
  if (v === null || v === undefined) return "-";
  if (typeof v === "string") return ACRONYMS[v.toLowerCase()] || humanize(v);
  return String(v);
}

function valueList(value) {
  const entries = Object.entries(value || {}).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return "";
  return html`<dl class="kv">${entries.map(([k, v]) => {
    const [label, unit] = FIELDS[k] || [humanize(k)];
    return html`<dt>${label}</dt><dd>${fmtValue(v)}${unit && typeof v === "number" ? ` ${unit}` : ""}</dd>`;
  })}</dl>`;
}

const STATUS_TEXT = {
  ok: "Collected", unsupported: "Not supported on this phone", permission_denied: "Blocked by the phone's security policy",
  timeout: "Timed out", error: "Could not be read", device_disconnected: "Phone disconnected", skipped: "Skipped",
};

export async function findingsView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  const obs = await sapi.observations();
  if (!obs.has_scan) {
    mount(ctx.el, html`${stepper(id, "findings", session)}<div class="card stack">
      ${callout("info", "No scan yet", "Connect your phone to collect device readings, or continue without them.")}
      <div class="row"><a class="btn btn-primary" href="#/s/${id}/connect">Connect phone</a><a class="btn" href="#/s/${id}/tests">Continue without a scan</a></div></div>`);
    return;
  }
  const ok = obs.observations.filter((o) => o.status === "ok");
  const bad = obs.observations.filter((o) => o.status !== "ok");
  mount(ctx.el, html`
    ${stepper(id, "findings", session)}
    <div class="page-head row-between">
      <div><h1>What the phone reported</h1>
        <p>Readings from ${obs.identity?.manufacturer || ""} ${obs.identity?.model || "your phone"}, collected ${dateTime(obs.scanned_at)}.
          These are observations, not a diagnosis yet.</p></div>
      ${nextLink(`#/s/${id}/tests`, "Continue to hands-on tests")}
    </div>
    ${obs.complete === false ? html`${callout("warn", "The scan was interrupted", "The phone disconnected before every check finished. Results below are partial; you can reconnect and scan again.")}<br>` : ""}
    <div class="grid grid-obs">
      ${ok.map((o) => html`<article class="card obs-card">
        <div class="obs-head"><h3>${o.title}</h3>${sourceBadge(o.evidence_kind)}</div>
        ${valueList(o.value)}
        ${o.limitations.length ? html`<details><summary class="small">Limitations</summary><div class="details-body"><ul class="bullets small">${o.limitations.map((l) => html`<li>${l}</li>`)}</ul></div></details>` : ""}
        <div class="small muted">Source: <code>${o.source}</code>${o.privacy_note ? html`<br>${o.privacy_note}` : ""}</div>
      </article>`)}
    </div>
    ${bad.length ? html`<h2 class="sr-only">Unavailable checks</h2><br><div class="card stack-sm">
      <h3>Checks that could not be completed</h3>
      <p class="small muted">An unavailable check tells us nothing about that part, good or bad. It is never counted as "healthy".</p>
      <div class="table-wrap"><table class="table"><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead><tbody>
      ${bad.map((o) => html`<tr><td>${o.title}</td><td>${sourceBadge("unavailable")} ${STATUS_TEXT[o.status] || o.status}</td><td class="small">${o.detail || ""}</td></tr>`)}
      </tbody></table></div></div>` : ""}
    <br><details class="card card-tight"><summary>Not assessable over USB</summary><div class="details-body">
      <ul class="bullets small">${obs.not_assessable.map((x) => html`<li>${x}</li>`)}</ul>
      <p class="small muted">The next steps cover these with short hands-on tests and, where needed, a few questions.</p></div></details>
    <br><section class="card stack-sm" id="bugreport-card">
      <h3>Optional: deeper software check (bug report)</h3>
      <p class="small">Android can produce a "bug report": a large file of system logs. If you agree, the agent creates it on
        <strong>this computer</strong>, counts crashes, freezes, low-memory kills and thermal or watchdog events, and then
        <strong>deletes the file</strong>. Only those counts are added to your report. It takes 1-3 minutes; keep the phone connected and unlocked.</p>
      <p class="small muted">Bug reports can contain personal information such as account names and app activity. That is why
        the file never leaves your computer and only counts are kept.</p>
      ${obs.observations.some((o) => o.check_id === "bugreport.summary") ? callout("ok", "Bug report summary added", "See the 'Bug report crash summary' card above.") : ""}
      <label class="check"><input type="checkbox" id="br-consent"><span class="small">I agree to create a bug report on this computer for counting, then delete it.</span></label>
      <div><button class="btn" id="br-run">Run the deeper check</button></div>
      <div id="br-status" role="status"></div>
    </section>
    <br><div class="row no-print">${nextLink(`#/s/${id}/tests`, "Continue to hands-on tests")}</div>
  `);
  $("#br-run", ctx.el).addEventListener("click", async (ev) => {
    const status = $("#br-status", ctx.el);
    if (!$("#br-consent", ctx.el).checked) { mount(status, callout("warn", "Please agree first")); return; }
    ev.target.disabled = true;
    mount(status, html`<div class="loading"><div class="spinner"></div>Checking the agent and the phone...</div>`);
    try {
      const cfg = await api.config();
      const { agent, serial } = await prepareAgent(cfg.agent_url, id, "bugreport");
      const { job_id: jobId } = await agent.bugreport(serial);
      const started = Date.now();
      for (;;) {
        const st = await agent.job(jobId);
        if (!ctx.el.isConnected) return;
        if (st.state === "running") {
          mount(status, html`<div class="loading"><div class="spinner"></div>Creating and reading the bug report... ${Math.round((Date.now() - started) / 1000)}s</div>`);
          await new Promise((r) => setTimeout(r, 2000));
          continue;
        }
        if (st.state === "failed") {
          mount(status, callout("danger", "The bug report could not be created", html`${st.error} Keep the phone unlocked and connected, then try again.`));
          ev.target.disabled = false;
          return;
        }
        await sapi.putObservation("bugreport.summary", st.result);
        toast("Bug report summary added.");
        ctx.rerender();
        return;
      }
    } catch (err) {
      mount(status, err instanceof AgentError ? agentProblem(err, id) : errorBox(err));
      ev.target.disabled = false;
    }
  });
}

const STOP_TEXT = {
  confident_enough: "The evidence already points clearly in one direction, so more questions are unlikely to change the result.",
  budget_reached: "That's the maximum number of questions. You can still review or change your answers.",
  no_more_questions: "There are no further relevant questions.",
  no_informative_questions: "The remaining questions wouldn't add useful information.",
  no_questions_needed: "Your phone's readings and the hands-on tests already give a clear answer, so there is nothing we need to ask.",
  safety_concern: "Your answers point to a possible safety risk. Please read the safety advice on the diagnosis page before anything else.",
  professional_inspection: "Liquid exposure is likely. Remote questions can't assess corrosion, so a professional inspection is the right next step.",
};

function skippedPanel(skipped) {
  if (!skipped?.length) return "";
  const n = skipped.length;
  return html`<details class="card card-tight"><summary>${n} question${n === 1 ? "" : "s"} skipped because your tests answered ${n === 1 ? "it" : "them"}</summary>
    <div class="details-body"><ul class="clean stack-sm">${skipped.map((q) => html`<li class="skip-item">
      <span class="badge badge-dot b-ok">Answered by a test</span><div><div>${q.text}</div><div class="small muted">Because ${q.reason}.</div></div></li>`)}</ul></div></details>`;
}

export async function questionsView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  const [nq, answers] = await Promise.all([sapi.nextQuestion(), sapi.answers()]);
  const p = session.progress || {};
  mount(ctx.el, html`
    ${stepper(id, "questions", session)}
    <div class="page-head"><h1>A few questions</h1>
      <p>We only ask what your ${session.has_scan ? "phone's readings and " : ""}hands-on tests couldn't tell us. Each question is
        chosen because its answer can change the result.</p>
      ${p.tests_done ? "" : html`<p class="small muted">Tip: <a href="#/s/${id}/tests">hands-on tests</a> can answer some of these for you.</p>`}</div>
    <div id="q-area"></div>`);
  const qArea = $("#q-area", ctx.el);
  draw(nq, answers.answers);

  function draw(next, answered) {
    const q = next.question;
    const stopKind = next.stop_reason === "safety_concern" ? "danger" : next.stop_reason === "professional_inspection" ? "warn" : "ok";
    const stopTitle = next.stop_reason === "safety_concern" ? "Please stop and read the safety advice"
      : next.stop_reason === "no_questions_needed" ? "No questions needed" : "Thanks, that's enough to work with";
    mount(qArea, html`
      <div class="grid grid-main">
        <div class="stack">
          ${q ? html`<form class="card stack question-card" id="q-form" aria-describedby="q-why">
            <div class="row-between"><span class="small muted">Question ${next.asked + 1} of at most ${next.budget}</span>
              ${next.asked > 0 ? html`<button type="button" class="btn btn-sm btn-ghost" data-action="back">&larr; Back</button>` : ""}</div>
            <div class="progress progress-thin" aria-hidden="true"><div class="pbar" data-pct="${Math.round((next.asked / next.budget) * 100)}"></div></div>
            <fieldset class="stack-sm"><legend><h2>${q.text}</h2></legend>
              ${q.help ? html`<p class="small muted">${q.help}</p>` : ""}
              <div class="options">${q.options.map((o) => html`<label class="check">
                <input type="${q.type === "multi_choice" ? "checkbox" : "radio"}" name="answer" value="${o.value}"
                  ${o.exclusive ? html`data-exclusive="1"` : ""}><span>${o.label}</span></label>`)}</div>
            </fieldset>
            <p class="small muted why" id="q-why"><strong>Why we ask:</strong> ${next.why}</p>
            <div id="q-error"></div>
            <div class="row"><button class="btn btn-primary" type="submit">Continue</button>
              <a class="btn btn-ghost" href="#/s/${id}/diagnosis">Skip to results</a></div>
          </form>` : html`<div class="card stack done-card">
            ${callout(stopKind, stopTitle, STOP_TEXT[next.stop_reason] || "")}
            <div class="row">${nextLink(`#/s/${id}/diagnosis`, "See the diagnosis")}
              ${next.stop_reason === "no_questions_needed" ? html`<button class="btn" data-action="more">I have another problem to describe</button>` : ""}
              ${answered.length ? html`<button class="btn" data-action="back">Change my last answer</button>` : ""}</div></div>`}
          ${skippedPanel(next.skipped_by_tests)}
        </div>
        <aside class="card stack-sm">
          <h3>Your answers</h3>
          ${answered.length ? html`<ul class="clean">${answered.map((a) => html`<li class="panel answer-item">
              <div class="small muted">${a.question}</div><div><strong>${a.labels.join(", ")}</strong></div>
              <button class="btn btn-sm btn-ghost" data-action="edit" data-qid="${a.question_id}">Change</button></li>`)}</ul>`
            : html`<p class="small muted">None yet.</p>`}
          <p class="small muted">${sourceBadge("customer_reported")} Answers are shown as customer-reported in your report.</p>
        </aside>
      </div>`);
    const bar = $(".pbar", qArea);
    if (bar) bar.style.width = `${bar.dataset.pct}%`;

    const form = $("#q-form", qArea);
    if (form) {
      form.addEventListener("change", (ev) => {
        const t = ev.target;
        if (t.type !== "checkbox" || !t.checked) return;
        form.querySelectorAll('input[name="answer"]').forEach((i) => {
          if (i !== t && (t.dataset.exclusive || i.dataset.exclusive)) i.checked = false;
        });
      });
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const chosen = [...form.querySelectorAll('input[name="answer"]:checked')].map((i) => i.value);
        if (!chosen.length) { mount($("#q-error", qArea), callout("warn", "Choose an answer to continue")); return; }
        ev.submitter.disabled = true;
        try {
          const n = await sapi.answer(q.id, q.type === "multi_choice" ? chosen : chosen[0]);
          draw(n, (await sapi.answers()).answers);
        } catch (err) {
          mount($("#q-error", qArea), errorBox(err));
          ev.submitter.disabled = false;
        }
      });
    }
  }

  qArea.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-action]");
    if (!b) return;
    try {
      if (b.dataset.action === "more") {
        draw(await sapi.nextQuestion(true), (await sapi.answers()).answers);
        return;
      }
      const current = (await sapi.answers()).answers;
      const qid = b.dataset.action === "back" ? current.at(-1)?.question_id : b.dataset.qid;
      if (!qid) return;
      draw(await sapi.unanswer(qid), (await sapi.answers()).answers);
    } catch (err) { toast(err.message); }
  });
}

function evidenceItem(e) {
  return html`<div class="evidence-item">
    <div class="row-between"><span>${e.rationale}</span>
      <span class="w ${e.weight > 0 ? "w-pos" : "w-neg"}" title="Ranking points from rule ${e.rule_id}">${e.weight > 0 ? "+" : ""}${e.weight}</span></div>
    <div class="fact-chips">${e.facts.map((f) => html`<span class="fact-chip">${sourceBadge(f.source)}
      ${(FIELDS[f.fact.split(".").pop()] || [humanize(f.fact.split(".").pop())])[0]}: ${Array.isArray(f.value) ? f.value.map(humanize).join(", ") : fmtValue(f.value)}</span>`)}</div>
    ${e.limitations.length ? html`<div class="small muted">Limitation: ${e.limitations.join(" ")}</div>` : ""}
  </div>`;
}

function repairCostChip(rc) {
  if (!rc) return "";
  if (rc.status === "insufficient_pricing_data") {
    return html`<div class="repair-cost-chip no-data">
      <span class="badge b-neutral">No price data</span>
      <span class="small muted">${rc.conditional_label}</span>
      ${rc.official_links?.length ? html`<div class="small">Check:
        ${rc.official_links.map((l, i) => html`${i ? ", " : ""}<a href="${l.url}" target="_blank" rel="noopener noreferrer">${l.label}</a>`)}</div>` : ""}
    </div>`;
  }
  const SCOPE_SHORT = { parts_and_labour: "Part + labour", parts_only: "Part only", inspection_fee: "Inspection" };
  return html`<div class="repair-cost-chip has-data">
    <div class="row">
      <span class="badge badge-dot b-estimate">${SCOPE_SHORT[rc.scope] || rc.scope}</span>
      <span class="money">${moneyRange(rc.low, rc.high, rc.currency)}</span>
    </div>
    <div class="small muted">${rc.repair_name} &middot; ${rc.conditional_label}</div>
    ${rc.scope_warning ? html`<div class="small muted"><em>${rc.scope_warning}</em></div>` : ""}
    ${rc.confirmation_note ? html`<div class="small muted"><em>${rc.confirmation_note}</em></div>` : ""}
    ${rc.tax ? html`<div class="small muted">${rc.tax === "excluded" ? "Before tax" : rc.tax === "included" ? "Including tax" : "Tax treatment unknown"}</div>` : ""}
    <details><summary class="small">Where this price comes from</summary>
      <div class="details-body small stack-sm">
        ${rc.evidence?.length ? html`<ul class="clean">${rc.evidence.map((e) => html`<li>
          <strong>${e.label}</strong>: ${money(e.amount, rc.currency)}
          <div class="muted">Source: <a href="${e.source_url}" target="_blank" rel="noopener noreferrer">${e.organization}</a>,
            retrieved ${e.retrieved_at ? new Date(e.retrieved_at).toLocaleDateString(undefined, {day:"numeric",month:"short",year:"numeric"}) : "-"}</div>
        </li>`)}</ul>` : ""}
        ${rc.notes?.length ? html`<ul class="bullets">${rc.notes.map((n) => html`<li>${n}</li>`)}</ul>` : ""}
      </div>
    </details>
  </div>`;
}

function candidateCard(c, idx, rc) {
  return html`<article class="card candidate lvl-${c.support_level} stack-sm">
    <div class="cand-head">
      <div><div class="small muted">#${idx + 1}</div><h3>${c.name}</h3><p class="small muted">${c.description}</p></div>
      <div class="stack-sm">${supportMeter(c.support_level)}
        <div class="row">${c.has_device_evidence ? html`<span class="badge badge-dot b-observed">Device evidence</span>` : ""}
          ${c.has_test_evidence ? html`<span class="badge badge-dot b-observed">Hands-on test</span>` : ""}
          ${!c.has_device_evidence && !c.has_test_evidence ? html`<span class="badge badge-dot b-reported">Based on your answers only</span>` : ""}
          ${c.requires_confirmation ? html`<span class="badge b-warn">Needs technician confirmation</span>` : ""}
          ${c.conflicting ? html`<span class="badge b-danger">Conflicting evidence</span>` : ""}</div>
        <div class="small muted" title="Sum of documented rule weights. Not a probability.">Ranking score ${c.score}</div></div>
    </div>
    ${repairCostChip(rc)}
    ${c.ranking_basis ? html`<p class="small">${c.ranking_basis}</p>` : ""}
    ${c.suggested_tests?.length ? html`<p class="small"><strong>To confirm:</strong> ${c.suggested_tests.map((t) => t.replaceAll("_", " ")).join(", ")}
      <a href="#/s/${CURRENT_SESSION.id}/tests">(run tests)</a></p>` : ""}
    <details ${idx === 0 ? "open" : ""}><summary>Why (${c.supporting.length} for${c.contradicting.length ? `, ${c.contradicting.length} against` : ""})</summary>
      <div class="details-body stack-sm">
        ${c.supporting.length ? html`<h4>Supporting evidence</h4>${c.supporting.map(evidenceItem)}` : ""}
        ${c.contradicting.length ? html`<h4>Evidence against</h4>${c.contradicting.map(evidenceItem)}` : ""}
        ${c.missing_evidence.length ? html`<h4>Still unknown</h4><ul class="bullets small">${c.missing_evidence.map((m) =>
          html`<li>${m.title} <span class="muted">(${m.reason})</span></li>`)}</ul>` : ""}
      </div></details>
    ${c.self_help.length ? html`<details><summary>Things you can try</summary><div class="details-body"><ul class="bullets small">${c.self_help.map((x) => html`<li>${x}</li>`)}</ul></div></details>` : ""}
  </article>`;
}

// Set by the views that render diagnosisBody so links can point at the right session.
export const CURRENT_SESSION = { id: "" };

export function diagnosisBody(d) {
  const safety = d.notes.filter((n) => n.severity === "safety");
  const info = d.notes.filter((n) => n.severity !== "safety");
  const shown = d.candidates.filter((c) => c.score > 0);
  const ruledDown = d.candidates.filter((c) => c.score <= 0);
  const outcomeKind = { candidates_found: "info", insufficient_evidence: "warn", no_fault_indicated: "ok" }[d.outcome];
  const costs = d.repair_costs || {};
  return html`
    ${safety.map((n) => html`${callout("danger", "Safety", n.text)}<br>`)}
    ${callout(outcomeKind, cap(d.outcome.replaceAll("_", " ")), d.outcome_summary)}
    ${d.recommended_next_step && d.recommended_next_step.kind !== "safety" ? html`<br><div class="card card-tight row-between">
      <div><div class="eyebrow">Most useful next step</div><strong>${d.recommended_next_step.text}</strong></div>
      ${d.recommended_next_step.kind === "test" || d.recommended_next_step.kind === "question"
        ? html`<a class="btn btn-sm no-print" href="#/s/${CURRENT_SESSION.id}/${d.recommended_next_step.kind === "test" ? "tests" : "questions"}">Go</a>` : ""}</div>` : ""}
    ${d.remaining_uncertainty ? html`<p class="small"><strong>Remaining uncertainty:</strong> ${d.remaining_uncertainty}</p>` : html`<br>`}
    <div class="grid grid-2">
      <div class="stack">${shown.length ? shown.map((c, i) => candidateCard(c, i, costs[c.fault_id])) : html`<div class="card empty">No fault has supporting evidence yet.</div>`}
        ${ruledDown.length ? html`<details class="card card-tight"><summary>Considered but not supported (${ruledDown.length})</summary>
          <div class="details-body stack-sm">${ruledDown.map((c) => html`<div class="panel"><strong>${c.name}</strong> <span class="small muted">score ${c.score}</span>
          ${c.contradicting.map(evidenceItem)}</div>`)}</div></details>` : ""}</div>
      <aside class="stack">
        <div class="card stack-sm"><h3>Recommended next steps</h3><ol class="bullets">${d.next_actions.map((a) => html`<li>${a}</li>`)}</ol></div>
        ${info.length ? html`<div class="card stack-sm"><h3>Notes</h3>${info.map((n) => callout("info", "", n.text))}</div>` : ""}
        ${d.close_call ? callout("warn", "Too close to call", "The top two explanations are supported almost equally. A technician should confirm before you pay for parts.") : ""}
        <div class="card stack-sm"><h3>How to read this</h3><p class="small">${d.score_interpretation}</p>
          <p class="small muted">Evidence used: ${d.evidence_counts.device_facts} device readings, ${d.evidence_counts.customer_answers} answers,
            ${d.evidence_counts.test_results ?? 0} hands-on tests, ${d.evidence_counts.rules_fired} rules. Knowledge base ${d.knowledge_version}.</p></div>
        ${d.unavailable_checks.length ? html`<div class="card stack-sm"><h3>Unavailable checks</h3><ul class="bullets small">${d.unavailable_checks.map((u) => html`<li>${u.title}: ${humanize(u.status)}</li>`)}</ul></div>` : ""}
        ${d.ml ? html`<div class="card stack-sm"><h3>Experimental model opinion</h3>
          ${callout("warn", d.ml.status === "validated" ? "Validated model" : "Not validated", d.ml.note)}
          ${d.ml.top ? html`<ul class="clean small">${d.ml.top.map((t) => html`<li>${humanize(t.fault_id)}: ${d.ml.score_label} ${t.score}</li>`)}</ul>` : ""}
          <p class="small muted">Not used in the ranking above.</p></div>` : ""}
      </aside>
    </div>`;
}

export async function diagnosisView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  let d;
  CURRENT_SESSION.id = id;
  try { d = await sapi.diagnosis(); } catch (err) { mount(ctx.el, errorBox(err)); return; }
  mount(ctx.el, html`
    ${stepper(id, "diagnosis", session)}
    <div class="page-head row-between"><div><h1>Likely causes</h1>
      <p>Ranked by how well the evidence supports each one. This is not proof that a part is faulty.</p></div>
      ${nextLink(`#/s/${id}/costs`, "Continue")}</div>
    ${diagnosisBody(d)}
    <div class="row no-print"><a class="btn" href="#/s/${id}/tests">Review tests</a><a class="btn" href="#/s/${id}/questions">Review answers</a>${nextLink(`#/s/${id}/costs`, "Continue")}</div>`);
}
