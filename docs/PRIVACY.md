# Privacy

## Principles

Collect the minimum needed to diagnose, keep it briefly, let the customer delete it, and never collect personal content.

## Data inventory

| Data | Source | Sent to server? | Stored? |
|---|---|---|---|
| Device model, Android version, patch level | Phone (getprop allowlist) or customer | Yes | Yes, in the session |
| Battery, storage, memory, thermal, crash **counts**, boot-reason class, camera count, touchscreen flag, airplane/Wi-Fi/SIM flags | Phone | Yes (normalized scalars) | Yes |
| Device serial | Phone | **No** (salted one-way hash `device_ref`, salt stored only on the agent PC) | Hash only |
| Raw command output (logs, dumpsys) | Phone | **No**, parsed in memory on the PC and discarded | No |
| Messages, photos, contacts, accounts, location, app list, Wi-Fi names, IMEI, phone number | - | Never read | No |
| Symptom answers, quotes, device age/storage, decision inputs | Customer | Yes | Yes |
| Bug-report crash/freeze counts (opt-in) | Phone, via agent | Counts only; the report file is processed on the customer's PC and deleted | Counts |
| Hands-on test results: pass/fail, touch coverage, microphone peak level, camera resolution, sensor event counts | Phone browser / customer | Yes | Yes |
| Camera images, audio, touch paths | Phone browser | **No**: processed live in the browser, never recorded or uploaded | No |
| Consent record (what was agreed, when) | Customer | Yes | Yes |
| IP address | Network | Used transiently for rate limiting | Not persisted (may appear in web server access logs of the hosting environment) |

No accounts, names or emails. Reports are linked only by a random session id and a bearer token held in the
customer's browser.

## Consent

* Required: processing diagnostic data for the report (session cannot start without it).
* Separate, required per scan: the read-only USB scan (both in the UI and enforced by the agent API).
* Optional: keep the report for the retention period.
* Separate, per action: bug report, connector test, opening the test page on the phone (`adb reverse`); browser
  permission prompts for camera, microphone and motion on the phone.

## Retention and deletion

* Kept reports: `FIXMYTECH_RETENTION_DAYS` (default 30). Others: `FIXMYTECH_UNSAVED_RETENTION_HOURS` (default 24).
* Expired sessions are inaccessible immediately and purged at startup and whenever a session is created.
* Customers can delete a report at any time (Report page, My reports, or `DELETE /api/v1/sessions/{id}`), which removes
  the row immediately.

## Third parties

Only on explicit request, FlipValue receives: manufacturer, model, storage, age, market, condition grade, fault
categories with moderate/strong support, and the repair type to value. No identifiers.

## Open items

* A formal privacy notice / legal review for the target jurisdiction (e.g. GDPR, India's DPDP Act) is outstanding.
* Hosting-provider log retention must be configured to match this policy when deployed.
