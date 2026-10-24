import { api, sessionApi } from "../api.js";
import { history } from "../store.js";
import { html, mount, callout, sourceBadge, humanize, dateTime, downloadJson, formData, numOrNull, $, toast } from "../ui.js";
import { loadSession, stepper } from "./common.js";
import { diagnosisBody, CURRENT_SESSION } from "./diagnose.js";
import { repairBody, valuationResult, decisionBody, applyRangeBars } from "./money.js";

export async function reportView(ctx) {
  const s = await loadSession(ctx);
  if (s.error) return mount(ctx.el, s.error);
  const { id, sapi, session } = s;
  CURRENT_SESSION.id = id;
  const [report, catalog, cfg] = await Promise.all([sapi.report(), api.faults(), api.config()]);
  const faultsById = Object.fromEntries(catalog.faults.map((f) => [f.id, f.name]));
  const dev = report.session.device;
  const obs = report.observations;

  mount(ctx.el, html`
    ${stepper(id, "report", session)}
    <div class="page-head row-between">
      <div><div class="eyebrow">FixMyTech diagnostic report</div>
        <h1>${[dev.manufacturer, dev.model].filter(Boolean).join(" ") || "Your phone"}</h1>
        <p class="small muted">Generated ${dateTime(report.generated_at)} &middot; Report ${id.slice(0, 8)} &middot;
          ${session.mode === "usb" ? "USB scan + questions" : "Questions only"} &middot; Kept until ${dateTime(report.session.expires_at)}</p></div>
      <div class="row no-print">
        <button class="btn" data-action="print">Print / save as PDF</button>
        <button class="btn" data-action="json">Download data (JSON)</button>
      </div>
    </div>
    ${callout("info", "Read this first", report.disclaimer)}<br>

    <section class="stack"><h2>1. Diagnosis</h2>${diagnosisBody(report.diagnosis)}</section><br>

    <section class="card stack-sm"><h2>2. Evidence</h2>
      <h3>Device readings ${sourceBadge("measured")}</h3>
      ${obs.has_scan ? html`<div class="table-wrap"><table class="table"><thead><tr><th>Check</th><th>Status</th><th>Values</th></tr></thead><tbody>
        ${obs.observations.map((o) => html`<tr><td>${o.title}</td><td>${o.status === "ok" ? sourceBadge(o.evidence_kind) : html`${sourceBadge("unavailable")} ${humanize(o.status)}`}</td>
          <td class="small">${Object.entries(o.value).filter(([, v]) => v !== null).map(([k, v]) => `${humanize(k)}: ${typeof v === "boolean" ? (v ? "yes" : "no") : v}`).join(" · ")}</td></tr>`)}
        </tbody></table></div>` : html`<p class="small muted">No USB scan in this report.</p>`}
      <h3>Your answers ${sourceBadge("customer_reported")}</h3>
      ${report.answers.length ? html`<dl class="kv">${report.answers.map((a) => html`<dt>${a.question}</dt><dd>${a.labels.join(", ")}</dd>`)}</dl>` : html`<p class="small muted">No answers.</p>`}
    </section><br>

    <section class="stack"><h2>3. Repair costs</h2>${repairBody(report.repair, faultsById)}</section><br>

    <section class="stack"><h2>4. Resale value</h2>
      ${report.valuation ? valuationResult(report.valuation) : html`<p class="small muted">No valuation requested${cfg.flipvalue_mode === "disabled" ? " (valuation is not enabled on this server)" : ""}.</p>`}
    </section><br>

    <section class="stack"><h2>5. Repair, sell or replace</h2>
      ${report.decision ? decisionBody(report.decision.result, report.decision.request) : html`<p class="small muted">No comparison yet. <a href="#/s/${id}/decision">Compare options</a>.</p>`}
    </section><br>

    <section class="card stack no-print">
      <h2>Manage this report</h2>
      <form id="dev-form" class="form-grid">
        <div class="field"><label for="e-man">Manufacturer</label><input id="e-man" name="manufacturer" type="text" maxlength="80" value="${dev.manufacturer || ""}"></div>
        <div class="field"><label for="e-mod">Model</label><input id="e-mod" name="model" type="text" maxlength="80" value="${dev.model || ""}"></div>
        <div class="field"><label for="e-sto">Storage (GB)</label><input id="e-sto" name="storage_gb" type="number" min="1" value="${dev.storage_gb ?? ""}"></div>
        <div class="field"><label for="e-age">Age (months)</label><input id="e-age" name="age_months" type="number" min="0" value="${dev.age_months ?? ""}"></div>
        <div class="field"><span class="label">&nbsp;</span><button class="btn" type="submit">Save device details</button></div>
      </form>
      <p class="small muted">Changing device details clears any previous resale valuation, because it no longer matches.</p>
      <hr class="divider">
      <div class="row-between"><div><strong>Delete this report</strong><div class="small muted">Removes all of its data from the FixMyTech server now. This cannot be undone.</div></div>
        <button class="btn btn-danger" data-action="delete">Delete report</button></div>
    </section>`);
  applyRangeBars(ctx.el);

  $("#dev-form", ctx.el).addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = formData(ev.target);
    try {
      await sapi.updateDevice({ manufacturer: f.manufacturer.trim() || null, model: f.model.trim() || null,
        storage_gb: numOrNull(f.storage_gb), age_months: numOrNull(f.age_months), region: dev.region });
      history.update(id, { label: [f.manufacturer, f.model].filter(Boolean).join(" ") || "Diagnosis" });
      toast("Device details saved.");
      ctx.rerender();
    } catch (err) { toast(err.message); }
  });

  ctx.el.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-action]");
    if (!b) return;
    if (b.dataset.action === "print") window.print();
    if (b.dataset.action === "json") downloadJson(`fixmytech-report-${id.slice(0, 8)}.json`, report);
    if (b.dataset.action === "delete") {
      if (!window.confirm("Delete this report permanently?")) return;
      try {
        await sapi.remove();
        history.remove(id);
        toast("Report deleted.");
        ctx.navigate("/history");
      } catch (err) { toast(err.message); }
    }
  });
}

export async function historyView(ctx) {
  const list = history.list();
  mount(ctx.el, html`
    <div class="page-head row-between"><div><h1>My reports</h1>
      <p>Reports started in this browser. The keys that unlock them are stored only here.</p></div>
      <a class="btn btn-primary" href="#/start">New diagnosis</a></div>
    ${list.length ? html`<div class="stack-sm" id="hist">${list.map((s) => html`<div class="device-row" data-id="${s.id}">
        <span class="status-dot ${s.expired ? "bad" : "ok"}"></span>
        <div class="spacer"><strong>${s.label}</strong><div class="small muted">${s.mode === "usb" ? "USB scan" : "Questions only"} &middot; started ${dateTime(s.created)}
          ${s.expired ? " · expired or deleted" : ` · kept until ${dateTime(s.expires)}`}</div></div>
        ${s.expired ? "" : html`<a class="btn btn-sm" href="#/s/${s.id}/${s.mode === "usb" ? "findings" : "diagnosis"}">Open</a>
          <a class="btn btn-sm" href="#/s/${s.id}/report">Report</a>`}
        <button class="btn btn-sm btn-ghost" data-action="forget" data-id="${s.id}">Forget</button>
        ${s.expired ? "" : html`<button class="btn btn-sm btn-danger" data-action="delete" data-id="${s.id}">Delete</button>`}
      </div>`)}</div>
      <p class="small muted">"Forget" removes a report from this list only. "Delete" erases it from the server.</p>`
      : html`<div class="card empty"><p>No reports yet.</p><a class="btn btn-primary" href="#/start">Start a diagnosis</a></div>`}`);

  ctx.el.addEventListener("click", async (ev) => {
    const b = ev.target.closest("[data-action]");
    if (!b) return;
    const sid = b.dataset.id;
    if (b.dataset.action === "forget") { history.remove(sid); ctx.rerender(); }
    if (b.dataset.action === "delete") {
      if (!window.confirm("Delete this report from the server permanently?")) return;
      try { await sessionApi(sid).remove(); } catch (err) { if (err.status !== 404) { toast(err.message); return; } }
      history.remove(sid);
      toast("Report deleted.");
      ctx.rerender();
    }
  });
  // Mark entries the server no longer has (expired under the retention policy).
  for (const s of list.filter((x) => !x.expired)) {
    sessionApi(s.id).get().catch((err) => { if (err.status === 404) { history.update(s.id, { expired: true }); } });
  }
}
