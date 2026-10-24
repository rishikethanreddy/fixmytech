# FlipValue integration (FixMyTech side)

The interface itself is specified in [INTEGRATION_CONTRACT.md](../INTEGRATION_CONTRACT.md). This page covers how
FixMyTech is configured and behaves.

## Which service answers valuations (v0.4.0)

Resale values are **optional** for the customer, and they only ever come from a real service. The server picks the
client in this order (`server/partner.py::resolve_client`):

1. the API key and address **saved on the Settings page** (origin `settings`);
2. the `FLIPVALUE_*` environment variables with `FLIPVALUE_MODE=live` (origin `environment`);
3. the development MOCK, only when `FLIPVALUE_MODE=mock` **and** `FIXMYTECH_ALLOW_MOCK_VALUATION=true` (never in
   production);
4. otherwise nothing: the valuation endpoint returns 409 `valuation_not_configured`, the Resale step says "add your
   FlipValue API key in Settings", and **no numbers are shown or invented**.

| `FLIPVALUE_MODE` | Behaviour |
|---|---|
| `disabled` (default) | No environment service. Works with a key saved in Settings |
| `mock` | Development only, and only with `FIXMYTECH_ALLOW_MOCK_VALUATION=true`; labelled MOCK everywhere. Refused in production |
| `live` | Real partner API from the environment; any response with `"mock": true` is rejected |

A key saved in Settings is always treated as `live`, so a service that identifies itself as a mock is refused
(`flipvalue_invalid_response`).

## Settings page (partner key)

Endpoints (`/api/v1/settings/partner`): `GET` status, `PUT` save, `DELETE` remove, `POST /test`.

* **Storage:** the key and address are written server-side to `FIXMYTECH_PARTNER_CONFIG_PATH`
  (default `var/partner_service.json`, owner-only permissions where the OS supports it). **It is not encrypted at rest**:
  a local app has no key management. In production prefer `FLIPVALUE_API_KEY` from a secret store; the Settings page
  then only shows that the service is configured from the environment.
* **Never exposed:** the API returns only `configured`, the address and the key's last four characters. The key is
  never logged, never in reports, `/config` or `/health`, and is not kept in the browser.
* **Who may change it:** in development, only clients on the same computer (loopback). With
  `FIXMYTECH_ADMIN_TOKEN` set, any client that sends it in `X-Admin-Token`. **In production the token is required**
  (without it the endpoints answer 403). Requests are rate limited (20/hour per client).
* **Address checks (SSRF guard):** `https://` required (`http://` only for localhost in development); no credentials,
  query or fragment in the address; in production no localhost, private, link-local or `.internal`/`.local` hosts,
  checked again after DNS resolution. A DNS-rebinding race between this check and a later request is not fully
  excluded; run the service on a network that cannot reach internal addresses if that matters.
* **Test connection** sends a fixed, non-personal sample request. "Connected" means the key was accepted.

## Configuration

```dotenv
FLIPVALUE_MODE=live
FLIPVALUE_BASE_URL=https://<flipvalue host>
FLIPVALUE_API_KEY=<secret from the FlipValue maintainer>
FLIPVALUE_TIMEOUT_S=8
FLIPVALUE_MAX_RETRIES=2
FLIPVALUE_API_VERSION=1
```

The key is read server-side only (`SecretStr`), sent only in the `X-API-Key` header, and never logged, returned by
`/health`, stored in reports or sent to the browser.

## What FixMyTech sends

Built in `server/services.py::build_valuation_request` from the session:

* `device`: manufacturer/model from the scan identity or customer input; storage and age from the form or session.
* `region`: session market; `currency`: the single currency of that region in the price data (USD for US, INR for IN).
* `condition.grade`: chosen by the customer (suggested `faulty` if a hardware fault has moderate/strong support).
* `condition.faults`: fault ids with moderate/strong support.
* `repair_scenario.repair_type`: chosen by the customer; defaults to the top hardware candidate's repair.

## What FixMyTech stores

`session.valuation` = `{ok, source: flipvalue_live|flipvalue_mock, requested_at, request_id, attempts, latency_ms,
request, response}` on success, or `{ok: false, error_code, requested_at, source, request}` on failure.
**No values are ever stored or displayed for a failed call**, and there is no fallback estimate.

## Failure handling

Diagnosis, repair estimates and the decision engine keep working without FlipValue. The decision step then shows
`incomplete` comparisons listing the missing values, or uses values the customer enters (labelled "Entered by you").
See the error mapping in [API.md](API.md).

## Connecting the real service (checklist)

1. Agree the contract (resolve §9 open questions in INTEGRATION_CONTRACT.md); update `schemas.py`, the contract doc
   and `scripts/export_openapi.py` output together if anything changes.
2. Obtain a key and a staging URL; put them in `.env` (never in git).
3. `FLIPVALUE_MODE=live`, start the backend, `GET /api/v1/health` → `flipvalue.mode = live`, `configured = true`.
4. Run a valuation from the UI; check the report shows "Live FlipValue", sensible values and `valuation_id`.
5. Exercise failures against staging: wrong key (expect `flipvalue_auth_failed`), unknown model, rate limit.
6. Record the results in IMPLEMENTATION_STATUS.md.
