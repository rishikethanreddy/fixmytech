import { api } from "../api.js";
import { history } from "../store.js";
import { html, mount, loading, callout, errorBox, money, moneyRange, valueSourceBadge, humanize, dateTime, formData, numOrNull, $, toast, cap } from "../ui.js";
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

// ---------------------------------------------------------------------------- one step, three tabs
// Repair costs, resale value and the decision share one step. Each tab re-renders when it is opened so it always
// reflects what was changed in the others (a new quote, a new valuation). FlipValue data appears in every tab and
// is never filled in: when FlipValue has nothing for this phone, the tab says so.

const TABS = [["repair", "Repair costs"], ["value", "Resale value"], ["decision", "Decision"]];
const PANELS = { repair: repairPanel, value: valuePanel, decision: decisionPanel };

export async function moneyView(ctx, initialTab = "repair") {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, session } = s;
  const asked = ctx.query.get("tab");
  let tab = TABS.some(([k]) => k === asked) ? asked : initialTab;
  let renderId = 0;

  // This step is optional: ask first, unless the customer already said yes or followed a link to a specific tab.
  if (!asked && !history.get(id)?.moneyOptIn && !session.has_decision) {
    const device = `${session.device.manufacturer || ""} ${session.device.model || "your phone"}`.trim();
    mount(ctx.el, html`
      ${stepper(id, "costs", session)}
      <section class="card optin stack">
        <span class="optin-tag">Optional</span>
        <h1>Want to see if it's worth fixing?</h1>
        <p class="lead">Your diagnosis is ready. If you like, we can also work out what a repair would cost, what ${device}
          is worth today, and whether repairing, selling or replacing it makes more sense.</p>
        <div class="optin-grid">
          <div class="optin-item"><strong>Repair costs</strong><span class="small muted">Official prices for the likely repair, and room for a shop's quote.</span></div>
          <div class="optin-item"><strong>Resale value</strong><span class="small muted">What the phone sells for now, its price trend and store prices (from FlipValue).</span></div>
          <div class="optin-item"><strong>Decision</strong><span class="small muted">A plain answer: repair, sell or replace, with the numbers behind it.</span></div>
        </div>
        <p class="small muted">Takes about 2 minutes. You can come back to this later from your report.</p>
        <div class="row">
          <button type="button" class="btn btn-primary btn-lg" data-optin="yes">Yes, show me</button>
          <button type="button" class="btn btn-lg" data-optin="no">No thanks, go to my report</button>
        </div>
      </section>`);
    const choice = await new Promise((resolve) => {
      ctx.el.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-optin]");
        if (b) resolve(b.dataset.optin);
      });
    });
    if (choice === "no") { ctx.navigate(`/s/${id}/report`); return; }
    history.update(id, { moneyOptIn: true });
  }

  mount(ctx.el, html`
    ${stepper(id, "costs", session)}
    <div class="page-head"><h1>Costs, resale value &amp; decision</h1>
      <p>What a repair costs, what ${session.device.manufacturer || ""} ${session.device.model || "your phone"} is worth on the market
        (from FlipValue), and whether repairing, selling or replacing comes out ahead.</p></div>
    <div class="tabs no-print" role="tablist" aria-label="Costs, resale value and decision">
      ${TABS.map(([k, label]) => html`<button type="button" class="tab" role="tab" id="tab-${k}" aria-controls="panel-${k}" data-tab="${k}">${label}</button>`)}
    </div>
    ${TABS.map(([k]) => html`<section class="tab-panel" role="tabpanel" id="panel-${k}" aria-labelledby="tab-${k}" hidden></section>`)}`);

  async function show(k, focusTab = false) {
    tab = k;
    for (const [t] of TABS) {
      const b = $(`#tab-${t}`, ctx.el);
      b.setAttribute("aria-selected", String(t === k));
      b.tabIndex = t === k ? 0 : -1;
      $(`#panel-${t}`, ctx.el).hidden = t !== k;
    }
    if (focusTab) $(`#tab-${k}`, ctx.el).focus();
    // Keep the address in sync without re-running the router.
    window.history.replaceState(null, "", `#/s/${id}/costs?tab=${k}`);
    const el = $(`#panel-${k}`, ctx.el);
    const mine = ++renderId;
    mount(el, loading());
    try {
      const fresh = await s.sapi.get();
      if (mine !== renderId) return;
      await PANELS[k]({ ...s, session: fresh, el, goTab: (t) => { show(t); window.scrollTo({ top: 0, behavior: "smooth" }); } });
    } catch (err) {
      if (mine === renderId) mount(el, errorBox(err));
    }
  }

  const bar = $(".tabs", ctx.el);
  bar.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-tab]");
    if (b && b.dataset.tab !== tab) show(b.dataset.tab);
  });
  bar.addEventListener("keydown", (ev) => {
    const i = TABS.findIndex(([k]) => k === tab);
    const step = { ArrowRight: 1, ArrowLeft: -1 }[ev.key];
    if (step) { ev.preventDefault(); show(TABS[(i + step + TABS.length) % TABS.length][0], true); }
  });
  // Links inside a panel that switch tabs ("Get a resale estimate", "Compare options").
  ctx.el.addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-goto-tab]");
    if (b) { ev.preventDefault(); show(b.dataset.gotoTab); window.scrollTo({ top: 0, behavior: "smooth" }); }
  });
  await show(tab);
}

function tabLink(tab, label, primary = false) {
  return html`<button type="button" class="btn ${primary ? "btn-primary" : ""}" data-goto-tab="${tab}">${label}${primary ? html` &rarr;` : ""}</button>`;
}

// ---------------------------------------------------------------------------- repair costs tab

// FlipValue: how much the repair adds to the resale value, next to what it costs.
function upliftCard(rep, val) {
  const head = html`<div class="row-between"><h3>Is the repair worth it? (FlipValue)</h3>${val.stored?.ok ? valueSourceBadge(val.stored.source) : ""}</div>`;
  if (!val.configured && !val.development_mock) {
    return html`<div class="card stack-sm">${head}<p class="small muted">Add your FlipValue API key in <a href="#/settings">Settings</a>
      to see how much a repair adds to your phone's resale value.</p></div>`;
  }
  const v = val.stored;
  if (!v?.ok) {
    return html`<div class="card stack-sm">${head}<p class="small muted">${v ? "The last FlipValue valuation failed, so there is no value to compare with."
      : "No FlipValue valuation for this phone yet."}</p><div>${tabLink("value", "Get a resale estimate")}</div></div>`;
  }
  const r = v.response;
  const repairType = v.request.repair_scenario?.repair_type;
  if (!r.after_repair || !repairType) {
    return html`<div class="card stack-sm">${head}<p class="small muted">No data: FlipValue did not return an after-repair value for this phone.
      Choose a repair in the Resale value tab to ask for one.</p><div>${tabLink("value", "Open Resale value")}</div></div>`;
  }
  const gain = Number(r.after_repair.value) - Number(r.as_is.value);
  const est = rep.estimates.find((e) => e.repair_type === repairType && e.usable_for_decision && e.currency === r.currency);
  const quote = rep.quotes.find((q) => q.repair_type === repairType && q.currency === r.currency);
  const lo = est ? Number(est.low) : quote ? Number(quote.amount) : null;
  const hi = est ? Number(est.high) : lo;
  const verdict = lo === null ? null : gain >= hi ? ["ok", "The repair adds more value than it costs."]
    : gain <= lo ? ["warn", "The repair costs more than it adds to the resale value."] : ["info", "Roughly break-even: it depends on the final repair price."];
  return html`<div class="card stack-sm">${head}
    <div class="grid grid-2">
      <div class="panel"><div class="small muted">Value added by ${humanize(repairType).toLowerCase()}</div><div class="money-lg">${money(gain, r.currency)}</div>
        <div class="small muted">${money(r.as_is.value, r.currency)} as-is &rarr; ${money(r.after_repair.value, r.currency)} after repair</div></div>
      <div class="panel"><div class="small muted">Repair cost</div>
        ${lo === null ? html`<div class="muted">No price for this repair yet</div><div class="small muted">Add a shop quote below.</div>`
          : html`<div class="money-lg">${moneyRange(lo, hi, r.currency)}</div><div class="small muted">${est ? "Reference estimate" : "Your quote"}</div>`}</div>
    </div>
    ${verdict ? callout(verdict[0], "", verdict[1]) : ""}
  </div>`;
}

async function repairPanel({ sapi, session, el }) {
  const [cfg, catalog, val] = await Promise.all([api.config(), api.faults(), sapi.valuation()]);
  const faultsById = Object.fromEntries(catalog.faults.map((f) => [f.id, f.name]));
  const currency = regionCurrency(cfg, session.device.region);
  let rep = await sapi.repair();
  draw();

  function draw() {
    mount(el, html`
      <div class="page-head row-between"><div><h2>Repair cost estimates</h2>
        <p>Prices for the repairs that match the likely causes on ${session.device.manufacturer || ""} ${session.device.model || "your phone"},
          taken from manufacturers' published price lists where available. Each price shows its source and date.
          If a repair shop has quoted you, add it below to compare.</p></div>
        <div class="stack-sm">${tabLink("value", "Next: resale value", true)}</div></div>
      ${repairBody(rep, faultsById)}
      <br>${upliftCard(rep, val)}<br>
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
      </section>
      <div class="row no-print"><br>${tabLink("value", "Next: resale value", true)}</div>`);
    $("#quote-form", el).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = formData(ev.target);
      const amount = numOrNull(f.amount);
      if (amount === null || amount < 0) { mount($("#quote-error", el), callout("warn", "Enter the quoted amount as a number")); return; }
      ev.submitter.disabled = true;
      try {
        rep = await sapi.addQuote({ repair_type: f.repair_type, amount, currency: f.currency.trim().toUpperCase(),
          includes_tax: f.includes_tax === "" ? null : f.includes_tax === "yes" });
        draw();
        toast("Quote added.");
      } catch (err) { mount($("#quote-error", el), errorBox(err)); ev.submitter.disabled = false; }
    });
  }

  el.addEventListener("click", async (ev) => {
    const b = ev.target.closest('[data-action="del-quote"]');
    if (!b) return;
    try { rep = await sapi.deleteQuote(Number(b.dataset.i)); draw(); } catch (err) { toast(err.message); }
  });
}

// ---------------------------------------------------------------------------- resale value tab

const EVIDENCE = { estimated: ["b-warn", "Modelled estimates"], real: ["b-ok", "Real price data"], observed: ["b-ok", "Real price data"],
  insufficient: ["b-neutral", "Not enough data"] };
const OFFER_GROUPS = [["used", "Used"], ["refurbished", "Refurbished"], ["buyback", "Buyback / trade-in"], ["new", "New"]];
const MONTH_FMT = (m) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, mo - 1, 1).toLocaleDateString(undefined, { month: "short", year: "numeric" });
};

function trendStats(points) {
  const last = points[points.length - 1];
  const yearAgo = points.find((p) => p.month >= `${Number(last.month.slice(0, 4)) - 1}${last.month.slice(4)}`);
  const pct = (a, b) => (b ? Math.round(((a - b) / b) * 100) : null);
  return { last, first: points[0], year: yearAgo && yearAgo !== last ? pct(last.median, yearAgo.median) : null,
    total: pct(last.median, points[0].median) };
}

// A single-quantity line chart (median resale price by month). Estimates are dashed, real observations solid,
// as on FlipValue's own Trends page. Axis labels are HTML so they stay readable at any width.
function trendChart(series, currency) {
  const names = Object.keys(series);
  const months = [...new Set(names.flatMap((n) => series[n].map((p) => p.month)))].sort();
  const values = names.flatMap((n) => series[n].map((p) => p.median));
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  const pad = (hi - lo) * 0.08 || hi * 0.05 || 1;
  lo = Math.max(0, lo - pad); hi += pad;
  const W = 640; const H = 220;
  const x = (m) => (months.length > 1 ? (months.indexOf(m) / (months.length - 1)) * W : W / 2);
  const y = (v) => H - ((v - lo) / (hi - lo)) * H;
  const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.month).toFixed(1)},${y(p.median).toFixed(1)}`).join(" ");
  const main = series[names[0]];
  const end = main[main.length - 1];
  const est = (n) => /estimate|model/i.test(n);
  return html`<div class="trend-chart" data-months="${months.join(",")}">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Median resale price by month, ${MONTH_FMT(months[0])} to ${MONTH_FMT(months[months.length - 1])}">
      ${[0.25, 0.5, 0.75].map((k) => html`<line class="grid-line" x1="0" x2="${W}" y1="${H * k}" y2="${H * k}" vector-effect="non-scaling-stroke"></line>`)}
      ${names.length === 1 ? html`<path class="area" d="${path(main)} L${x(end.month)},${H} L${x(main[0].month)},${H} Z"></path>` : ""}
      ${names.map((n) => html`<path class="line ${est(n) ? "is-estimate" : ""}" d="${path(series[n])}"></path>`)}
      <line class="cross" x1="0" x2="0" y1="0" y2="${H}" hidden></line>
      <rect class="hit" x="0" y="0" width="${W}" height="${H}"></rect>
    </svg>
    <div class="trend-tip" hidden></div>
  </div>
  <div class="trend-axis" aria-hidden="true"><span>${MONTH_FMT(months[0])}</span><span>Scale ${money(lo, currency)} – ${money(hi, currency)}</span><span>${MONTH_FMT(months[months.length - 1])}</span></div>
  ${names.length > 1 || est(names[0]) ? html`<p class="small muted">${names.map((n) => html`<span class="legend-key ${est(n) ? "is-estimate" : ""}"></span>${est(n) ? "FlipValue modelled estimate" : "Observed sale prices"}&nbsp;&nbsp; `)}</p>` : ""}
  <details><summary class="small">Show as a table</summary><div class="details-body"><div class="table-wrap"><table class="table">
    <thead><tr><th>Month</th>${names.map((n) => html`<th class="num">${est(n) ? "Estimate" : humanize(n)}</th>`)}</tr></thead><tbody>
    ${[...months].reverse().map((m) => html`<tr><td>${MONTH_FMT(m)}</td>${names.map((n) => {
      const p = series[n].find((q) => q.month === m);
      return html`<td class="num money">${p ? money(p.median, currency) : "-"}</td>`;
    })}</tr>`)}</tbody></table></div></div></details>`;
}

// Crosshair + tooltip for the trend chart (attributes and CSSOM only: the page's CSP forbids inline styles).
function wireTrendChart(root, series, currency) {
  const box = $(".trend-chart", root);
  if (!box) return;
  const months = box.dataset.months.split(",");
  const svg = $("svg", box); const cross = $(".cross", box); const tip = $(".trend-tip", box);
  const names = Object.keys(series);
  const move = (ev) => {
    const r = svg.getBoundingClientRect();
    const frac = Math.min(Math.max((ev.clientX - r.left) / r.width, 0), 1);
    const i = Math.round(frac * (months.length - 1));
    const m = months[i];
    const xPos = months.length > 1 ? (i / (months.length - 1)) * 640 : 320;
    cross.setAttribute("x1", xPos); cross.setAttribute("x2", xPos); cross.removeAttribute("hidden");
    const rows = names.map((n) => [n, series[n].find((p) => p.month === m)]).filter(([, p]) => p);
    tip.textContent = `${MONTH_FMT(m)}: ${rows.map(([n, p]) => `${money(p.median, currency)}${names.length > 1 ? ` (${n})` : ""}`).join(", ")}`;
    tip.hidden = false;
    const px = (xPos / 640) * r.width;
    tip.style.left = `${Math.min(Math.max(px, 90), r.width - 90)}px`;
  };
  const hide = () => { cross.setAttribute("hidden", ""); tip.hidden = true; };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerleave", hide);
}

function trendCard(m, currency) {
  const t = m.trend;
  const head = html`<div class="row-between"><h3>Resale price trend</h3><span class="small muted">Condition: ${humanize(m.grade)}</span></div>`;
  if (!t?.available) {
    return html`<article class="card stack-sm">${head}<div class="empty"><strong>No trend data for this phone</strong>
      <p class="small">${t?.reason || "FlipValue has no price history for this phone yet."}</p></div></article>`;
  }
  const name = Object.keys(t.series)[0];
  const st = trendStats(t.series[name]);
  const cur = t.currency || currency;
  const chg = (v) => (v === null ? "-" : `${v > 0 ? "+" : ""}${v}%`);
  return html`<article class="card stack-sm">${head}
    <dl class="kv small"><dt>Latest (${MONTH_FMT(st.last.month)})</dt><dd>${money(st.last.median, cur)}</dd>
      <dt>Change over 12 months</dt><dd>${chg(st.year)}</dd><dt>Since ${MONTH_FMT(st.first.month)}</dt><dd>${chg(st.total)}</dd></dl>
    ${trendChart(t.series, cur)}
  </article>`;
}

function offersCard(m, group) {
  const o = m.offers;
  const head = html`<h3>Store prices for this phone</h3>`;
  if (!o?.available) {
    return html`<article class="card stack-sm">${head}<div class="empty"><strong>No store prices for this phone</strong>
      <p class="small">${o?.reason || "FlipValue has no store, refurbished, used or buyback prices for this phone yet."}</p></div></article>`;
  }
  const rows = o.groups[group] || [];
  const lowest = (g) => (o.groups[g]?.length ? o.groups[g][0].price : null);
  return html`<article class="card stack-sm">${head}
    <p class="small muted">New, refurbished, used and buyback prices are kept apart: they measure different things.</p>
    <div class="seg" role="group" aria-label="Price type">${OFFER_GROUPS.map(([k, label]) => html`<button type="button" class="btn btn-sm ${k === group ? "is-selected" : ""}"
      data-offer-group="${k}" aria-pressed="${k === group}">${label} <span class="muted">${o.groups[k]?.length || 0}</span></button>`)}</div>
    ${rows.length ? html`<div class="small">Lowest: <strong class="money">${money(lowest(group), o.currency)}</strong></div>
      <div class="table-wrap"><table class="table offers-table"><thead><tr><th>Seller</th><th class="num">Price</th><th>Details</th></tr></thead><tbody>
      ${rows.map((r) => html`<tr><td>${r.url ? html`<a href="${r.url}" target="_blank" rel="noopener noreferrer">${r.seller}</a>` : r.seller}
          ${r.seller_type ? html`<div class="small muted">${humanize(r.seller_type)}</div>` : ""}</td>
        <td class="num money">${money(r.price, r.currency || o.currency)}</td>
        <td class="small">${r.estimated ? html`<span class="badge b-warn">Estimate</span> ` : r.verified ? html`<span class="badge b-ok">Verified</span> ` : ""}
          ${r.availability ? humanize(r.availability) : ""}${r.retrieved_at ? html`<div class="muted">Checked ${shortDate(r.retrieved_at)}${r.source ? ` · ${r.source}` : ""}</div>` : ""}</td></tr>`)}
      </tbody></table></div>`
      : html`<div class="empty small">No ${OFFER_GROUPS.find(([k]) => k === group)[1].toLowerCase()} prices for this phone in FlipValue.</div>`}
  </article>`;
}

async function marketSection(root, sapi, currency, opts) {
  mount(root, html`<div class="loading"><div class="spinner"></div>Loading FlipValue market data...</div>`);
  let m;
  try {
    m = await sapi.market(opts);
  } catch (err) {
    mount(root, err.code === "valuation_not_configured"
      ? callout("info", "FlipValue market data needs an API key", html`<a href="#/settings">Add your FlipValue API key in Settings</a> to see trends and store prices for this phone.`)
      : errorBox(err));
    return;
  }
  if (m.status !== "ok") {
    mount(root, html`<div class="card empty"><strong>${m.status === "no_product" ? "No FlipValue data for this phone" : "FlipValue market data is not available"}</strong>
      <p class="small">${m.message || ""}</p></div>`);
    return;
  }
  const p = m.product;
  const ev = EVIDENCE[p.evidence?.[m.region]] || null;
  let group = OFFER_GROUPS.find(([k]) => m.offers?.groups?.[k]?.length)?.[0] || "used";
  const draw = () => {
    mount(root, html`
      <div class="card card-tight row-between">
        <div><strong>FlipValue match: ${p.manufacturer} ${p.name}</strong>
          <div class="small muted">${p.release_date ? `Released ${shortDate(p.release_date)} · ` : ""}${m.region} market${m.storage_matched === false ? " · storage size not known, showing the first available" : ""}</div></div>
        <div class="row market-filters">
          ${ev ? html`<span class="badge ${ev[0]}">${ev[1]}</span>` : ""}
          ${p.storage_gb.length ? html`<label class="small" for="mk-storage">Storage</label><select id="mk-storage">${p.storage_gb.map((g) => html`<option value="${g}" ${g === m.storage_gb ? "selected" : ""}>${g} GB</option>`)}</select>` : ""}
          <label class="small" for="mk-grade">Condition</label><select id="mk-grade">${GRADES.map(([v, label]) => html`<option value="${v}" ${v === m.grade ? "selected" : ""}>${label}</option>`)}</select>
        </div>
      </div><br>
      <div class="grid grid-2">${trendCard(m, currency)}${offersCard(m, group)}</div>`);
    if (m.trend?.available) wireTrendChart(root, m.trend.series, m.trend.currency || currency);
    $("#mk-storage", root)?.addEventListener("change", (e) => marketSection(root, sapi, currency, { ...opts, storage_gb: e.target.value }));
    $("#mk-grade", root).addEventListener("change", (e) => marketSection(root, sapi, currency, { ...opts, grade: e.target.value }));
  };
  root.onclick = (e) => {
    const b = e.target.closest("[data-offer-group]");
    if (b) { group = b.dataset.offerGroup; draw(); }
  };
  draw();
}

async function valuePanel({ id, sapi, session, el }) {
  const cfg = await api.config();
  const currency = regionCurrency(cfg, session.device.region);
  let state = await sapi.valuation();
  draw();

  function draw(errorHtml) {
    const sug = state.suggested_condition;
    const configured = state.configured;
    const prev = state.stored?.request;
    const grade = prev?.condition?.grade || sug.grade || "good";
    mount(el, html`
      <div class="page-head row-between"><div><h2>Resale value <span class="step-opt">(optional)</span></h2>
        <p>FlipValue, an independent valuation service, estimates what your phone could sell for as it is and after a repair,
          and shows how its price has moved and what stores are asking. You can skip this and enter resale values yourself in the Decision tab.</p></div>
        <div class="stack-sm">${tabLink("decision", "Next: decision", true)}</div></div>
      ${!configured && !state.development_mock ? html`${callout("info", "A FlipValue API key is needed for resale values",
        html`To see resale values, <a href="#/settings">add your FlipValue API key in Settings</a>. Nothing is estimated without it. You can also skip this tab or enter values yourself in the Decision tab.`)}<br>` : ""}
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
              ${grade === v ? "checked" : ""}><span><strong>${label}</strong> <span class="small muted">${desc}</span></span></label>`)}</div>
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
          ${!state.stored ? html`<div class="card empty">No valuation yet.</div>` : ""}
          ${state.stored && !state.stored.ok && state.stored.error_code === "flipvalue_unsupported_device"
            ? html`<div class="card empty"><strong>No FlipValue data for this phone</strong><p class="small">FlipValue cannot value this model yet.</p></div>` : ""}</div>
      </div>
      <br><h3>Market data from FlipValue</h3>
      <div id="fv-market"></div>
      <div class="row no-print"><br>${tabLink("repair", "Back: repair costs")}${tabLink("decision", "Next: decision", true)}</div>`);
    if (configured) marketSection($("#fv-market", el), sapi, currency, { grade, ...(session.device.storage_gb ? { storage_gb: session.device.storage_gb } : {}) });
    else mount($("#fv-market", el), html`<p class="small muted">${state.development_mock ? "Trends and store prices are not available from the development mock."
      : html`Trends and store prices appear here once a FlipValue API key is <a href="#/settings">added in Settings</a>.`}</p>`);
    $("#val-form", el).addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = formData(ev.target);
      ev.submitter.disabled = true;
      mount($("#val-error", el), html`<div class="loading"><div class="spinner"></div>Contacting FlipValue...</div>`);
      try {
        const stored = await sapi.requestValuation({ condition_grade: f.condition_grade, repair_type: f.repair_type || null,
          storage_gb: numOrNull(f.storage_gb), age_months: numOrNull(f.age_months) });
        state = { ...state, stored };
        draw();
      } catch (err) {
        state = await sapi.valuation();
        draw(html`${err.code === "valuation_not_configured" ? callout("info", "A FlipValue API key is needed", html`<a href="#/settings">Add it in Settings</a>.`)
          : err.code === "flipvalue_unsupported_device" ? callout("info", "No FlipValue data for this phone", "FlipValue cannot value this model yet. Nothing has been estimated.")
          : errorBox(err)}<p class="small muted">Your diagnosis and repair estimates are unaffected. You can enter resale values
          manually in the Decision tab.</p>`);
      }
    });
  }
}

// ---------------------------------------------------------------------------- decision tab

const mid = (r) => (r ? (Number(r.low) + Number(r.high)) / 2 : null);

// The single values FlipValue's repair-or-sell comparison takes, from the inputs of FixMyTech's own comparison.
function compareInputs(req, rep, val, currency) {
  let repair = null;
  const [kind, ref] = (req.repair_source || "").split(":");
  if (kind === "estimate") repair = mid(rep.estimates.find((e) => e.repair_type === ref && e.low !== null));
  else if (kind === "quote" && rep.quotes[Number(ref)]) repair = Number(rep.quotes[Number(ref)].amount);
  let asIs = null; let after = null;
  if (req.value_source === "flipvalue" && val.stored?.ok) {
    const r = val.stored.response;
    asIs = Number(r.as_is.value); after = r.after_repair ? Number(r.after_repair.value) : null;
  } else if (req.manual_values) {
    asIs = mid(req.manual_values.as_is); after = mid(req.manual_values.repaired);
  }
  return { currency, as_is_value: asIs, after_repair_value: after, repair_cost: repair,
    replacement_price: req.replacement_price ?? null, selling_fee_pct: req.transaction_fee_pct ?? 0 };
}

function flipCompareCard(res, inputs) {
  const head = html`<div class="row-between"><h3>FlipValue: repair or sell?</h3><span class="badge badge-dot b-estimate">FlipValue</span></div>`;
  if (!res.available) {
    return html`<article class="card stack-sm">${head}<div class="empty"><strong>No FlipValue comparison for this phone</strong>
      <p class="small">${res.reason || ""}</p></div></article>`;
  }
  const cur = res.currency || inputs.currency;
  const best = res.scenarios.filter((s) => s.available && s.net !== null && ["sell_as_is", "repair_then_sell"].includes(s.key))
    .sort((a, b) => b.net - a.net)[0];
  const adv = res.repair_first_advantage;
  return html`<article class="card stack">${head}
    <p class="small muted">What you end up with under each option, calculated by FlipValue from the mid-point values you are using here.</p>
    <div class="table-wrap"><table class="table"><thead><tr><th>Option</th><th class="num">You end up with</th></tr></thead><tbody>
      ${res.scenarios.map((s) => html`<tr class="${best && s.key === best.key ? "is-best" : ""}"><td><strong>${s.title}</strong>
          ${best && s.key === best.key ? html` <span class="badge b-ok">Most cash</span>` : ""}
          <div class="small muted">${s.explanation || ""}</div></td>
        <td class="num">${s.available && s.net !== null ? html`<span class="money">${money(s.net, cur)}</span>`
          : html`<span class="small muted">No data${s.missing.length ? `: needs ${s.missing.map(humanize).join(", ").toLowerCase()}` : ""}</span>`}</td></tr>`)}
    </tbody></table></div>
    ${adv !== null ? callout(adv > 0 ? "ok" : "info", "", adv > 0 ? `Repairing first leaves you ${money(adv, cur)} more than selling as-is.`
      : adv < 0 ? `Selling as-is leaves you ${money(-adv, cur)} more than repairing first.` : "Repairing first and selling as-is come out the same.") : ""}
    ${res.assumptions.length ? html`<details><summary class="small">FlipValue's assumptions</summary><div class="details-body"><ul class="bullets small">
      ${res.assumptions.map((a) => html`<li>${a}</li>`)}</ul></div></details>` : ""}
  </article>`;
}

async function runFlipCompare(out, sapi, req, rep, val, currency) {
  if (!val.configured) {
    mount(out, html`<p class="small muted">${val.development_mock ? "FlipValue's own comparison is not available from the development mock."
      : html`Add your FlipValue API key in <a href="#/settings">Settings</a> to also see FlipValue's repair-or-sell comparison.`}</p>`);
    return;
  }
  const inputs = compareInputs(req, rep, val, currency);
  mount(out, html`<div class="loading"><div class="spinner"></div>Asking FlipValue to compare the options...</div>`);
  try {
    mount(out, flipCompareCard(await sapi.marketCompare(inputs), inputs));
  } catch (err) {
    mount(out, errorBox(err));
  }
}

const GOALS = [
  ["lowest_cost", "Spend the least", "Over the next couple of years, is fixing this phone cheaper than getting another one?", html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M14.5 9.5c-.5-1-1.5-1.5-2.5-1.5-1.7 0-2.8.9-2.8 2s1 1.7 2.8 2 2.8.9 2.8 2-1.1 2-2.8 2c-1.1 0-2.1-.5-2.6-1.5M12 6.5V8M12 16v1.5"/></svg>`],
  ["max_cash_now", "Get the most cash", "I'm selling it. Do I get more if I repair it first?", html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.5"/></svg>`],
  ["keep_device", "Keep my phone", "I want to keep it. What will the repair cost me?", html`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M10.5 18.5h3"/></svg>`],
];
// Which questions each goal relies on; the others are marked optional.
const NEEDS = {
  lowest_cost: ["repair", "worth", "replacement"],
  max_cash_now: ["repair", "worth"],
  keep_device: ["repair"],
};
const KEEP_FOR = [[12, "About 1 year"], [24, "About 2 years"], [36, "About 3 years"], [48, "4 years or more"]];

function currencySymbol(currency) {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).formatToParts(0).find((p) => p.type === "currency")?.value || currency;
  } catch { return currency; }
}

function moneyInput(name, id, value, sym, placeholder = "") {
  return html`<div class="money-input"><span aria-hidden="true">${sym}</span>
    <input id="${id}" name="${name}" type="number" inputmode="decimal" min="0" step="any" placeholder="${placeholder}" value="${value ?? ""}"></div>`;
}

function question(n, key, title, hint, content) {
  return html`<section class="dq card stack-sm" data-q="${key}">
    <div class="dq-head"><span class="dq-num" aria-hidden="true">${n}</span>
      <div class="spacer"><h3>${title}</h3>${hint ? html`<p class="small muted">${hint}</p>` : ""}</div>
      <span class="badge b-neutral dq-optional">Optional for your goal</span></div>
    ${content}
  </section>`;
}

async function decisionPanel({ id, sapi, session, el }) {
  const [cfg, rep, val, prior] = await Promise.all([api.config(), sapi.repair(), sapi.valuation(), sapi.report().then((r) => r.decision).catch(() => null)]);
  const currency = regionCurrency(cfg, session.device.region);
  const sym = currencySymbol(currency);
  const fvOk = val.stored?.ok;
  const fv = fvOk ? val.stored.response : null;
  const req = prior?.request || {};
  const estimates = rep.estimates.filter((e) => e.usable_for_decision && e.repair_type !== "professional_diagnosis");
  const repairChoices = [
    ...estimates.map((e) => [`estimate:${e.repair_type}`, e.repair_name, moneyRange(e.low, e.high, e.currency), "Official reference price"]),
    ...rep.quotes.map((q, i) => [`quote:${i}`, humanize(q.repair_type), money(q.amount, q.currency), "A shop's quote you added"]),
  ];
  const repairPick = req.repair_source !== undefined ? req.repair_source || "" : repairChoices[0]?.[0] || "";
  const manualAsIs = mid(req.manual_values?.as_is);
  const manualRep = mid(req.manual_values?.repaired);
  const useFv = fvOk && req.value_source !== "manual";
  const goal = req.objective || "lowest_cost";
  const horizon = req.horizon_months ?? 24;

  mount(el, html`
    <div class="page-head"><h2>Repair, sell or replace?</h2>
      <p>Answer a few quick questions. Rough numbers are fine, and anything you skip is left out rather than guessed.</p></div>
    <form id="dec-form" class="decision-form" novalidate>
      ${question(1, "goal", "What matters most to you?", "", html`<div class="goal-grid">
        ${GOALS.map(([v, l, d, icon]) => html`<label class="goal-card"><input type="radio" name="objective" value="${v}" ${goal === v ? "checked" : ""}>
          <span class="goal-icon" aria-hidden="true">${icon}</span><strong>${l}</strong><span class="small muted">${d}</span></label>`)}</div>`)}

      ${question(2, "repair", "How much will the repair cost?", "Pick the repair you would do. You can add a shop's quote in the Repair costs tab.", html`<div class="options">
        ${repairChoices.map(([v, name, amount, note]) => html`<label class="check choice"><input type="radio" name="repair_source" value="${v}" ${repairPick === v ? "checked" : ""}>
          <span class="spacer"><strong>${name}</strong><br><span class="small muted">${note}</span></span><span class="money">${amount}</span></label>`)}
        <label class="check choice"><input type="radio" name="repair_source" value="" ${repairPick === "" ? "checked" : ""}>
          <span class="spacer"><strong>I don't know yet</strong><br><span class="small muted">Leave the repair cost out</span></span></label>
        ${repairChoices.length ? "" : html`<p class="small muted">No repair price is available for this phone yet. <button type="button" class="btn btn-sm" data-goto-tab="repair">Add a shop's quote</button></p>`}
      </div>`)}

      ${question(3, "worth", "What is your phone worth?", "What you could sell it for, before any selling fees.", html`
        ${fvOk ? html`<div class="options">
          <label class="check choice"><input type="radio" name="value_source" value="flipvalue" ${useFv ? "checked" : ""}>
            <span class="spacer"><strong>Use FlipValue's estimate</strong> ${valueSourceBadge(val.stored.source)}<br>
              <span class="small muted">As it is: ${money(fv.as_is.value, fv.currency)}${fv.after_repair ? ` · After repair: ${money(fv.after_repair.value, fv.currency)}` : ""}</span></span></label>
          <label class="check choice"><input type="radio" name="value_source" value="manual" ${useFv ? "" : "checked"}>
            <span class="spacer"><strong>I'll type my own numbers</strong></span></label></div>`
          : html`<input type="hidden" name="value_source" value="manual">`}
        <div class="form-grid" id="manual-values" ${useFv ? "hidden" : ""}>
          <div class="field"><label for="m-asis">If you sold it today, as it is</label>${moneyInput("as_is", "m-asis", manualAsIs, sym, "e.g. 15000")}
            <div class="suggest" data-suggest="as_is"></div></div>
          <div class="field"><label for="m-rep">If you sold it after the repair</label>${moneyInput("repaired", "m-rep", manualRep, sym, "e.g. 20000")}
            <div class="suggest" data-suggest="repaired"></div></div>
        </div>
        ${fvOk ? "" : html`<p class="small muted">Not sure? Look up your model on OLX or Cashify${val.configured ? ", or tap a FlipValue price below a box" : ""}.
          ${val.configured ? html`You can also <a href="#" data-goto-tab="value">get a full FlipValue estimate</a>.` : ""}</p>`}`)}

      ${question(4, "replacement", "If you replaced it, what would the new phone cost?", "The price of the phone you would buy instead. Leave it empty if you wouldn't replace it.", html`
        <div class="field narrow-field"><label for="d-repl" class="sr-only">Replacement phone price</label>${moneyInput("replacement_price", "d-repl", req.replacement_price, sym, "e.g. 30000")}</div>`)}

      <details class="card dq-more">
        <summary><strong>Fine-tune the assumptions</strong> <span class="small muted">(optional, the defaults suit most people)</span></summary>
        <div class="details-body stack">
          <div class="form-grid">
            <div class="field"><label for="d-h">How long will you keep the phone?</label><select id="d-h" name="horizon_months">
              ${KEEP_FOR.map(([m, l]) => html`<option value="${m}" ${Number(horizon) === m ? "selected" : ""}>${l}</option>`)}
              ${KEEP_FOR.some(([m]) => m === Number(horizon)) ? "" : html`<option value="${horizon}" selected>${horizon} months</option>`}</select>
              <span class="hint">Costs are compared over this time.</span></div>
            <div class="field"><label for="d-fee">Selling platform fee</label><div class="unit-input"><input id="d-fee" name="transaction_fee_pct" type="number" min="0" max="50" step="0.5" value="${req.transaction_fee_pct ?? 0}"><span>%</span></div>
              <span class="hint">What the site or shop keeps when you sell. 0 if you sell to a friend.</span></div>
            <div class="field"><label for="d-extra">Other repairs you expect</label>${moneyInput("expected_extra_repairs", "d-extra", req.expected_extra_repairs ?? 0, sym)}
              <span class="hint">For example, a battery that will need replacing soon.</span></div>
            <div class="field"><label for="d-dold">Your phone loses value each year</label><div class="unit-input"><input id="d-dold" name="old_device_annual_depreciation_pct" type="number" min="0" max="90" value="${req.old_device_annual_depreciation_pct ?? 20}"><span>%</span></div>
              <span class="hint">Older phones usually lose about 20% a year.</span></div>
            <div class="field"><label for="d-dnew">A new phone loses value each year</label><div class="unit-input"><input id="d-dnew" name="new_device_annual_depreciation_pct" type="number" min="0" max="90" value="${req.new_device_annual_depreciation_pct ?? 30}"><span>%</span></div>
              <span class="hint">New phones drop faster, about 30% in the first years.</span></div>
          </div>
        </div>
      </details>
      <div id="dec-error"></div>
      <div class="row"><button class="btn btn-primary btn-lg" type="submit">Show me the best option &rarr;</button></div>
    </form>
    <div id="dec-result" class="stack decision-result">${prior ? decisionBody(prior.result, prior.request) : ""}</div>
    <div id="fv-compare"></div>
    <div class="row no-print decision-nav">${tabLink("value", "Back: resale value")}${nextLink(`#/s/${id}/report`, "View full report")}</div>`);
  applyRangeBars(el);
  if (prior) runFlipCompare($("#fv-compare", el), sapi, prior.request, rep, val, currency);

  const form = $("#dec-form", el);
  const syncGoal = () => {
    const needs = NEEDS[form.objective.value] || [];
    form.querySelectorAll(".dq[data-q]").forEach((q) => q.classList.toggle("is-optional", q.dataset.q !== "goal" && !needs.includes(q.dataset.q)));
  };
  const syncValues = () => {
    const manual = form.querySelector('[name="value_source"]:checked, input[type="hidden"][name="value_source"]')?.value !== "flipvalue";
    $("#manual-values", el).hidden = !manual;
  };
  form.addEventListener("change", (ev) => {
    if (ev.target.name === "objective") syncGoal();
    if (ev.target.name === "value_source") syncValues();
  });
  syncGoal();

  // FlipValue price suggestions for the "worth" boxes: tap to fill. Shown only when FlipValue has data.
  if (val.configured) {
    const grade = val.suggested_condition?.grade || "good";
    const latest = (m) => {
      const s = m?.status === "ok" && m.trend?.available ? Object.values(m.trend.series)[0] : null;
      return s?.length ? s[s.length - 1].median : null;
    };
    const chip = (box, amount, label) => {
      const host = $(`[data-suggest="${box}"]`, el);
      if (host && amount) mount(host, html`<button type="button" class="suggest-chip" data-fill="${box}" data-amount="${Math.round(amount)}">Use ${money(amount, currency)} <span class="muted">· ${label}</span></button>`);
    };
    Promise.all([sapi.market({ grade }).catch(() => null), grade === "good" ? null : sapi.market({ grade: "good" }).catch(() => null)]).then(([now, good]) => {
      chip("as_is", latest(now), `FlipValue, ${humanize(grade).toLowerCase()} condition`);
      // In good condition already, an "after repair" price would just repeat the first one.
      if (good) chip("repaired", latest(good), "FlipValue, good condition after repair");
    });
    el.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-fill]");
      if (b) { form.elements[b.dataset.fill].value = b.dataset.amount; form.elements[b.dataset.fill].focus(); }
    });
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = formData(form);
    const point = (v) => { const n = numOrNull(v); return n === null ? null : { low: n, high: n }; };
    const body = {
      objective: f.objective, repair_source: f.repair_source || null, value_source: f.value_source || "manual",
      replacement_price: numOrNull(f.replacement_price), horizon_months: numOrNull(f.horizon_months) ?? 24,
      transaction_fee_pct: numOrNull(f.transaction_fee_pct) ?? 0, expected_extra_repairs: numOrNull(f.expected_extra_repairs) ?? 0,
      old_device_annual_depreciation_pct: numOrNull(f.old_device_annual_depreciation_pct) ?? 20,
      new_device_annual_depreciation_pct: numOrNull(f.new_device_annual_depreciation_pct) ?? 30,
      currency,
    };
    if (body.value_source === "manual") body.manual_values = { as_is: point(f.as_is), repaired: point(f.repaired) };
    const out = $("#dec-result", el);
    ev.submitter.disabled = true;
    try {
      const d = await sapi.decision(body);
      mount($("#dec-error", el), "");
      mount(out, decisionBody(d.result, d.request));
      applyRangeBars(out);
      out.scrollIntoView({ behavior: "smooth", block: "start" });
      runFlipCompare($("#fv-compare", el), sapi, d.request, rep, val, currency);
    } catch (err) {
      mount($("#dec-error", el), errorBox(err));
    } finally {
      ev.submitter.disabled = false;
    }
  });
}
