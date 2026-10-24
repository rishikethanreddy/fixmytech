# Project audit

**Date:** 2026-10-19 · **Auditor:** Claude Code session that built v0.1.0

## Starting point

The workspace `C:\Users\palar\Downloads\FixMyTech` was **empty**: no source, README, dependency files, tests or git
repository. There was no existing framework, database, functionality, duplicated logic or security debt to preserve,
so the project was established from scratch.

## Environment found

| Tool | Status |
|---|---|
| OS | Windows 11 Home 10.0.26300 |
| Python | 3.13.5 (also `py` launcher) |
| ADB | 1.0.41 / platform-tools 35.0.2 at `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`, on PATH |
| Git | 2.51.0 |
| Node | 22.19.0 (used only to syntax-check frontend modules) |
| Browsers | Microsoft Edge, Google Chrome (used headless via Playwright for UI verification) |
| Connected Android devices | **None** (`adb devices` empty) |
| Port 8000 | **Occupied by another local project** (`Downloads\NLP` uvicorn). Left untouched; FixMyTech was tested on 8010 |
| FlipValue | No URL, credentials or partner contract available |
| Labelled repair data / verified repair prices | Not available |

No system-wide changes were made. Dependencies are installed in the project-local `.venv`.

## Decisions taken from the audit

| Finding | Decision |
|---|---|
| Empty repo | Build fresh: Python/FastAPI backend + local agent + no-build SPA (see docs/ARCHITECTURE.md) |
| No phone | Parsers developed against documented formats and recorded samples; hardware test written and marked; everything else verified with fixtures and the real ADB binary |
| No FlipValue access | Client implemented to a written, *proposed* contract; MOCK server for development; handoff documents for the partner |
| No labelled data | Rule-based engine is the product; ML pipeline exists but is disabled and labelled unvalidated |
| No verified price data | Bundled prices are explicitly illustrative; customer quotes supported; real data pluggable via config |
| Port conflict | All commands take `--port`; docs and `run-dev.ps1` explain |

## Risks identified

1. Parser robustness across manufacturers is unproven until tested on real phones (highest technical risk).
2. Rule weights are expert-set; without outcome data, ranking quality is unknown.
3. Contract mismatch with FlipValue (model identification, price semantics) could require client changes.
4. Illustrative repair prices could be mistaken for real ones; mitigated by labelling at every layer.

---

# Second audit: production transformation (v0.1 → v0.2, 2026-10-19)

Audit of the v0.1 code (written in an earlier session, reviewed critically). Findings and what was done:

| Area | Finding in v0.1 | Severity | Action in v0.2 |
|---|---|---|---|
| Repair prices | Every price was a generic `ILLUSTRATIVE_PLACEHOLDER` range, shown in normal results with a warning | Critical (trust) | Source-attributed pricing subsystem; Samsung India official prices ingested; generic ranges only as labelled, opt-in DEMO data refused in production; `insufficient_pricing_data` otherwise |
| Repair prices | No source URL, retrieval date, scope (parts vs labour vs tax) or model match | Critical | Evidence + normalized record model; scopes never mixed; exact/variant model matching; freshness |
| Diagnostics | No way to test display, touch, audio, cameras, sensors | High | 12 guided tests: phone-browser (measured + customer-observed), agent connector test, manual tests; provenance per result |
| Diagnostics | Read-only scan missed sensors, storage I/O and software integrity | Medium | 3 new checks; opt-in, consented bug-report summary (file deleted after counting) |
| Diagnostics | Note-only findings impossible; no next-step or uncertainty statement | Medium | Note-only rules, ranking basis, suggested confirmation tests, single recommended next step, remaining-uncertainty text |
| Questions | Adaptive selection never compared with a baseline; no safety/inspection stop | Medium | Fixed-order baseline + entropy strategy + simulation harness; safety and professional-inspection stops; simulation exposed an under-weighted storage rule (fixed) |
| Decisions | No plain-language walkthrough, no input sanity checks, possible "profit" reading | Medium | Plain-language explanation, input warnings, explicit no-guarantee caveat |
| Persistence | `create_all` only; no way to evolve the schema | Medium | Versioned migrations with upgrade of pre-migration databases |
| Operations | No readiness check, DB errors became 500s, no backups, no deployment docs | Medium | `/ready`, 503 `database_unavailable`, JSON logs, backup script, Dockerfile, DEPLOYMENT.md |
| UI | Placeholder price cards; tests absent; several layout bugs on mobile | Medium | Evidence-backed price cards, Questions & tests step, bug-report card; browser-verified at 390 px |
| FlipValue | Contract still proposed; no partner contact | External | Interface unchanged; documents refreshed |

Source review for pricing (robots.txt and terms) is recorded in docs/PRICING_SOURCES.md. Motorola (robots disallow) and
Apple (terms prohibit automated and equivalent manual copying) were deliberately **not** collected.
