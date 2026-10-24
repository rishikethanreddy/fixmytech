# Hosting on Firebase

Firebase Hosting serves static files only, and FixMyTech has a Python (FastAPI) backend. The standard setup is:

```
browser -> Firebase Hosting (https, your *.web.app domain) -> Cloud Run service "fixmytech" (this repo's Dockerfile)
```

`firebase.json` forwards every request to the Cloud Run service `fixmytech` in `asia-south1` (change the region to match
your service). The backend also serves the web pages, so the pages and the API share one origin (no CORS settings).
Cloud Run needs the **Blaze (pay-as-you-go)** plan; the free monthly allowance is usually enough for testing.

## One-time setup

1. Create/choose a Firebase project and note its **project ID**. Upgrade it to Blaze.
2. Install the Google Cloud CLI (`gcloud`), or use **Cloud Shell** in the Google Cloud console (nothing to install; upload or
   clone this repository there). Then: `gcloud auth login`, `gcloud config set project <PROJECT_ID>`,
   `gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com`.
3. (Optional, for prices) stage the price data into the image: `mkdir pricing_seed; copy var\pricing\dataset.json pricing_seed\`.
   Read "Before you go public" below first.

## Deploy the backend (Cloud Run)

From the repository root:

```powershell
gcloud run deploy fixmytech --source . --region asia-south1 --allow-unauthenticated --port 8000 `
  --min-instances 1 --max-instances 1 `
  --set-env-vars "FIXMYTECH_TRUST_PROXY_HEADERS=true,FIXMYTECH_PRICING_ALLOWED_SOURCES=" `
  --set-env-vars "FIXMYTECH_ADMIN_TOKEN=<a long random string>"
```

* `--port 8000` matches the Dockerfile. One instance keeps the in-memory rate limits and the SQLite file consistent.
* `FIXMYTECH_PRICING_ALLOWED_SOURCES=` (empty) is valid and shows **no prices**. To show the collected prices list the
  source ids, e.g. `FIXMYTECH_PRICING_ALLOWED_SOURCES=samsung_in_parts,samsung_in_labour,samsung_us_screen,google_in_estimator,google_us_estimator`.
* Resale values: set `FLIPVALUE_MODE=live`, `FLIPVALUE_BASE_URL=https://...` and put the key in Secret Manager
  (`--set-secrets FLIPVALUE_API_KEY=flipvalue-key:latest`). A key typed into the Settings page is written to the
  container's disk, which Cloud Run **discards on every restart or deploy**, so use the secret for a hosted site.
* The Settings page needs `FIXMYTECH_ADMIN_TOKEN` in production (shown above); you type it once in the page.

## Connect Firebase Hosting

```powershell
firebase login
firebase use <PROJECT_ID>
firebase deploy --only hosting
```

Your site is then at `https://<PROJECT_ID>.web.app`. Check `https://<PROJECT_ID>.web.app/api/v1/health` and `/api/v1/ready`.

## Local agent with the hosted site

The agent still runs on the customer's computer. Start it pointing at the hosted address:

```powershell
.\.venv\Scripts\python -m fixmytech.agent --web-url https://<PROJECT_ID>.web.app
```

Chrome and Edge allow a secure page to talk to `http://127.0.0.1:8765` (the first time they may ask for permission);
Safari blocks it. The "Open on my phone (USB)" button is hidden on a hosted site (it forwards a *local* web server to the
phone); use "Show a link to type on the phone". The hosted address is https, so camera and microphone tests work.

## Things that are different from running locally

* **Storage is temporary.** Reports are in a SQLite file inside the container: they are lost when Cloud Run restarts or
  you deploy again (reports expire after 24 hours/30 days anyway). For lasting storage use Cloud SQL for PostgreSQL
  (`FIXMYTECH_DATABASE_URL=postgresql+psycopg://...`; the PostgreSQL path is **not yet tested** with FixMyTech) and then
  you can raise `--max-instances`, but the rate limits are per instance.
* **Price refresh** runs on your computer (`python -m fixmytech.pricing.ingest`), then re-stage `pricing_seed/` and redeploy.
* **Legal:** Samsung and Google prices are marked "legal review required" (docs/PRICING_SOURCES.md). Do not list them in
  `FIXMYTECH_PRICING_ALLOWED_SOURCES` for a public site until that is settled.

## Before you go public

Work through the "Production checklist" in docs/DEPLOYMENT.md (HTTPS only, allowed sources decided, admin token set,
mock valuation off, logs and retention). This guide has not been run against a real Firebase project.
