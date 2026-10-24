# API reference

Machine-readable: [`fixmytech-api.openapi.json`](fixmytech-api.openapi.json) (regenerate with
`python scripts/export_openapi.py`); interactive docs at `/api/docs` outside production.

## Conventions

* Base path `/api/v1`, JSON only.
* Session endpoints require `Authorization: Bearer <access_token>` (returned once by `POST /sessions`).
  Unknown session ids and wrong tokens both return **404** (no probing); a missing token returns 401.
* Errors: `{"error": {"code": "...", "message": "...", "request_id": "...", "details": ...}}`.
* Every response has `X-Request-ID`; `/api` responses are `Cache-Control: no-store`.
* Limits: body ≤ 256 KB (413), rate limit per IP (429 + `Retry-After`), valuation calls per session per hour (429).

## Backend endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness: status, DB, knowledge-base version, FlipValue mode (never secrets) |
| GET | `/ready` | Readiness: DB reachable, schema current, pricing records loaded/failed sources, FlipValue configured (503 if not ready) |
| GET | `/pricing/status` | Price sources, usage policies, last success/error, record counts |
| GET | `/config` | Public UI config: agent URL, regions/currencies, retention, flags |
| GET | `/catalog/checks` | Capability registry + not-assessable list |
| GET | `/catalog/faults` | Fault taxonomy + repair types |
| POST | `/sessions` | `{mode: usb|manual, device, consent: {diagnostic_processing: true, store_report}}` → 201 `{session_id, access_token, expires_at}` |
| GET | `/sessions/{id}` | Summary |
| PATCH | `/sessions/{id}/device` | Update device details (clears stored valuation) |
| DELETE | `/sessions/{id}` | Delete now (204) |
| PUT | `/sessions/{id}/scan` | Store agent `ScanResult` (idempotent per `scan_id`; extra fields rejected) |
| GET | `/sessions/{id}/observations` | Observations with titles, quality, privacy notes |
| GET | `/sessions/{id}/questions/next` | `{question|null, stop_reason, asked, budget, utility, why}` |
| GET | `/sessions/{id}/answers` | Answers with question text and option labels |
| PUT | `/sessions/{id}/answers/{qid}` | `{value: str | [str]}` → next question (422 `invalid_answer`) |
| DELETE | `/sessions/{id}/answers/{qid}` | Remove answer ("back") → next question |
| GET | `/sessions/{id}/diagnosis` | Ranked candidates, evidence, missing evidence, notes, next actions |
| GET | `/sessions/{id}/repair-estimate` | Estimates per plausible fault, overall range, quote comparisons |
| POST / DELETE | `/sessions/{id}/quotes[/{index}]` | Add/remove a customer quote (max 10) |
| GET | `/sessions/{id}/valuation` | Stored valuation outcome + suggested condition |
| POST | `/sessions/{id}/valuation` | `{condition_grade, repair_type?, storage_gb?, age_months?}` → calls FlipValue |
| POST | `/sessions/{id}/decision` | Decision inputs → scenarios, comparisons, recommendation |
| GET | `/sessions/{id}/report` | Everything above in one document (incl. guided tests) |
| PUT | `/sessions/{id}/observations/{check_id}` | Add an opt-in observation (bug-report summary) to the stored scan |
| GET | `/sessions/{id}/tests` | Test catalog with results, recommendations and availability |
| PUT / DELETE | `/sessions/{id}/tests/{test_id}` | Manual result or `not_run` (phone-browser results must come from the phone) |
| POST | `/sessions/{id}/tests/{test_id}/agent-result` | Agent-assisted result relayed from the agent (USB sessions only) |
| POST / DELETE | `/sessions/{id}/phone-link` | Create (30 min) / revoke the phone test token |
| GET | `/phone/{token}` | Phone page: device label and phone-browser tests (token-scoped) |
| PUT | `/phone/{token}/tests/{test_id}` | Phone page submits a phone-browser test result |

The repair estimate now returns per repair: `status` (`estimated` / `parts_only` / `insufficient_pricing_data`),
`data_kind`, `low`/`high`, `currency`, `scope`, `tax`, `usable_for_decision`, `model_match`, `matched_model`,
`freshness`, `last_verified_at`, `evidence[]` (source URL, organization, retrieved/published dates, raw label and
value), `exclusions`, `limitations`, `notes`, `official_links`. Decision results add `plain_language` and
`input_warnings`. Database failures return 503 `database_unavailable`.

FlipValue failure mapping: `flipvalue_disabled`/`flipvalue_misconfigured`/`flipvalue_rate_limited` → 503,
`flipvalue_timeout` → 504, `flipvalue_auth_failed`/`flipvalue_unavailable`/`flipvalue_invalid_response` → 502,
`flipvalue_unsupported_device`/`flipvalue_rejected_request` → 422. Details always include
`diagnosis_still_available: true`.

## Local agent endpoints (`http://127.0.0.1:8765/agent/v1`)

Require `X-Agent-Token` (except `/health`), a loopback `Host`, and (if present) an allowlisted `Origin`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness, version |
| GET | `/session` | Validate the pairing token |
| GET | `/adb` | `{installed, server_running, version, error}` |
| GET | `/devices` | `[{serial, state, model, ready, help}]` (states: device, unauthorized, offline, no_permissions, recovery, ...) |
| GET | `/devices/{serial}/summary` | Identity observation + expected check support + not-assessable list |
| GET | `/capabilities` | Capability registry |
| POST | `/scans` | `{serial, consent: true, checks?}` → 202 `{scan_id}` (409 if a scan is running or device not ready) |
| GET | `/scans/{scan_id}` | `{state: running|done|failed, done, total, current_title, error, result: ScanResult}` |
| POST | `/devices/{serial}/connector-test` | `{consent: true, duration_s?}` → 202 `{job_id}` |
| POST | `/devices/{serial}/bugreport` | `{consent: true}` → 202 `{job_id}`; result is a `bugreport.summary` observation |
| GET | `/jobs/{job_id}` | `{state, stage, done, total, error, result}` |
| POST / DELETE | `/devices/{serial}/phone-tests` | `{consent: true, web_port, token}`: `adb reverse` + open test page / remove forward |
