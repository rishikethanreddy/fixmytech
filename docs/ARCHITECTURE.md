# Architecture

## Overview

```
 Customer's Windows PC                                         FixMyTech server (local or hosted)
 ┌──────────────────────────────────────────────┐              ┌───────────────────────────────────────┐
 │ Browser: FixMyTech SPA (frontend/)            │  HTTPS/JSON  │ FastAPI  /api/v1  (src/fixmytech/server)│
 │   ├─ talks to backend  ───────────────────────┼─────────────▶│   ├─ sessions, answers, quotes (SQLite) │
 │   └─ talks to local agent (pairing token) ─┐  │              │   ├─ domain/ (pure Python)              │
 │                                            │  │              │   │   facts → rules → fusion            │
 │ Local agent 127.0.0.1:8765 ◀───────────────┘  │              │   │   questionnaire, repair, decision   │
 │ (src/fixmytech/agent)                         │              │   └─ FlipValue client ──────────────────┼──▶ FlipValue API
 │   └─ adb (fixed allowlisted read-only cmds)   │              └───────────────────────────────────────┘    (partner)
 │        └─ USB ─▶ Android phone                │
 └──────────────────────────────────────────────┘
```

**Why a local agent:** a hosted server cannot reach a USB device on the customer's computer, and a browser cannot run
ADB. The agent is a small Python service bound to 127.0.0.1. The browser relays the agent's normalized scan result to
the backend, so the backend never needs network access to the customer's machine and the agent never needs the
backend's address or credentials.

Since v0.2 there are two more pieces:

```
 Operator / scheduler ──▶ python -m fixmytech.pricing.ingest ──(polite HTTP, robots-checked)──▶ manufacturer pages
                                │ writes var/pricing/dataset.json + reports/            (Samsung India today)
                                ▼
                        backend RepairPricingService (reloads when the file changes)

 Customer's phone browser ──▶ /p/<phone token>  (phone test page; token can only submit that session's tests)
        ▲  opened via agent: adb reverse tcp:PORT + am start http://localhost:PORT/p/<token>  (secure context)
```

## Flow

1. `POST /api/v1/sessions` with consent → session id + **access token** (capability token; stored hashed).
2. USB mode: browser pairs with the agent (token delivered in the URL fragment of the pairing link), lists devices,
   gets a device summary, the customer consents to the scan, the agent runs the checks, the browser `PUT`s the
   `ScanResult` to `/sessions/{id}/scan`.
3. Questionnaire: `GET /questions/next`, `PUT /answers/{qid}` (returns the next question).
4. `GET /diagnosis` (computed on demand, deterministic from stored facts), `GET /repair-estimate`, `POST /quotes`.
5. Hands-on tests: `POST /phone-link` → phone page `/p/<token>` submits `PUT /api/v1/phone/<token>/tests/<id>`;
   manual tests `PUT /tests/<id>`; agent tests relayed with `POST /tests/<id>/agent-result`; opt-in bug-report summary
   `PUT /observations/bugreport.summary`.
6. `POST /valuation` → FlipValue client → stored outcome (success or error code; never fabricated values).
7. `POST /decision` → scenarios and comparisons; `GET /report` bundles everything.

## Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `core/observations.py` | `Observation` / `ScanResult` schema (agent ↔ backend contract) | pydantic |
| `core/capabilities.py` | Capability registry: checks, sources, evidence quality, limitations; NOT_ASSESSABLE list | core |
| `core/redaction.py` | Free-text redaction (emails, IPs, MACs, long numbers, serials, tokens, paths) | - |
| `agent/adb.py` | Safe subprocess runner, command allowlist, transport-error detection | stdlib |
| `agent/parsers.py` | Pure parsers for each command's output | stdlib |
| `agent/collectors.py` | One collector per check; scan orchestration; disconnect handling | agent, core |
| `agent/server.py` | Loopback HTTP API: Host/Origin guard, token, CORS + Private Network Access; scan and task jobs | FastAPI |
| `agent/actions.py` | Consented actions: connector test, bug-report summary (file deleted after counting) | agent |
| `domain/guided.py` | Guided-test result validation, provenance, recommendations | domain |
| `domain/question_eval.py` | Strategy comparison on personas (simulation) | domain |
| `pricing/` | Source registry & policies, robots-aware fetcher, Samsung collectors, store/merge, ingestion CLI, estimates | httpx |
| `domain/knowledge.py` | Loads + cross-validates taxonomy, rules, questions | core |
| `domain/conditions.py` | Declarative condition language | - |
| `domain/facts.py` | Facts with provenance; stale-answer handling | core |
| `domain/diagnosis.py` | Rule firing, group de-duplication, ranking, support levels, missing evidence, next actions | domain |
| `domain/questionnaire.py` | Adaptive question selection and stopping rules; answer validation | domain |
| `domain/repair.py` | Price table, estimates per plausible fault, quote comparison | domain |
| `domain/decision.py` | Scenario formulas, interval comparison, sensitivity drivers | pydantic |
| `integrations/flipvalue/` | Contract schemas, client, MOCK server | httpx |
| `ml/` | Feature schema, training/evaluation, inference (separate, never ranks) | scikit-learn (optional) |
| `server/` | Config, DB + migrations, security middleware, errors, services, routes, readiness, app factory | FastAPI, SQLAlchemy |
| `frontend/` | No-build ES-module SPA, strict CSP (no inline script/style) | - |

Domain code has no FastAPI or database imports; it is tested directly.

## Key decisions

| Decision | Rationale |
|---|---|
| Separate local agent, browser relays results | Only feasible way to reach USB from a web product without exposing the customer's machine |
| Agent token in URL fragment, stored in `sessionStorage` | Fragments are never sent to servers; tab-scoped; dies with the agent |
| No accounts; per-session capability tokens | Minimal personal data; reports unlinkable server-side. History lives in the browser |
| Rules + questions as JSON data, validated at load | New rules/questions without code changes; typos fail fast |
| Additive ranking scores with documented thresholds, no percentages | No validated probabilities exist; scores must not look like probabilities |
| One JSON-column table (SQLite) | Small MVP; portable to PostgreSQL via `FIXMYTECH_DATABASE_URL` |
| Vanilla JS SPA served by FastAPI | No build toolchain; strict CSP is easy to enforce; one origin |
| ML output never merged into ranking | No real labelled data; synthetic metrics are meaningless for accuracy |
| Price data in a file store refreshed by a job, not per request | Reference data; avoids hammering sources; reviewable; atomic swap |
| Per-source usage policy + production allowlist | Robots/terms differ per source; legal approval must be explicit |
| Browser-based hardware tests opened via `adb reverse` | No companion app to install; `localhost` is a secure context for camera/mic |
| Hand-rolled schema migrations (`schema_migrations` table) | Two columns added; Alembic would add more than it saves at this size |

## Extending

* **New check:** add a `CheckSpec` in `core/capabilities.py`, an allowlisted command in `agent/adb.py`, a parser +
  collector, fixtures and tests. The import-time assertion fails if a check lacks a collector.
* **New rule/question:** edit `data/rules.json` / `data/questions.json`; `load_knowledge_base()` validates references.
* **New device family:** compatibility is per check (statuses are reported honestly); add parsers for vendor formats.
* **iOS:** not supported. Would need a separate agent (e.g. libimobiledevice) and its own capability registry.
