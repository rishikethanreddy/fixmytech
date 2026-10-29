# FixMyTech

Smartphone diagnosis, repair-cost estimation and repair-vs-sell-vs-replace decisions for Android phones.

FixMyTech combines **read-only readings from a USB-connected Android phone** (through a local agent and ADB),
**hands-on hardware tests** run in the phone's own browser, **adaptive symptom questions**, a **transparent rule-based
diagnostic engine**, **official, source-attributed repair prices**, **resale values from the independent FlipValue
service**, and a **transparent financial comparison**. Every item in a report is labelled with where it came from:
measured on the device, measured in a test, observed by the customer, an official price (with link and date), live
FlipValue, or MOCK/DEMO.

> Status (v0.4.0, 2026-10-21): functional, production-oriented MVP. Verified with recorded ADB output, a real browser
> (including the phone test page on an emulated phone), live retrieval of Samsung India's official repair prices, and a
> MOCK FlipValue. **Not yet validated on a physical phone or against the real FlipValue API; Samsung price display in
> production needs legal approval.** See [PROJECT_AUDIT.md](PROJECT_AUDIT.md).

## What is in the box

| Component | Where | What it does |
|---|---|---|
| Backend + web UI | `src/fixmytech/server`, `frontend/` | FastAPI API under `/api/v1`, static SPA at `/` |
| Local diagnostic agent | `src/fixmytech/agent` | Runs on the customer's PC (127.0.0.1); 15 allowlisted read-only checks, opt-in bug-report summary, connector test, opens the phone test page |
| Diagnostic engine | `src/fixmytech/domain` | Facts with provenance, 113 rules, 25 adaptive questions, 12 guided tests, evidence fusion, decision engine |
| Repair price intelligence | `src/fixmytech/pricing` | Source registry & usage policies, robots-aware ingestion, Samsung India collectors, evidence-backed estimates |
| Phone test page | `frontend/phone.html` | Touch grid, colours, speaker, microphone, cameras, vibration, sensors in the phone's browser |
| Knowledge base | `src/fixmytech/data/*.json` | Fault taxonomy, rules and questions as data (validated at startup) |
| FlipValue client + MOCK | `src/fixmytech/integrations/flipvalue` | Authenticated client with bounded retries, market-price lookups (`market.py`); mock server for development only |
| Optional ML | `src/fixmytech/ml` | Training/evaluation pipeline; **disabled and unvalidated** (no real labelled data exists) |

## Setup (Windows, VS Code)

Requirements: Python 3.11+ (tested with 3.13.5), and for USB diagnosis
[Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) (`adb`).

Open the folder in VS Code, then in the PowerShell terminal:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.lock.txt
.\.venv\Scripts\python -m pip install -e ".[ml,dev]"
Copy-Item .env.example .env     # optional; defaults work for local development

# Repair prices: fetch official price lists (polite, robots-checked; a few requests). Re-run weekly.
$env:FIXMYTECH_PRICING_CONTACT="you@example.com"; .\.venv\Scripts\python -m fixmytech.pricing.ingest
```

Without this step every repair shows "no reliable price" (by design: nothing is invented). See
[docs/PRICING_SOURCES.md](docs/PRICING_SOURCES.md) for sources, terms and the production approval gate.

In VS Code choose *Python: Select Interpreter* > `.venv\Scripts\python.exe`.

## Run

Easiest: start the backend and the agent in two windows (add `-Mock` for the development mock FlipValue):

```powershell
.\scripts\run-dev.ps1              # web app on http://127.0.0.1:8000
.\scripts\run-dev.ps1 -Port 8010   # if port 8000 is already used
.\scripts\run-dev.ps1 -Mock        # also start the MOCK FlipValue (fake numbers, development only)
```

Or individually:

```powershell
# 1. MOCK FlipValue (development only - fake numbers, clearly labelled in the UI)
$env:MOCK_FLIPVALUE_API_KEY="mock-dev-key"; .\.venv\Scripts\python -m fixmytech.integrations.flipvalue.mock_server

# 2. Backend + web UI  ->  http://127.0.0.1:8000
$env:FLIPVALUE_MODE="mock"; $env:FIXMYTECH_ALLOW_MOCK_VALUATION="true"; $env:FLIPVALUE_BASE_URL="http://127.0.0.1:8090"; $env:FLIPVALUE_API_KEY="mock-dev-key"
# (without the mock, skip these variables and add a real key on the Settings page instead)
.\.venv\Scripts\python -m fixmytech.server --port 8000

# 3. Local agent (on the computer the phone is plugged into). Prints + opens a pairing link.
.\.venv\Scripts\python -m fixmytech.agent --web-url http://127.0.0.1:8000
```

API docs (development): http://127.0.0.1:8000/api/docs · Health: `/api/v1/health` · Readiness: `/api/v1/ready`

Deployment (TLS proxy, Docker, backups, production checklist): [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

To use the real FlipValue API set `FLIPVALUE_MODE=live`, `FLIPVALUE_BASE_URL=https://...` and
`FLIPVALUE_API_KEY` in `.env` (see [docs/FLIPVALUE_INTEGRATION.md](docs/FLIPVALUE_INTEGRATION.md)).

## Test

```powershell
.\scripts\check.ps1                                      # ruff, mypy, pytest, KB validation, OpenAPI export, pip-audit
.\.venv\Scripts\python -m pytest -q                      # 250 tests, no phone, browser or network needed
.\.venv\Scripts\python -m pip install playwright; .\.venv\Scripts\python -m pytest -m browser tests\browser
.\.venv\Scripts\python -m pytest -m hardware -s tests\hardware   # needs a real, authorized Android phone
.\.venv\Scripts\python -m fixmytech.domain.question_eval   # question-strategy simulation
```

See [docs/TESTING.md](docs/TESTING.md).

## Reproducibility

Everything needed to rebuild and re-verify the project is in this repository.

| What | How it is pinned / reproduced |
|---|---|
| Python | 3.11+ (`pyproject.toml`); developed and tested on 3.13.5, Windows 11 |
| Dependencies | `requirements.lock.txt` (exact versions, used by the Dockerfile too) plus extras `ml`, `dev`, `browser` in `pyproject.toml` |
| Configuration | every setting is an environment variable with a default; see `.env.example` (copy to `.env`; never commit it) |
| Database | SQLite at `var/fixmytech.db`, created and migrated automatically on first start |
| Knowledge base | JSON in `src/fixmytech/data/`, validated at startup and by tests |
| Repair prices | not committed as live data: regenerate with `python -m fixmytech.pricing.ingest`; `pricing_seed/` is the snapshot baked into the Docker image |
| Resale values | the independent FlipValue service (set `FLIPVALUE_*`), or the MOCK server (`-Mock`) for fake, clearly labelled numbers |
| Tests | deterministic: recorded ADB output in `tests/fixtures/adb/`, saved Samsung India pages in `tests/fixtures/pricing/`, in-process mock FlipValue |
| API contracts | `docs/fixmytech-api.openapi.json` is regenerated by `python scripts/export_openapi.py`; the partner contract is `docs/flipvalue-contract.openapi.json` |
| How it was built | the prompts given to the AI coding assistant are logged in [.github/PROMPTS.md](.github/PROMPTS.md) |

Clean-room check (fresh clone, no phone, no network, no keys needed):

```powershell
git clone <repo-url> FixMyTech; cd FixMyTech
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock.txt
.\.venv\Scripts\python -m pip install -e ".[ml,dev]"
.\.venv\Scripts\python -m pytest -q          # default suite must pass
.\scripts\run-dev.ps1 -Mock                  # full stack with the MOCK FlipValue -> http://127.0.0.1:8000
```

macOS/Linux: the backend and tests are cross-platform (use `.venv/bin/python`); the `.ps1`/`.cmd` helpers are Windows-only,
so start the three processes with the commands under **Run**.

Not reproducible from the repository alone: live Samsung prices (change over time), the real FlipValue API (needs a key
from its owner), physical-phone results, and the presentation files (`*.pptx`, binary and not part of the build).

### Hosting (optional)

Backend container: `docker build -t fixmytech .` then run with `FIXMYTECH_ENV=production` (see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)). Google Cloud Run + Firebase Hosting setup, with the exact commands, is in
[docs/FIREBASE.md](docs/FIREBASE.md). Production requires `FIXMYTECH_ADMIN_TOKEN` and an explicit
`FIXMYTECH_PRICING_ALLOWED_SOURCES`. The local agent always runs on the customer's own PC, never in the cloud.

## Troubleshooting

| Problem | Fix |
|---|---|
| `error while attempting to bind on address ... 8000` | Another app uses port 8000. Use `--port 8010` (and `--web-url http://127.0.0.1:8010` for the agent). |
| UI says "Agent not detected" | Start the agent; keep its window open. Check the agent address under Settings. |
| UI asks to pair | Open the pairing link printed by the agent, or paste it into the Connect page. Pairing is per browser tab. |
| Device "unauthorized" | Unlock the phone and accept *Allow USB debugging?*; or revoke authorizations in Developer options and reconnect. |
| Device "offline" / not listed | Different cable (data, not charge-only) or port; install the OEM USB driver; close other ADB tools. |
| ADB not found | Install Platform-Tools, add to PATH, or start the agent with `--adb-path C:\path\adb.exe`. |
| Resale step says a key is needed | Add the FlipValue API key on the Settings page (or `FLIPVALUE_*` in `.env`). Without it no resale numbers are shown; you can skip the step or enter values manually in the Decision step. |
| Valuation fails | Diagnosis still works; enter resale values manually in the Decision step. Use *Test connection* on the Settings page. |
| "No reliable price" everywhere | Run the price ingestion (Setup). Only Samsung India has automated official data so far. |
| Camera/microphone tests unavailable on the phone | Use "Open on my phone (USB)" (secure `localhost` link) or serve the site over https. |

## Documentation

- [PROJECT_AUDIT.md](PROJECT_AUDIT.md), [.github/PROMPTS.md](.github/PROMPTS.md) (every prompt used to build this project)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/API.md](docs/API.md), [docs/DIAGNOSTICS.md](docs/DIAGNOSTICS.md),
  [docs/DIAGNOSTIC_LIMITATIONS.md](docs/DIAGNOSTIC_LIMITATIONS.md)
- [docs/PRICING_SOURCES.md](docs/PRICING_SOURCES.md), [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- [docs/SECURITY.md](docs/SECURITY.md), [docs/PRIVACY.md](docs/PRIVACY.md), [docs/TESTING.md](docs/TESTING.md),
  [docs/DATA_AND_MODEL_CARD.md](docs/DATA_AND_MODEL_CARD.md)
- FlipValue: [docs/FLIPVALUE_INTEGRATION.md](docs/FLIPVALUE_INTEGRATION.md), [docs/flipvalue-contract.openapi.json](docs/flipvalue-contract.openapi.json)
- Firebase / Cloud Run hosting: [docs/FIREBASE.md](docs/FIREBASE.md)
