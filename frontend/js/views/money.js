import { api } from "../api.js";
import { html, mount, callout, errorBox, money, moneyRange, valueSourceBadge, humanize, dateTime, formData, numOrNull, $, toast, cap } from "../ui.js";
import { loadSession, stepper, nextLink } from "./common.js";

const QUOTE_POS = { below_range: ["b-warn", "Below official reference"], within_range: ["b-ok", "Within official reference"],
  above_range: ["b-danger", "Above official reference"], below_part_price: ["b-danger", "Below the part price alone"],
  not_comparable: ["b-neutral", "Not comparable"] };
const MATCH = { exact_model_code: ["b-ok", "Exact model"], exact_model_name: ["b-ok", "Matched by model name"],
  regional_variant: ["b-warn", "Approximation: regional variant"], none: ["b-neutral", "No model match"] };
const FRESH = { fresh: ["b-ok", "Checked"], aging: ["b-warn", "Checked"], stale: ["b-danger", "Out of date: checked"] };
const SCOPE = { parts_and_labour: "Official part + labour", parts_only: "Official part price only",
  all_inclusive: "All-inclusive", labour_only: "Labour only", inspection_fee: "Official inspection fee" };

function regionCurrency(cfg, region) {
  return cfg.regions.find((r) => r.region === region)?.currency || "INR";
}

function shortDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function evidenceTable(e) {
  return html`<div class="table-wrap"><table class="table"><thead><tr><th>Item</th><th class="num">Amount</th><th>Source</th></tr></thead><tbody>
    ${e.evidence.map((x) => html`<tr><td>${x.label}<div class="small muted">Source text: <code>${x.raw_label}</code> = <code>${x.raw_value}</code>
        ${x.model_code ? html` &middot; ${x.model_name} (${x.model_code})` : ""}</div></td>
      <td class="num money">${money(x.amount, e.currency)}</td>
      <td class="small"><a href="${x.source_url}" target="_blank" rel="noopener noreferrer">${x.organization}</a>
        <div class="muted">Retrieved ${shortDate(x.retrieved_at)}${x.published_at ? ` · page dated ${x.published_at}` : ""}</div></td></tr>`)}
    </tbody></table></div>`;
}

function officialLinks(e) {
  return e.official_links.length ? html`<p class="small">Check the official source yourself:
    ${e.official_links.map((l, i) => html`${i ? ", " : ""}<a href="${l.url}" target="_blank" rel="noopener noreferrer">${l.label}</a>`)}</p>` : "";
}

function estimateCard(e, faultsById) {
  const forLine = e.for_faults.length ? html`<p class="small muted">For: ${e.for_faults.map((f) => faultsById[f] || humanize(f)).join(", ")}
    ${e.fault_support ? html` &middot; ${cap(e.fault_support)} diagnostic support` : ""}</p>` : "";
  if (e.status === "insufficient_pricing_data") {
    return html`<article class="card stack-sm">
      <div class="row-between"><h3>${e.repair_name}</h3><span class="badge b-neutral">No reliable price</span></div>${forLine}
      <p>We don't have a source-backed price for this repair on your phone, so we won't guess one.</p>
      ${e.notes.length ? html`<ul class="bullets small">${e.notes.map((n) => html`<li>${n}</li>`)}</ul>` : ""}
      ${officialLinks(e)}
      <p class="small muted">If a shop gives you a quote, add it below to keep it in your report and decision.</p>
    </article>`;
  }
  const demo = e.data_kind === "demo_fixture";
  const benchmark = e.data_kind === "benchmark";
  const fresh = FRESH[e.freshness] || ["b-neutral", "Checked"];
  return html`<article class="card stack-sm">
    ${demo ? callout("mock", "DEMO price - not real", "Development data only. Never use this for a real decision.") : ""}
    <div class="row-between"><h3>${e.repair_name}</h3>
      <span class="badge badge-dot ${demo ? "b-mock" : benchmark ? "b-warn" : "b-estimate"}">${demo ? "Demo data" : benchmark ? "Market benchmark" : SCOPE[e.scope] || "Reference"}</span></div>
    ${forLine}
    <div class="money-lg">${moneyRange(e.low, e.high, e.currency)}</div>
    <div class="small">${benchmark ? html`Typical repair reference range based on market repair references.`
      : e.scope === "inspection_fee" ? html`Inspection charge only, before tax. Not a repair price.`
      : e.status === "parts_only" ? html`<strong>Part price only</strong>: labour and tax not included, so a repair will cost more.`
      : html`${e.tax === "included" ? "Including tax." : e.tax === "excluded" ? "Before tax (GST/VAT extra)." : "May not include tax (the source doesn't say)."}
          ${e.status === "estimated" && !demo ? (e.scope === "parts_and_labour" ? "The manufacturer's own repair estimate, parts and labour." : "Based on the manufacturer's official prices.") : ""}`}</div>
    <div class="row">
      ${e.matched_model ? html`<span class="badge ${MATCH[e.model_match][0]}">${MATCH[e.model_match][1]}: ${e.matched_model}</span>` : ""}
      ${e.last_verified_at ? html`<span class="badge ${fresh[0]}">${fresh[1]} ${shortDate(e.last_verified_at)}</span>` : ""}
    </div>
    ${e.notes.length ? html`<ul class="bullets small">${e.notes.map((n) => html`<li>${n}</li>`)}</ul>` : ""}
    ${e.exclusions.length ? html`<p class="small"><strong>Not included:</strong> ${e.exclusions.join(", ")}.</p>` : ""}
    ${e.evidence.length ? html`<details><summary class="small">Where this price comes from</summary><div class="details-body stack-sm">
      ${evidenceTable(e)}
      <ul class="bullets small">${e.limitations.map((x) => html`<li>${x}</li>`)}</ul></div></details>` : ""}
    ${officialLinks(e)}
    <details><summary class="small">Why a shop's quote may differ</summary><div class="details-body small">${e.why_quotes_differ}</div></details>
  </article>`;
}

export function repairBody(rep, faultsById) {
  const usable = rep.estimates.filter((e) => e.usable_for_decision);
  const policyNotes = [...new Set(rep.estimates.flatMap((e) => e.policy_notes || []))];
  return html`
    ${policyNotes.length ? html`<details class="card card-tight no-print"><summary class="small">Operator note (development only): source usage review pending</summary>
      <div class="details-body small">${policyNotes.map((n) => html`<p>${n}</p>`)}</div></details><br>` : ""}
    ${rep.demo_data_in_use ? html`${callout("mock", "Demo pricing is switched on", "Some prices below are made-up development data and are labelled DEMO.")}<br>` : ""}
    ${rep.estimates.length ? html`<div class="grid grid-2">${rep.estimates.map((e) => estimateCard(e, faultsById))}</div>`
      : html`<div class="card empty">No repair is suggested by the current diagnosis.</div>`}
    ${rep.overall_low !== null && usable.length > 1 ? html`<br><div class="card"><div class="row-between"><div><h3>Range across the likely repairs</h3>
      <p class="small muted">${rep.diagnostic_uncertainty}</p></div>
      <div class="money-lg">${moneyRange(rep.overall_low, rep.overall_high, rep.overall_currency)}</div></div></div>`
      : html`<br>${callout("info", "", rep.diagnostic_uncertainty)}`}
    ${rep.no_paid_repair_suggested.length ? html`<br>${callout("ok", "Try free fixes first",
      `${rep.no_paid_repair_suggested.join(", ")}: these often don't need a paid repair. See the self-help steps in the diagnosis.`)}` : ""}`;
}

export async function repairView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  const [cfg, catalog] = await Promise.all([api.config(), api.faults()]);
  const faultsById = Object.fromEntries(catalog.faults.map((f) => [f.id, f.name]));
  const currency = regionCurrency(cfg, session.device.region);
  let rep = await sapi.repair();
  draw();

  function draw() {
    mount(ctx.el, html`
      ${stepper(id, "repair", session)}
      <div class="page-head row-between"><div><h1>Repair cost estimates</h1>
        <p>Prices for the repairs that match the likely causes on ${session.device.manufacturer || ""} ${session.device.model || "your phone"},
          taken from manufacturers' published price lists where available. Each price shows its source and date.
          If a repair shop has quoted you, add it below to compare.</p></div>
        <div class="stack-sm">${nextLink(`#/s/${id}/decision`, "Compare options")}
          <a class="btn" href="#/s/${id}/value">Check resale value (optional)</a></div></div>
      ${repairBody(rep, faultsById)}
      <br>
      <section class="card stack">
        <h2>Your quotes</h2>
        ${rep.quotes.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Repair</th><th class="num">Quote</th><th>Compared with reference</th><th></th></tr></thead><tbody>
          ${rep.quotes.map((q, i) => html`<tr><td>${humanize(q.repair_type)} ${valueSourceBadge("customer_quote")}</td>
            <td class="num money">${money(q.amount, q.currency)}</td>
            <td><span class="badge ${QUOTE_POS[q.position][0]}">${QUOTE_POS[q.position][1]}</span><div class="small muted">${q.explanation}</div></td>
            <td><button class="btn btn-sm btn-ghost" data-action="del-quote" data-i="${i}" aria-label="Delete quote">Remove</button></td></tr>`)}
          </tbody></table></div>` : html`<p class="small muted">No quotes yet.</p>`}
        <form id="quote-form" class="form-grid" novalidate>
          <div class="field"><label for="q-type">Repair</label><select id="q-type" name="repair_type">
            ${catalog.repair_types.map((r) => html`<option value="${r.id}">${r.name}</option>`)}</select></div>
          <div class="field"><label for="q-amount">Amount</label><input id="q-amount" name="amount" type="number" min="0" step="0.01" required></div>
          <div class="field"><label for="q-cur">Currency</label><input id="q-cur" name="currency" type="text" maxlength="3" value="${currency}" pattern="[A-Z]{3}" autocapitalize="characters"></div>
          <div class="field"><label for="q-tax">Does it include tax?</label><select id="q-tax" name="includes_tax">
            <option value="">Not sure</option><option value="yes">Yes</option><option value="no">No</option></select></div>
          <div class="field"><span class="label">&nbsp;</span><button class="btn btn-primary" type="submit">Add quote</button></div>
        </form>
        <div id="quote-error" role="alert"></div>
      </section>`);
    $("#quote-form", ctx.el).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = formData(ev.target);
      const amount = numOrNull(f.amount);
      if (amount === null || amount < 0) { mount($("#quote-error", ctx.el), callout("warn", "Enter the quoted amount as a number")); return; }
      ev.submitter.disabled = true;
      try {
        rep = await sapi.addQuote({ repair_type: f.repair_type, amount, currency: f.currency.trim().toUpperCase(),
          includes_tax: f.includes_tax === "" ? null : f.includes_tax === "yes" });
        draw();
        toast("Quote added.");
      } catch (err) { mount($("#quote-error", ctx.el), errorBox(err)); ev.submitter.disabled = false; }
    });
  }

  ctx.el.addEventListener("click", async (ev) => {
    const b = ev.target.closest('[data-action="del-quote"]');
    if (!b) return;
    try { rep = await sapi.deleteQuote(Number(b.dataset.i)); draw(); } catch (err) { toast(err.message); }
  });
}

const GRADES = [
  ["excellent", "Excellent", "Like new, no visible wear, fully working."],
  ["good", "Good", "Light signs of use, fully working."],
  ["fair", "Fair", "Visible scratches or dents, fully working."],
  ["poor", "Poor", "Heavy wear or cracked back, still working."],
  ["faulty", "Faulty", "At least one function does not work properly."],
];
const REPAIRABLE = ["battery_replacement", "charging_port_replacement", "screen_replacement", "camera_module_replacement",
  "audio_component_replacement", "liquid_damage_service", "software_reset"];

export function valuationResult(v) {
  if (!v) return "";
  if (!v.ok) {
    return callout("warn", "The last valuation attempt failed", `Code: ${v.error_code}. No resale values were stored, and none are made up.`);
  }
  const r = v.response;
  return html`<div class="card stack">
    ${v.source === "flipvalue_mock" ? html`${callout("mock", "MOCK DATA - not a real valuation",
      "These numbers come from FixMyTech's development mock of FlipValue. They are not market estimates.")}` : ""}
    <div class="row-between"><h3>Resale estimate</h3>${valueSourceBadge(v.source)}</div>
    <div class="grid grid-2">
      <div class="panel"><div class="small muted">As-is (current condition)</div>
        <div class="money-lg">${money(r.as_is.value, r.currency)}</div>
        ${r.as_is.low !== null ? html`<div class="small muted">Range ${moneyRange(r.as_is.low, r.as_is.high, r.currency)}</div>` : ""}</div>
      <div class="panel"><div class="small muted">After ${humanize(v.request.repair_scenario?.repair_type || "repair")}</div>
        ${r.after_repair ? html`<div class="money-lg">${money(r.after_repair.value, r.currency)}</div>
          ${r.after_repair.low !== null ? html`<div class="small muted">Range ${moneyRange(r.after_repair.low, r.after_repair.high, r.currency)}</div>` : ""}`
          : html`<div class="muted">Not provided</div>`}</div>
    </div>
    <dl class="kv small"><dt>Method</dt><dd>${r.method}</dd><dt>Data as of</dt><dd>${dateTime(r.data_timestamp)}</dd>
      <dt>Requested</dt><dd>${dateTime(v.requested_at)}</dd><dt>Valuation ID</dt><dd><code>${r.valuation_id}</code></dd></dl>
    ${r.warnings.length ? html`<ul class="bullets small">${r.warnings.map((w) => html`<li>${w}</li>`)}</ul>` : ""}
  </div>`;
}

export async function valueView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  let state = await sapi.valuation();
  draw();

  function draw(errorHtml) {
    const sug = state.suggested_condition;
    const configured = state.configured;
    const prev = state.stored?.request;
    mount(ctx.el, html`
      ${stepper(id, "value", session)}
      <div class="page-head row-between"><div><h1>Resale value <span class="step-opt">(optional)</span></h1>
        <p>FlipValue, an independent valuation service, estimates what your phone could sell for as it is and after a repair.
          You can skip this step and enter resale values manually in the decision step, or proceed without them.</p></div>
        <div class="stack-sm">${nextLink(`#/s/${id}/decision`, "Compare options")}
          <a class="btn" href="#/s/${id}/decision">Skip to decision</a></div></div>
      ${!configured && !state.development_mock ? html`${callout("info", "A FlipValue API key is needed for resale values",
        html`To see resale values, <a href="#/settings">add your FlipValue API key in Settings</a>. Nothing is estimated without it. You can also skip this step or enter values yourself in the decision step.`)}<br>` : ""}
      ${state.development_mock ? html`${callout("mock", "Development mode: MOCK FlipValue",
        "This server is connected to a mock valuation service. Its numbers are made up for testing.")}<br>` : ""}
      <div class="grid grid-2">
        <form class="card stack" id="val-form">
          <h3>Phone details for valuation</h3>
          ${!session.device.manufacturer || !session.device.model ? callout("warn", "Manufacturer and model needed",
            html`Valuation needs the manufacturer and model. <a href="#/s/${id}/report">Edit device details</a> in the report step.`) : html`<p class="small muted">${session.device.manufacturer} ${session.device.model} &middot; ${session.device.region}</p>`}
          <fieldset class="stack-sm"><legend>Condition</legend>
            <p class="small muted">${sug.explanation}</p>
            <div class="options">${GRADES.map(([v, label, desc]) => html`<label class="check"><input type="radio" name="condition_grade" value="${v}"
              ${(prev?.condition?.grade || sug.grade || "good") === v ? "checked" : ""}><span><strong>${label}</strong> <span class="small muted">${desc}</span></span></label>`)}</div>
          </fieldset>
          <div class="form-grid">
            <div class="field"><label for="v-repair">Value after this repair</label><select id="v-repair" name="repair_type">
              <option value="">None</option>${REPAIRABLE.map((r) => html`<option value="${r}" ${(prev?.repair_scenario?.repair_type || sug.repair_type) === r ? "selected" : ""}>${humanize(r)}</option>`)}</select></div>
            <div class="field"><label for="v-storage">Storage (GB)</label><input id="v-storage" name="storage_gb" type="number" min="1" value="${session.device.storage_gb ?? ""}"></div>
            <div class="field"><label for="v-age">Age (months)</label><input id="v-age" name="age_months" type="number" min="0" value="${session.device.age_months ?? ""}"></div>
          </div>
          <p class="small muted">Sent to FlipValue: manufacturer, model, storage, age, market, condition grade, likely fault categories and the repair type.
            No serial numbers or personal data.</p>
          <div id="val-error">${errorHtml || ""}</div>
          <div class="row"><button class="btn btn-primary" type="submit" ${configured || state.development_mock ? "" : "disabled"}>Get resale estimate</button></div>
        </form>
        <div class="stack">${valuationResult(state.stored)}
          ${!state.stored ? html`<div class="card empty">No valuation yet.</div>` : ""}</div>
      </div>`);
    $("#val-form", ctx.el).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = formData(ev.target);
      ev.submitter.disabled = true;
      mount($("#val-error", ctx.el), html`<div class="loading"><div class="spinner"></div>Contacting FlipValue...</div>`);
      try {
        const stored = await sapi.requestValuation({ condition_grade: f.condition_grade, repair_type: f.repair_type || null,
          storage_gb: numOrNull(f.storage_gb), age_months: numOrNull(f.age_months) });
        state = { ...state, stored };
        draw();
      } catch (err) {
        state = await sapi.valuation();
        draw(html`${err.code === "valuation_not_configured" ? callout("info", "A FlipValue API key is needed", html`<a href="#/settings">Add it in Settings</a>.`) : errorBox(err)}<p class="small muted">Your diagnosis and repair estimates are unaffected. You can enter resale values
          manually in the next step.</p>`);
      }
    });
  }
}

function rangeBar(sc, lo, hi) {
  const span = hi - lo || 1;
  const left = ((sc.low - lo) / span) * 100;
  const width = Math.max(((sc.high - sc.low) / span) * 100, 0.8);
  const mid = ((sc.mid - lo) / span) * 100;
  return html`<div class="range-bar" aria-hidden="true" data-left="${left}" data-width="${width}" data-mid="${mid}">
    <div class="fill"></div><div class="mid"></div></div>`;
}

function comparisonCard(cmp, scenarios, currency, horizon) {
  const scs = cmp.options.map((o) => scenarios.find((s) => s.id === o));
  const avail = scs.filter((s) => s.available);
  const lo = Math.min(...avail.map((s) => s.low));
  const hi = Math.max(...avail.map((s) => s.high));
  const title = cmp.basis === "horizon_net_cost" ? `Keep vs replace: net cost over ${horizon} months` : "Cash now: sell as-is vs repair then sell";
  const hint = cmp.basis === "horizon_net_cost" ? "Lower is better. Negative means you come out ahead." : "Higher is better.";
  const verdictBadge = { clear: ["b-ok", "Clear difference"], too_close: ["b-warn", "Too close to call"], uncertain: ["b-warn", "Depends on assumptions"], incomplete: ["b-neutral", "Incomplete"] }[cmp.verdict];
  return html`<article class="card stack">
    <div class="row-between"><h3>${title}</h3><span class="badge ${verdictBadge[0]}">${verdictBadge[1]}</span></div>
    <p class="small muted">${hint}</p>
    ${scs.map((sc) => html`<div class="scenario ${cmp.winner === sc.id && cmp.verdict === "clear" ? "winner" : ""}">
      <div class="row-between"><strong>${sc.label}</strong>
        ${sc.available ? html`<span class="money">${money(sc.mid, currency)}</span>` : html`<span class="small muted">Needs: ${sc.missing.join(", ")}</span>`}</div>
      ${sc.available ? html`${rangeBar(sc, lo, hi)}<div class="small muted">Plausible range ${moneyRange(sc.low, sc.high, currency)}</div>
        <details><summary class="small">How this is calculated</summary><div class="details-body small">
          <p><code>${sc.formula}</code></p><dl class="kv">${Object.entries(sc.breakdown).map(([k, v]) => html`<dt>${humanize(k)}</dt><dd>${money(v, currency)}</dd>`)}</dl></div></details>` : ""}
    </div>`)}
    <p>${cmp.explanation}</p>
    ${cmp.drivers.length ? html`<p class="small muted">The result could flip if these change: ${cmp.drivers.join(", ")}.</p>` : ""}
  </article>`;
}

export function decisionBody(result, request) {
  const rec = result.scenarios.find((s) => s.id === result.recommendation);
  return html`
    ${result.caveats.filter((c) => c.includes("MOCK")).map((c) => html`${callout("mock", "Mock valuation data", c)}<br>`)}
    <div class="card stack-sm">
      <div class="small muted">Based on your goal: ${{ lowest_cost: "lowest cost over time", max_cash_now: "most cash now", keep_device: "keep my phone" }[result.objective]}</div>
      <div class="verdict">${rec ? `Suggested: ${rec.label}` : "No single option clearly comes out ahead"}</div>
      <p>${result.recommendation_text}</p>
    </div><br>
    ${(result.input_warnings || []).map((w) => html`${callout("warn", "Check your inputs", w)}<br>`)}
    ${(result.plain_language || []).length ? html`<div class="card stack-sm"><h3>The calculation in plain words</h3>
      <ul class="bullets">${result.plain_language.map((x) => html`<li>${x}</li>`)}</ul>
      <p class="small muted">Mid-range values are used here; the comparisons below show the full plausible ranges.</p></div><br>` : ""}
    <div class="grid grid-2">${result.comparisons.map((c) => comparisonCard(c, result.scenarios, result.currency, request?.horizon_months || 24))}</div><br>
    <div class="grid grid-2">
      <div class="card stack-sm"><h3>Assumptions</h3><ul class="bullets small">${result.assumptions.map((a) => html`<li>${a}</li>`)}</ul></div>
      <div class="card stack-sm"><h3>Keep in mind</h3><ul class="bullets small">${result.caveats.map((a) => html`<li>${a}</li>`)}</ul>
        ${result.missing_inputs.length ? html`<p class="small">Missing inputs: ${result.missing_inputs.join(", ")}.</p>` : ""}</div>
    </div>`;
}

export function applyRangeBars(root) {
  root.querySelectorAll(".range-bar").forEach((bar) => {
    const fill = bar.querySelector(".fill");
    const mid = bar.querySelector(".mid");
    fill.style.left = `${bar.dataset.left}%`;
    fill.style.width = `${bar.dataset.width}%`;
    mid.style.left = `calc(${bar.dataset.mid}% - 1px)`;
  });
}

export async function decisionView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  const [cfg, rep, val, prior] = await Promise.all([api.config(), sapi.repair(), sapi.valuation(), sapi.report().then((r) => r.decision).catch(() => null)]);
  const currency = regionCurrency(cfg, session.device.region);
  const fvOk = val.stored?.ok;
  const req = prior?.request || {};
  const estimates = rep.estimates.filter((e) => e.usable_for_decision && e.repair_type !== "professional_diagnosis");

  mount(ctx.el, html`
    ${stepper(id, "decision", session)}
    <div class="page-head"><h1>Repair, sell or replace?</h1>
      <p>A transparent comparison on a consistent time basis. Every assumption is editable and shown with the result.</p></div>
    <div class="grid grid-2">
      <form class="card stack" id="dec-form">
        <fieldset class="stack-sm"><legend>What matters most to you?</legend><div class="options">
          ${[["lowest_cost", "Lowest cost over time", "Compare keeping a repaired phone with buying a replacement."],
             ["max_cash_now", "Most cash now", "Compare selling as-is with repairing and then selling."],
             ["keep_device", "I want to keep this phone", "Show the cost of repairing, with replacement for reference."]]
            .map(([v, l, d]) => html`<label class="check"><input type="radio" name="objective" value="${v}" ${(req.objective || "lowest_cost") === v ? "checked" : ""}>
              <span><strong>${l}</strong><br><span class="small muted">${d}</span></span></label>`)}</div></fieldset>
        <div class="field"><label for="d-repair">Repair cost</label><select id="d-repair" name="repair_source">
          <option value="">Not included</option>
          ${estimates.map((e) => html`<option value="estimate:${e.repair_type}" ${req.repair_source === `estimate:${e.repair_type}` ? "selected" : ""}>${e.repair_name}: ${moneyRange(e.low, e.high, e.currency)} (reference estimate)</option>`)}
          ${rep.quotes.map((q, i) => html`<option value="quote:${i}" ${req.repair_source === `quote:${i}` ? "selected" : ""}>${humanize(q.repair_type)}: ${money(q.amount, q.currency)} (your quote)</option>`)}
        </select></div>
        <fieldset class="stack-sm"><legend>Resale values</legend>
          <div class="options">
            <label class="check"><input type="radio" name="value_source" value="flipvalue" ${fvOk ? "" : "disabled"} ${fvOk && req.value_source !== "manual" ? "checked" : ""}>
              <span>Use the FlipValue estimate ${fvOk ? valueSourceBadge(val.stored.source) : html`<span class="small muted">(not available)</span>`}</span></label>
            <label class="check"><input type="radio" name="value_source" value="manual" ${!fvOk || req.value_source === "manual" ? "checked" : ""}>
              <span>Enter values myself ${valueSourceBadge("customer_provided")}</span></label>
          </div>
          <div class="form-grid" id="manual-values">
            <div class="field"><label for="m-asis-lo">As-is value, low</label><input id="m-asis-lo" name="asis_low" type="number" min="0" value="${req.manual_values?.as_is?.low ?? ""}"></div>
            <div class="field"><label for="m-asis-hi">As-is value, high</label><input id="m-asis-hi" name="asis_high" type="number" min="0" value="${req.manual_values?.as_is?.high ?? ""}"></div>
            <div class="field"><label for="m-rep-lo">Repaired value, low</label><input id="m-rep-lo" name="rep_low" type="number" min="0" value="${req.manual_values?.repaired?.low ?? ""}"></div>
            <div class="field"><label for="m-rep-hi">Repaired value, high</label><input id="m-rep-hi" name="rep_high" type="number" min="0" value="${req.manual_values?.repaired?.high ?? ""}"></div>
          </div>
        </fieldset>
        <div class="form-grid">
          <div class="field"><label for="d-repl">Replacement phone price (${currency})</label><input id="d-repl" name="replacement_price" type="number" min="0" value="${req.replacement_price ?? ""}">
            <span class="hint">What you'd pay for the phone you'd buy instead.</span></div>
          <div class="field"><label for="d-h">Time horizon (months)</label><input id="d-h" name="horizon_months" type="number" min="1" max="72" value="${req.horizon_months ?? 24}"></div>
          <div class="field"><label for="d-fee">Selling fee (%)</label><input id="d-fee" name="transaction_fee_pct" type="number" min="0" max="50" step="0.5" value="${req.transaction_fee_pct ?? 0}"></div>
          <div class="field"><label for="d-extra">Expected extra repairs (${currency})</label><input id="d-extra" name="expected_extra_repairs" type="number" min="0" value="${req.expected_extra_repairs ?? 0}"></div>
          <div class="field"><label for="d-dold">Depreciation, current phone (%/yr)</label><input id="d-dold" name="old_device_annual_depreciation_pct" type="number" min="0" max="90" value="${req.old_device_annual_depreciation_pct ?? 20}"></div>
          <div class="field"><label for="d-dnew">Depreciation, replacement (%/yr)</label><input id="d-dnew" name="new_device_annual_depreciation_pct" type="number" min="0" max="90" value="${req.new_device_annual_depreciation_pct ?? 30}"></div>
        </div>
        <div id="dec-error"></div>
        <div><button class="btn btn-primary btn-lg" type="submit">Compare options</button></div>
      </form>
      <div id="dec-result" class="stack">${prior ? decisionBody(prior.result, prior.request) : html`<div class="card empty">Fill in what you know and compare. Missing inputs are listed, never guessed.</div>`}</div>
    </div>
    <div class="row no-print"><br>${nextLink(`#/s/${id}/report`, "View full report")}</div>`);
  applyRangeBars(ctx.el);

  $("#dec-form", ctx.el).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = formData(ev.target);
    const range = (lo, hi) => {
      const l = numOrNull(lo); const h = numOrNull(hi);
      if (l === null && h === null) return null;
      return { low: l ?? h, high: h ?? l };
    };
    const body = {
      objective: f.objective, repair_source: f.repair_source || null, value_source: f.value_source || "manual",
      replacement_price: numOrNull(f.replacement_price), horizon_months: numOrNull(f.horizon_months) ?? 24,
      transaction_fee_pct: numOrNull(f.transaction_fee_pct) ?? 0, expected_extra_repairs: numOrNull(f.expected_extra_repairs) ?? 0,
      old_device_annual_depreciation_pct: numOrNull(f.old_device_annual_depreciation_pct) ?? 20,
      new_device_annual_depreciation_pct: numOrNull(f.new_device_annual_depreciation_pct) ?? 30,
      currency,
    };
    if (body.value_source === "manual") {
      body.manual_values = { as_is: range(f.asis_low, f.asis_high), repaired: range(f.rep_low, f.rep_high) };
    }
    const out = $("#dec-result", ctx.el);
    try {
      const d = await sapi.decision(body);
      mount($("#dec-error", ctx.el), "");
      mount(out, decisionBody(d.result, d.request));
      applyRangeBars(out);
      out.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      mount($("#dec-error", ctx.el), errorBox(err));
    }
  });
}
