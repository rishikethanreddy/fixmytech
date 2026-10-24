// Browser-side storage.
// * Report history (session id + access token) lives in localStorage on this device only; the server
//   never learns which reports belong together. Removing an entry here does not delete it on the server.
// * The local-agent pairing token lives in localStorage so every tab of this site can use the agent. It only works
//   against the agent on 127.0.0.1 and is useless once the agent stops (each start prints a new token).

const HISTORY_KEY = "fmt.history.v1";
const AGENT_KEY = "fmt.agent.v1";
const SETTINGS_KEY = "fmt.settings.v1";

function read(storage, key, fallback) {
  try {
    const v = storage.getItem(key);
    return v ? JSON.parse(v) : fallback;
  } catch {
    return fallback;
  }
}

function write(storage, key, value) {
  try {
    storage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export const history = {
  list() { return read(localStorage, HISTORY_KEY, []); },
  get(id) { return this.list().find((s) => s.id === id) || null; },
  add(entry) {
    const list = this.list().filter((s) => s.id !== entry.id);
    list.unshift(entry);
    write(localStorage, HISTORY_KEY, list.slice(0, 50));
  },
  update(id, patch) {
    write(localStorage, HISTORY_KEY, this.list().map((s) => (s.id === id ? { ...s, ...patch } : s)));
  },
  remove(id) { write(localStorage, HISTORY_KEY, this.list().filter((s) => s.id !== id)); },
  clear() { try { localStorage.removeItem(HISTORY_KEY); } catch { /* ignore */ } },
};

export const agentPairing = {
  get() { return read(localStorage, AGENT_KEY, null); },
  set(url, token) { write(localStorage, AGENT_KEY, { url, token }); },
  clear() { try { localStorage.removeItem(AGENT_KEY); } catch { /* ignore */ } },
};

export const settings = {
  get() { return read(localStorage, SETTINGS_KEY, {}); },
  set(patch) { write(localStorage, SETTINGS_KEY, { ...this.get(), ...patch }); },
};

// The device chosen on the Connect step, remembered so later steps (tests, bug report) can use it in any tab.
export const selectedDevice = {
  get(sessionId) { return read(localStorage, `fmt.serial.${sessionId}`, null); },
  set(sessionId, serial) { write(localStorage, `fmt.serial.${sessionId}`, serial); },
};

// Colour theme: "light", "dark" or "system" (follow the operating system). Applied early by theme.js.
export const theme = {
  get() { return read(localStorage, "fmt.theme.v1", "system"); },
  set(value) { write(localStorage, "fmt.theme.v1", value); },
};
