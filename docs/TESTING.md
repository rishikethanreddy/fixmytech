# Testing

## Commands (PowerShell, project root)

```powershell
.\scripts\check.ps1                                              # all automated gates
.\.venv\Scripts\python -m pytest -q                              # default suite (no phone, no browser, no network)
.\.venv\Scripts\python -m pytest -m browser tests\browser        # real UI in Edge/Chrome (needs: pip install playwright)
.\.venv\Scripts\python -m pytest -m hardware -s tests\hardware   # real phone, USB debugging authorized
.\.venv\Scripts\python -m fixmytech.domain.question_eval         # question-strategy simulation
.\.venv\Scripts\python -m ruff check src tests scripts
.\.venv\Scripts\python -m mypy
.\.venv\Scripts\python -m pip_audit -r requirements.lock.txt
```

## Suite (256 tests: 250 default + 5 browser + 1 hardware)

| Area | Files | Covers |
|---|---|---|
| ADB parsing | `unit/test_parsers.py` | every parser on recorded output incl. sensors, diskstats, verified boot |
| Agent safety & collection | `unit/test_agent_collectors.py`, `unit/test_agent_actions.py` | read-only allowlist; destructive/injection commands refused; action allowlist and argument validation; full scan; denied/timeout/unsupported checks yield no values; disconnect mid-scan; connector test pass/dropouts/ADB loss/fail/inconclusive; bug-report counting, file deletion, no personal data returned |
| Agent API | `integration/test_agent_api.py` | token, DNS-rebinding Host, foreign Origin, CORS+PNA preflight, missing ADB, device states, consent, scan/job lifecycles, phone-tests action |
| Knowledge base | `unit/test_knowledge_and_conditions.py` | cross-reference validation, symptom-only rules capped, condition semantics |
| Diagnosis | `unit/test_diagnosis.py` | insufficient evidence, provenance, de-duplication, contradictions, safety notes, failed checks ≠ health, stale answers |
| Questions | `unit/test_questionnaire.py`, `unit/test_question_strategies.py` | visibility, never repeats, budget, validation, safety and inspection stops, three strategies, simulation regression guard |
| Guided tests | `integration/test_guided_tests.py` | recommendations, provenance (user_observed / phone_measured / agent_measured), not_run never evidence, validation, phone-token scope/expiry/revocation, report contents |
| Pricing | `unit/test_pricing.py`, `unit/test_repair.py` | Indian number parsing, parsers on saved Samsung excerpts, site-change detection, rejections, merge (added/unchanged/updated/not_seen/duplicates), model matching (exact/name/variant/none), scopes never mixed, parts-only, inspection fee, policy gating, freshness, official links, ingestion from files/HTTP, robots disallow, bounded retries, manual import rules, quote comparison, demo gating, production config rules |
| Decisions | `unit/test_decision.py` | formulas, fees once, separate time bases, verdicts, drivers, objectives, plain-language text, input warnings, no profit promise |
| FlipValue | `unit/test_flipvalue_client.py`, `integration/test_backend_api.py` | auth, all error mappings, retries/Retry-After, timeouts, invalid responses, mock refused in live, contract test against mock |
| Backend & security | `integration/test_backend_api.py`, `integration/test_security.py`, `unit/test_migrations.py` | access control, scan validation, quotes, source-attributed estimates, `insufficient_pricing_data`, demo labelling, headers, limits, readiness, DB failure → 503, retention, hashed tokens, backup script, migrations (fresh and pre-migration DB) |
| End to end | `e2e/test_customer_journey.py` | USB journey (agent + backend + mock FlipValue) and manual journey |
| Browser | `browser/test_ui.py` | static pages, unknown-session recovery, agent-not-running state, manual journey with source-attributed price, inline validation, USB scan, phone test page, invalid phone link |
| Hardware | `hardware/test_real_device.py` | real `adb` + real phone; skips with a reason otherwise |

Deterministic inputs: recorded ADB output (`tests/fixtures/adb/`), saved Samsung India page excerpts
(`tests/fixtures/pricing/`, retrieved 2026-10-19), in-process MOCK FlipValue. A passing parser test does **not** mean a
scraper is live or a price is current; live status is in `/api/v1/pricing/status` and the ingestion reports.

## Results on 2026-10-19 (Windows 11, Python 3.13.5)

* `pytest -q`: **250 passed**, 6 deselected. `pytest -m browser`: **5 passed** (Edge). `pytest -m hardware`: **skipped**
  (no authorized device connected).
* ruff: clean. mypy (51 files): clean. pip-audit: see IMPLEMENTATION_STATUS.md.
* Live price ingestion from Samsung India: 3 successful runs (06:26, 07:06, 07:07 UTC).
* Manual browser verification (Playwright, Edge, scripts outside the repo): full USB journey with bug report, phone test
  page on an emulated touch phone with fake camera/microphone (colours, touch grid with a simulated dead zone,
  microphone, camera, speaker), manual and connector tests, diagnosis, evidence-backed repair prices, decision. No
  console or page errors. All 12 routes fit a 390 px screen. Bugs found and fixed this way: a hidden overlay that
  intercepted taps on the phone page, a stale phone link after re-opening, long version strings widening the page,
  "parts" wrongly listed as excluded, and inconsistent quote validation.
* Real agent + real ADB 35.0.2 without a phone (from v0.1): ADB found, empty device list, token/origin rejections.

## Not verified

* **Any physical phone** (ADB checks, connector test, bug report, `adb reverse` + opening the page, real
  camera/microphone/touch hardware).
* The real FlipValue API.
* Diagnostic accuracy on real repair cases.
* The Docker image (Docker daemon not running in this environment).
