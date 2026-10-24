// Client for the LOCAL diagnostic agent running on the customer's computer (127.0.0.1).
// The browser cannot run ADB itself; it can only talk to this agent, which requires the pairing token
// printed by the agent when it starts.
import { agentPairing, settings, selectedDevice } from "./store.js";

export class AgentError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export function agentUrl(defaultUrl) {
  return agentPairing.get()?.url || settings.get().agentUrl || defaultUrl || "http://127.0.0.1:8765";
}

export function isLoopback(url) {
  try {
    const u = new URL(url);
    return u.protocol === "http:" && (u.hostname === "127.0.0.1" || u.hostname === "localhost");
  } catch {
    return false;
  }
}

async function call(base, path, { method = "GET", body, token, timeoutMs = 15000 } = {}) {
  if (!isLoopback(base)) throw new AgentError("agent_url_not_local", "The agent address must be on this computer (127.0.0.1).");
  const headers = { Accept: "application/json" };
  if (token) headers["X-Agent-Token"] = token;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${base}${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: ctrl.signal,
      credentials: "omit", cache: "no-store",
    });
  } catch (e) {
    if (e.name === "AbortError") {
      throw new AgentError("agent_timeout", "The FixMyTech agent is running but took too long to answer. Check the phone is still connected and unlocked, then try again.");
    }
    throw new AgentError("agent_unreachable", "The FixMyTech agent is not running on this computer, or it blocked this page.");
  } finally {
    clearTimeout(timer);
  }
  let data = null;
  try { data = await res.json(); } catch { /* ignore */ }
  if (!res.ok) {
    const e = data?.error || {};
    throw new AgentError(e.code || "agent_error", e.message || `Agent error (HTTP ${res.status})`, res.status);
  }
  return data;
}

export function agentClient(defaultUrl) {
  const base = agentUrl(defaultUrl);
  const token = agentPairing.get()?.token;
  return {
    base,
    paired: Boolean(token),
    health: () => call(base, "/agent/v1/health", { timeoutMs: 4000 }),
    session: () => call(base, "/agent/v1/session", { token, timeoutMs: 4000 }),
    adb: () => call(base, "/agent/v1/adb", { token, timeoutMs: 40000 }),
    devices: () => call(base, "/agent/v1/devices", { token, timeoutMs: 25000 }),
    summary: (serial) => call(base, `/agent/v1/devices/${encodeURIComponent(serial)}/summary`, { token, timeoutMs: 30000 }),
    startScan: (serial) => call(base, "/agent/v1/scans", { method: "POST", token, body: { serial, consent: true } }),
    scanStatus: (id) => call(base, `/agent/v1/scans/${encodeURIComponent(id)}`, { token }),
    connectorTest: (serial, durationS = 45) => call(base, `/agent/v1/devices/${encodeURIComponent(serial)}/connector-test`,
      { method: "POST", token, body: { consent: true, duration_s: durationS } }),
    bugreport: (serial) => call(base, `/agent/v1/devices/${encodeURIComponent(serial)}/bugreport`,
      { method: "POST", token, body: { consent: true } }),
    job: (id) => call(base, `/agent/v1/jobs/${encodeURIComponent(id)}`, { token }),
    // A local site is forwarded to the phone by port; a hosted site is opened by its public https address.
    openPhoneTests: (serial, target, phoneToken) => call(base, `/agent/v1/devices/${encodeURIComponent(serial)}/phone-tests`,
      { method: "POST", token, body: { consent: true, token: phoneToken,
        ...(target.siteOrigin ? { site_origin: target.siteOrigin } : { web_port: target.port }) }, timeoutMs: 30000 }),
  };
}

// Accepts a full pairing link (".../#/pair?agent=...&token=...") or just the token.
export function parsePairing(input, fallbackUrl) {
  const text = (input || "").trim();
  if (!text) return null;
  const q = text.includes("?") ? text.slice(text.indexOf("?") + 1) : null;
  if (q) {
    const p = new URLSearchParams(q);
    const token = p.get("token");
    if (token) return { url: p.get("agent") || fallbackUrl, token };
  }
  if (/^[A-Za-z0-9_-]{20,}$/.test(text)) return { url: fallbackUrl, token: text };
  return null;
}

// Everything a later step needs before using the agent: reachable, new enough, paired, and a phone to use.
// Throws an AgentError whose code tells the UI what the customer should do (see agentProblem in views/common.js).
export async function prepareAgent(defaultUrl, sessionId, feature) {
  const agent = agentClient(defaultUrl);
  const health = await agent.health();
  if (feature && !(health.features || []).includes(feature)) {
    throw new AgentError("agent_outdated", `The FixMyTech agent running on this computer (version ${health.agent_version || "unknown"}) is too old for this. Stop it (Ctrl+C in its window) and start it again to use the current version.`);
  }
  if (!agent.paired) throw new AgentError("agent_not_paired", "This browser isn't paired with the agent yet.");
  try {
    await agent.session();
  } catch (e) {
    if (e.status === 401) {
      agentPairing.clear();
      throw new AgentError("agent_not_paired", "The agent was restarted, so this browser needs to pair with it again.");
    }
    throw e;
  }
  const ready = (await agent.devices()).devices.filter((d) => d.ready);
  let serial = selectedDevice.get(sessionId);
  if (!ready.some((d) => d.serial === serial)) {
    if (ready.length === 1) {
      serial = ready[0].serial;
      selectedDevice.set(sessionId, serial);
    } else if (!ready.length) {
      throw new AgentError("no_device", "No phone is connected and ready. Connect it with the USB cable, unlock it and allow USB debugging if asked.");
    } else {
      throw new AgentError("choose_device", "Several phones are connected. Choose the one to use on the Connect step.");
    }
  }
  return { agent, serial, health };
}
