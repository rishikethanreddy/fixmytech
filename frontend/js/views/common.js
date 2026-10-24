import { sessionApi } from "../api.js";
import { history } from "../store.js";
import { html, callout } from "../ui.js";

// What to show when the local agent can't be used, with the action that fixes it.
export function agentProblem(err, id) {
  const fix = {
    agent_unreachable: html`Start the agent on this computer (see <a href="#/s/${id}/connect">Connect</a>), then try again.`,
    agent_outdated: html`After restarting it, open the new pairing link it prints, then try again.`,
    agent_not_paired: html`<a href="#/s/${id}/connect">Go to Connect</a> and open or paste the pairing link the agent printed.`,
    no_device: html`Then press the button again. <a href="#/s/${id}/connect">Check the connection</a>.`,
    choose_device: html`<a href="#/s/${id}/connect">Choose the phone</a>.`,
    agent_timeout: "",
  }[err?.code];
  const title = {
    agent_unreachable: "The FixMyTech agent isn't running", agent_outdated: "Please restart the FixMyTech agent",
    agent_not_paired: "Pair this browser with the agent", no_device: "No phone connected",
    choose_device: "Choose a phone", agent_timeout: "The agent didn't answer in time",
  }[err?.code] || "The agent reported a problem";
  return callout("warn", title, html`${err?.message || ""}${fix ? html` ${fix}` : ""}`);
}

const STEPS = [
  ["connect", "Connect", "usb"],
  ["findings", "Findings", "usb"],
  ["tests", "Tests"],
  ["questions", "Questions"],
  ["diagnosis", "Diagnosis"],
  ["repair", "Repair cost"],
  ["value", "Resale value", null, true],
  ["decision", "Decision"],
  ["report", "Report"],
];

// Steps with a real completion state; the others count as done once the customer has moved past them.
function completion(session) {
  const p = session.progress || {};
  return {
    connect: session.has_scan,
    findings: session.has_scan,
    tests: p.tests_complete,
    questions: p.questions_complete,
    decision: session.has_decision,
  };
}

export function stepper(id, current, session) {
  const steps = STEPS.filter(([, , only]) => !only || only === session.mode);
  const idx = steps.findIndex(([k]) => k === current);
  const known = completion(session);
  const p = session.progress || {};
  return html`<nav class="stepper no-print" aria-label="Diagnosis steps"><ol>
    ${steps.map(([key, label, , optional], i) => {
      const done = key in known ? Boolean(known[key]) : i < idx;
      const extra = key === "tests" && p.tests_available && !done ? `${p.tests_done}/${p.tests_available}` : "";
      return html`<li><a href="#/s/${id}/${key}" class="${done ? "done" : ""}" ${key === current ? html`aria-current="step"` : ""}>
        <span class="num" aria-hidden="true">${done ? "✓" : i + 1}</span><span class="lbl">${label}${optional ? html`<span class="step-opt"> (optional)</span>` : ""}</span>
        ${extra ? html`<span class="step-count">${extra}</span>` : ""}${done ? html`<span class="sr-only">(completed)</span>` : ""}</a></li>`;
    })}
  </ol></nav>`;
}

// Loads the session (verifying this browser still holds its token) or renders a helpful dead end.
export async function loadSession(ctx) {
  const id = ctx.params[0];
  const sapi = sessionApi(id);
  if (!sapi.known) {
    return { id, sapi, error: html`<div class="card stack">${callout("warn", "This report isn't available in this browser",
      "Reports are protected by a private key stored only in the browser that created them.")}
      <div><a class="btn" href="#/history">Go to my reports</a></div></div>` };
  }
  try {
    const session = await sapi.get();
    return { id, sapi, session };
  } catch (err) {
    if (err.status === 404) {
      history.update(id, { expired: true });
      return { id, sapi, error: html`<div class="card stack">${callout("warn", "This report no longer exists",
        "It was deleted or has expired under the retention policy.")}<div><a class="btn" href="#/start">Start a new diagnosis</a></div></div>` };
    }
    throw err;
  }
}

export function nextLink(href, label) {
  return html`<a class="btn btn-primary" href="${href}">${label} &rarr;</a>`;
}
