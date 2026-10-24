# Deployment

## Topology

* **Backend + web UI**: one FastAPI process (`uvicorn fixmytech.server.app:create_app --factory`) behind a reverse
  proxy that terminates TLS (Caddy, nginx, a cloud load balancer). Single instance with SQLite is appropriate for
  the MVP; for several instances move to PostgreSQL (`FIXMYTECH_DATABASE_URL=postgresql+psycopg://...`, install
  `psycopg`; **not yet tested** with FixMyTech) and a shared rate-limit store.
* **Local agent**: runs on each customer's computer, never on the server. Distribute it with Python + `pip install
  fixmytech` or as a packaged executable (not yet built). Customers start it with
  `python -m fixmytech.agent --web-url https://<your host>`.
* **Price ingestion**: a scheduled job (`python -m fixmytech.pricing.ingest`) writing to the shared
  `FIXMYTECH_PRICING_DIR`; the backend reloads the dataset automatically when the file changes.

## Production checklist

1. `FIXMYTECH_ENV=production`, HTTPS only, `FIXMYTECH_TRUST_PROXY_HEADERS=true` only behind your proxy.
2. `FIXMYTECH_PRICING_ALLOWED_SOURCES` set explicitly after legal review (see PRICING_SOURCES.md); empty is valid and
   means "show no prices".
3. `FIXMYTECH_PRICING_CONTACT` set; schedule ingestion weekly; alert when `/api/v1/ready` lists failed sources.
4. FlipValue: `FLIPVALUE_MODE=live`, `FLIPVALUE_BASE_URL=https://...`, `FLIPVALUE_API_KEY` from a secret store. To let an
   operator add the key from the Settings page instead, set `FIXMYTECH_ADMIN_TOKEN` (required in production; the key is
   then stored unencrypted in `FIXMYTECH_PARTNER_CONFIG_PATH`, so protect that volume). `FIXMYTECH_ALLOW_MOCK_VALUATION`
   must stay unset.
5. Persist `var/` (database, pricing data) on a volume; back up with `python scripts/backup_db.py` (SQLite) or managed
   PostgreSQL backups; restrict backup access and delete backups per the retention policy.
6. `FIXMYTECH_LOG_FORMAT=json`; ship logs; monitor `/api/v1/health` (liveness) and `/api/v1/ready` (readiness).
7. Configure hosting access-log retention to match docs/PRIVACY.md.

## Docker

```bash
docker build -t fixmytech .
docker run -d -p 8000:8000 -v fixmytech-var:/app/var \
  -e FIXMYTECH_PRICING_ALLOWED_SOURCES= -e FLIPVALUE_MODE=disabled fixmytech
docker exec <container> python -m fixmytech.pricing.ingest   # first price import
```

The Dockerfile was written but **has not been built** in the authoring environment (Docker daemon not running).

## Restore

Stop the server, copy a backup over `var/fixmytech.db` (or `pg_restore`), start the server; schema migrations run at
startup and are recorded in `schema_migrations`.

## Upgrades

Migrations are applied automatically on start (`server/db.py`). Never edit a released migration; append a new one.
