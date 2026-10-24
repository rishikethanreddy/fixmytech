# Implementation plan

Status key: ✅ done and tested · 🟡 done, verified only with fixtures/mocks/emulation · ⛔ blocked externally

## v0.1 milestones (first session)

| # | Milestone | Status |
|---|---|---|
| 1-10 | Audit, backend, agent, hybrid diagnosis, repair estimation, FlipValue client, decisions, frontend, hardening, handoff | ✅ (see v0.2 for what was superseded) |

## v0.2 production transformation (this session)

| Priority | Work | Status | Notes |
|---|---|---|---|
| 1 Trust | Source-attributed pricing model, ingestion, Samsung India collectors, estimates, UI | ✅ | Live retrieval verified 3×; production display ⛔ pending legal approval for Samsung data |
| 1 Trust | Remove placeholder prices from normal results; demo only when enabled; production refuses demo | ✅ | |
| 2 Diagnosis | 3 new ADB checks, opt-in bug-report summary | 🟡 | fixtures only; ⛔ needs a physical phone |
| 2 Diagnosis | Guided tests: phone page (8), connector test, manual (3); provenance; rules | 🟡 | browser-verified on an emulated phone with fake camera/mic; ⛔ real phone |
| 2 Diagnosis | Question strategies, baseline comparison, safety/inspection stops | ✅ | simulation only (no real cases) |
| 2 Diagnosis | Next step, uncertainty, ranking basis, suggested tests | ✅ | |
| 3 Money | Plain-language decision, input warnings, no-profit caveat, tax caveat | ✅ | |
| 4 UX | Questions & tests step, price cards, bug-report card, mobile fixes, inline validation | ✅ | browser tests committed |
| 5 Hardening | Migrations, readiness, DB-failure 503, JSON logs, backups, Dockerfile, deployment docs | 🟡 | Docker image ⛔ not built (daemon not running) |
| 6 Partner | Contract re-verified unchanged; docs refreshed | ✅ / ⛔ | real FlipValue API not available |

## Next work, in priority order

1. **Physical-phone validation** on 3-5 Android phones (OEMs, Android 10-15): `pytest -m hardware -s`, a full UI scan,
   the connector test, the bug report, and "Open on my phone" (camera/mic/touch on real hardware). Fix parser gaps;
   add redacted outputs as fixtures.
2. **Legal approval** (or Samsung's permission) for displaying Samsung India prices; then set
   `FIXMYTECH_PRICING_ALLOWED_SOURCES` in production.
3. **More price sources** with acceptable terms (OnePlus India after terms review; authorized providers; independent
   shops with published prices); a manual curation workflow for Motorola.
4. **FlipValue**: agree the contract, connect staging (`FLIPVALUE_MODE=live`).
5. Technician-feedback endpoint to record confirmed outcomes (prerequisite for calibration or ML).
6. Build and test the Docker image; test PostgreSQL; shared rate-limit store; packaged agent (PyInstaller).
7. Accessibility audit with assistive technology (keyboard and screen reader) beyond the current structural checks.
