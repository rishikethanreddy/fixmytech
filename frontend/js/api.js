// Backend API client. Every session request carries that session's capability token.
import { history } from "./store.js";

export class ApiError extends Error {
  constructor(status, code, message, requestId, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.details = details;
  }
}

async function request(method, path, { body, token, timeoutMs = 30000, extraHeaders } = {}) {
  const headers = { Accept: "application/json", ...extraHeaders };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`/api/v1${path}`, {
      method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: ctrl.signal,
      credentials: "omit", cache: "no-store",
    });
  } catch (e) {
    throw new ApiError(0, "network_error", e.name === "AbortError"
      ? "The server took too long to respond." : "Could not reach the FixMyTech server. Check your connection.");
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    const e = data?.error || {};
    throw new ApiError(res.status, e.code || "http_error", e.message || `Request failed (HTTP ${res.status}).`,
      e.request_id, e.details);
  }
  return data;
}

let configCache = null;

export const api = {
  config: async () => (configCache ||= await request("GET", "/config")),
  health: () => request("GET", "/health"),
  checks: () => request("GET", "/catalog/checks"),
  faults: () => request("GET", "/catalog/faults"),
  pricingStatus: () => request("GET", "/pricing/status"),
  createSession: (body) => request("POST", "/sessions", { body }),
  // Partner (FlipValue) service settings. The admin token is only needed when the server requires one.
  partner: {
    get: (adminToken) => request("GET", "/settings/partner", { extraHeaders: adminHeader(adminToken) }),
    save: (body, adminToken) => request("PUT", "/settings/partner", { body, extraHeaders: adminHeader(adminToken) }),
    remove: (adminToken) => request("DELETE", "/settings/partner", { extraHeaders: adminHeader(adminToken) }),
    test: (adminToken) => request("POST", "/settings/partner/test", { timeoutMs: 45000, extraHeaders: adminHeader(adminToken) }),
  },
};

function adminHeader(token) { return token ? { "X-Admin-Token": token } : undefined; }

// Session-scoped calls; the token is looked up from local history.
export function sessionApi(id) {
  const entry = history.get(id);
  const token = entry?.token;
  const s = (method, path, body, opts = {}) => request(method, `/sessions/${id}${path}`, { body, token, ...opts });
  return {
    known: Boolean(token),
    get: () => s("GET", ""),
    remove: () => s("DELETE", ""),
    updateDevice: (device) => s("PATCH", "/device", device),
    putScan: (scan) => s("PUT", "/scan", scan),
    observations: () => s("GET", "/observations"),
    nextQuestion: (more = false) => s("GET", `/questions/next${more ? "?more=true" : ""}`),
    answers: () => s("GET", "/answers"),
    answer: (qid, value) => s("PUT", `/answers/${encodeURIComponent(qid)}`, { value }),
    unanswer: (qid) => s("DELETE", `/answers/${encodeURIComponent(qid)}`),
    diagnosis: () => s("GET", "/diagnosis"),
    repair: () => s("GET", "/repair-estimate"),
    addQuote: (q) => s("POST", "/quotes", q),
    deleteQuote: (i) => s("DELETE", `/quotes/${i}`),
    valuation: () => s("GET", "/valuation"),
    requestValuation: (body) => s("POST", "/valuation", body, { timeoutMs: 45000 }),
    decision: (body) => s("POST", "/decision", body),
    report: () => s("GET", "/report"),
    tests: () => s("GET", "/tests"),
    putTest: (tid, result) => s("PUT", `/tests/${encodeURIComponent(tid)}`, result),
    putAgentTest: (tid, result) => s("POST", `/tests/${encodeURIComponent(tid)}/agent-result`, result),
    deleteTest: (tid) => s("DELETE", `/tests/${encodeURIComponent(tid)}`),
    phoneLink: () => s("POST", "/phone-link"),
    putObservation: (checkId, obs) => s("PUT", `/observations/${encodeURIComponent(checkId)}`, obs),
  };
}
