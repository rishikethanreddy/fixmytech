# Security

## Trust boundaries

| Boundary | Threat | Control (file) |
|---|---|---|
| Website ↔ local agent | Any website the customer visits tries to drive the agent | Loopback bind; `Host` must be loopback (DNS rebinding); `Origin` allowlist (403); pairing token `X-Agent-Token` compared in constant time; custom header forces CORS preflight; CORS limited to the FixMyTech origin (`agent/server.py`) |
| Agent ↔ phone | Command injection, destructive commands | Commands are fixed argv tuples in `ALLOWED_SHELL_COMMANDS`, `shell=False`, serial validated by regex and must be in `adb devices` output, timeouts (`agent/adb.py`). No endpoint accepts a command. Tested: `reboot`, `pm uninstall`, `rm`, `settings put`, `;` injection, reading `/sdcard` are refused |
| Agent → backend data | Personal data leaving the PC | Parsers return scalars only; getprop allowlist drops serial/IMEI props; dropbox parsed for tags/timestamps only; batterystats reduced to capacity lines; serial replaced by salted hash; free text redacted (`core/redaction.py`) |
| Agent consented actions | Abuse of state-changing ADB | Separate `action_argv` allowlist: `reverse tcp:P tcp:P`, `reverse --remove`, `am start -a VIEW -d http://localhost:P/p/<token>`, `bugreport <tempdir>/bugreport.zip`; ports 1024-65535 and token `[A-Za-z0-9_-]{20,100}` validated; explicit consent field; same token/Host/Origin guards. Bug-report file deleted in `finally` |
| Phone test page | Token misuse | Phone token is separate from the report token, stored hashed, expires in 30 min, revocable, and can only list/submit that session's phone-browser tests (cannot read the session or report). `Permissions-Policy` allows camera/microphone only on `/p/*` and only for self |
| Price ingestion | Overloading or violating sources | robots.txt checked per fetch, identifying User-Agent with contact, ≥5 s per host, bounded retries, no auth/CAPTCHA bypass; usage policy per source; production display allowlist |
| Browser ↔ backend | Unauthorized report access | Per-session random 256-bit capability token, stored as SHA-256 hash, constant-time compare; 404 for unknown/wrong (no enumeration); expiry enforced (`server/api.py`, `server/security.py`) |
| Untrusted input | Oversized/malformed payloads | Pydantic validation with `extra="forbid"` for scans; bounded value sizes; 256 KB body limit; answer validation against option sets |
| Abuse | Flooding, FlipValue quota burn | Per-IP sliding-window rate limit; per-session valuation limit |
| Backend ↔ FlipValue | Key leakage, spoofed responses | Key only in server env (`SecretStr`), never in frontend, logs, reports or `/health`; https required in live mode (except localhost); strict response validation; live mode rejects responses flagged `mock`; bounded retries |
| Web UI | XSS, clickjacking | Escape-by-default templating (`frontend/js/ui.js`); CSP `script-src 'self'; style-src 'self'` (no inline), `frame-ancestors 'none'`, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` |
| Errors & logs | Stack traces / secrets in responses | Structured errors with request id; unhandled exceptions logged server-side only; request log records route template, status, latency (no bodies, tokens or query strings) |

## Configuration safety

* `FIXMYTECH_ENV=production` refuses `FLIPVALUE_MODE=mock`, non-https FlipValue URLs and demo pricing, requires an
  explicit `FIXMYTECH_PRICING_ALLOWED_SOURCES`, and disables `/api/docs`.
* `FIXMYTECH_TRUST_PROXY_HEADERS` must stay `false` unless behind a trusted reverse proxy (otherwise clients can spoof
  `X-Forwarded-For` to dodge rate limits).
* Serve the backend over HTTPS in any non-local deployment (terminate TLS at a reverse proxy).
* `.env` is git-ignored; `.env.example` contains no secrets.

## Known gaps / next steps

* Rate limiter is in-process; use a shared store (e.g. Redis) when running several instances.
* No CSRF token is needed (no cookies; bearer tokens in headers), but a deployment adding cookie auth must add one.
* The agent pairing token is valid for the agent's lifetime; there is no rotation or expiry beyond restart.
* Report tokens are bearer secrets kept in browser `localStorage`; anyone with access to that browser profile can
  open those reports. This is documented to customers on the Privacy page.
* Dependency audit: `pip-audit -r requirements.lock.txt` (clean on 2026-10-19). Re-run regularly.
* `adb reverse` forwards only the FixMyTech port and is removed on request or when the phone disconnects; while active,
  apps on the phone could also reach that port on localhost (they would still need a session or phone token).
* Agent-relayed results (scan, connector test, bug-report counts) are trusted as the browser sends them; a customer can
  only affect their own report.
* No penetration test has been performed.
