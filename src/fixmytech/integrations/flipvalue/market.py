"""FlipValue market data: catalogue match, price trends, store prices and the repair-or-sell comparison.

These are the same data FlipValue shows on its own site (Trends, Compare prices, Repair or sell). Calls go through
the configured FlipValue client's base URL and API key. Each call tries the keyed ``/api/v1/...`` endpoint first and
falls back to FlipValue's public ``/api/...`` endpoint when the keyed one does not exist yet or the key lacks the
scope. Responses are untrusted: only known fields are kept, mock rows are dropped, and nothing is ever filled in when
FlipValue has no data (callers get ``FlipValueNoDataError`` and show "no data for this phone").
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from fixmytech.integrations.flipvalue.client import (
    FlipValueAuthError,
    FlipValueClient,
    FlipValueError,
    FlipValueInvalidResponseError,
    FlipValueRateLimitedError,
    FlipValueTimeoutError,
    FlipValueUnavailableError,
)

log = logging.getLogger("fixmytech.flipvalue.market")

CACHE_TTL_S = 600.0
OFFER_GROUPS = ("new", "refurbished", "used", "buyback")
MAX_OFFERS_PER_GROUP = 40
_MONTH = re.compile(r"^\d{4}-\d{2}$")
# 404/422 codes that mean "FlipValue has no data for this", as opposed to "this endpoint does not exist".
NO_DATA_CODES = {"unsupported_model", "no_data_for_condition", "variant_required", "unsupported_region",
                 "unsupported_currency", "not_found"}


class FlipValueNoDataError(FlipValueError):
    code = "flipvalue_no_data"


class _RouteMissing(Exception):
    """The endpoint is not offered by this FlipValue deployment (or not with this key's scopes)."""


_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, int | float) or v != v or v < 0:
        return None
    return float(v)


def _text(v: Any, limit: int = 200) -> str | None:
    return v[:limit] if isinstance(v, str) and v else None


def _https(v: Any) -> str | None:
    return v[:500] if isinstance(v, str) and v.startswith("https://") else None


class MarketClient:
    """Read-only market data from the FlipValue service a ``FlipValueClient`` is configured for."""

    def __init__(self, client: FlipValueClient, now: Any = time.monotonic) -> None:
        self.client = client
        self._now = now

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None) -> Any:
        s = self.client.settings
        self.client._check_config()
        headers = {"X-API-Key": s.api_key or "", "Accept": "application/json",
                   "FlipValue-API-Version": s.api_version, "User-Agent": "FixMyTech/0.5"}
        try:
            with httpx.Client(base_url=s.base_url or "", timeout=s.timeout_s, transport=self.client._transport) as http:
                resp = http.request(method, path, params=params, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise FlipValueTimeoutError("FlipValue did not respond in time") from exc
        except httpx.TransportError as exc:
            raise FlipValueUnavailableError("Could not connect to FlipValue") from exc
        try:
            data = resp.json()
        except ValueError:
            data = None
        err = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else None
        code = err.get("code") if err else None
        message = _text(err.get("message")) if err else None
        if resp.status_code == 200 or resp.status_code == 201:
            if data is None:
                raise FlipValueInvalidResponseError("FlipValue returned a response that is not JSON", resp.status_code)
            return data
        if resp.status_code == 401:
            raise FlipValueAuthError("FlipValue rejected the API key", 401)
        if resp.status_code in (404, 422) and code in NO_DATA_CODES:
            raise FlipValueNoDataError(message or "FlipValue has no data for this phone", resp.status_code)
        if resp.status_code in (403, 404, 405):
            raise _RouteMissing(path)
        if resp.status_code == 429:
            raise FlipValueRateLimitedError("FlipValue rate limit reached", 429)
        if resp.status_code in (400, 422):
            raise FlipValueNoDataError(message or "FlipValue could not answer for these details", resp.status_code)
        raise FlipValueUnavailableError(f"FlipValue is unavailable (HTTP {resp.status_code})", resp.status_code)

    def _first(self, attempts: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]],
               accept: Any) -> Any:
        """Try each (method, path, params, body) in order; the first response that ``accept`` approves wins."""
        for method, path, params, body in attempts:
            try:
                data = self._request(method, path, params, body)
            except _RouteMissing:
                continue
            if accept(data):
                return data
            log.info("flipvalue market: %s returned an unexpected shape, trying the next endpoint", path)
        raise FlipValueUnavailableError("This FlipValue service does not offer this data")

    def _cached(self, key: tuple[Any, ...], fetch: Any) -> Any:
        full = (self.client.settings.base_url, *key)
        hit = _cache.get(full)
        if hit and hit[0] > self._now():
            return hit[1]
        value = fetch()
        _cache[full] = (self._now() + CACHE_TTL_S, value)
        return value

    # ------------------------------------------------------------------ catalogue

    def find_product(self, manufacturer: str, model: str) -> dict[str, Any]:
        """The FlipValue catalogue entry for a phone, matched by OEM model code or name."""
        return self._cached(("product", manufacturer.lower(), model.lower()),
                            lambda: self._find_product(manufacturer, model))

    def _find_product(self, manufacturer: str, model: str) -> dict[str, Any]:
        def items(data: Any) -> list[dict[str, Any]] | None:
            if isinstance(data, dict):
                data = next((data[k] for k in ("items", "products", "results") if isinstance(data.get(k), list)), None)
            return [x for x in data if isinstance(x, dict) and isinstance(x.get("id"), str)] \
                if isinstance(data, list) else None

        found: list[dict[str, Any]] = []
        for q in dict.fromkeys([model, f"{manufacturer} {model}"]):
            data = self._first([("GET", "/api/v1/products", {"q": q}, None),
                                ("GET", "/api/catalogue", {"q": q}, None)], lambda d: items(d) is not None)
            found = items(data) or []
            if found:
                break
        best = _best_match(found, manufacturer, model)
        if best is None:
            raise FlipValueNoDataError(f"{manufacturer} {model} is not in the FlipValue catalogue")
        return {"id": best["id"], "manufacturer": _text(best.get("manufacturer"), 80) or manufacturer,
                "name": _text(best.get("name"), 120) or model,
                "release_date": _text(best.get("release_date"), 20),
                "storage_gb": [int(x) for x in best.get("storage_gb") or [] if isinstance(x, int) and 0 < x <= 4096],
                "oem_codes": [c for c in best.get("oem_codes") or [] if isinstance(c, str)][:10],
                "evidence": {k: v for k, v in (best.get("evidence") or {}).items()
                             if isinstance(k, str) and isinstance(v, str)}}

    # ------------------------------------------------------------------ trends

    def trend(self, product_id: str, storage_gb: int, region: str, grade: str) -> dict[str, Any]:
        params = {"model_id": product_id, "product_id": product_id, "storage_gb": storage_gb, "region": region,
                  "grade": grade}
        data = self._cached(("trend", product_id, storage_gb, region, grade), lambda: self._first(
            [("GET", "/api/v1/trends", params, None), ("GET", "/api/trends", params, None)],
            lambda d: isinstance(d, dict) and "available" in d))
        series: dict[str, list[dict[str, Any]]] = {}
        for name, points in (data.get("series") or {}).items():
            if not isinstance(points, list) or name in ("mock",):
                continue
            clean = [{"month": p["month"], "median": _num(p.get("median")), "count": p.get("count")}
                     for p in points if isinstance(p, dict) and isinstance(p.get("month"), str)
                     and _MONTH.match(p["month"]) and _num(p.get("median")) is not None]
            if clean:
                series[str(name)[:40]] = clean[-120:]
        available = bool(data.get("available")) and bool(series)
        return {"available": available, "currency": _text(data.get("currency"), 3),
                "reason": None if available else (_text(data.get("reason"), 300)
                                                  or "FlipValue has no price history for this phone yet."),
                "series": series if available else {}}

    # ------------------------------------------------------------------ store prices

    def offers(self, product_id: str, storage_gb: int, region: str) -> dict[str, Any]:
        params = {"product_id": product_id, "model_id": product_id, "storage_gb": storage_gb, "region": region}
        data = self._cached(("offers", product_id, storage_gb, region), lambda: self._first(
            [("GET", "/api/v1/market-prices", params, None), ("GET", "/api/market/offers", params, None)],
            lambda d: isinstance(d, dict) and isinstance(d.get("groups"), dict)))
        groups: dict[str, list[dict[str, Any]]] = {}
        for g in OFFER_GROUPS:
            rows: list[dict[str, Any]] = []
            for o in data["groups"].get(g) or []:
                if not isinstance(o, dict):
                    continue
                src: dict[str, Any] = o["source"] if isinstance(o.get("source"), dict) else {}
                price = _num(o.get("price"))
                if price is None or o.get("is_mock") or src.get("is_mock"):
                    continue
                rows.append({"seller": _text(o.get("seller"), 80) or _text(src.get("name"), 80) or "Unknown seller",
                             "seller_type": _text(o.get("seller_type"), 40), "title": _text(o.get("title"), 160),
                             "price": price, "mrp": _num((o.get("mrp") or {}).get("value"))
                             if isinstance(o.get("mrp"), dict) else _num(o.get("mrp")),
                             "currency": _text(o.get("currency"), 3) or _text(data.get("currency"), 3),
                             "url": _https(o.get("url")), "availability": _text(o.get("availability"), 40),
                             "retrieved_at": _text(o.get("retrieved_at"), 40),
                             "estimated": bool(src.get("estimated")) or o.get("freshness") == "estimate",
                             "verified": bool(src.get("verified")), "source": _text(src.get("name"), 120)})
            groups[g] = sorted(rows, key=lambda r: r["price"])[:MAX_OFFERS_PER_GROUP]
        return {"currency": _text(data.get("currency"), 3), "groups": groups,
                "available": any(groups.values())}

    # ------------------------------------------------------------------ repair or sell

    def compare(self, body: dict[str, Any]) -> dict[str, Any]:
        data = self._first([("POST", "/api/v1/decision", None, body), ("POST", "/api/decision", None, body)],
                           lambda d: isinstance(d, dict) and isinstance(d.get("scenarios"), list))
        scenarios = [{"key": _text(s.get("key"), 40), "title": _text(s.get("title"), 120) or "Option",
                      "available": bool(s.get("available")),
                      "missing": [m for m in s.get("missing") or [] if isinstance(m, str)][:10],
                      "gross": _num(s.get("gross")), "selling_fees": _num(s.get("selling_fees")),
                      "other_costs": _num(s.get("other_costs")),
                      "net": s.get("net") if isinstance(s.get("net"), int | float)
                      and not isinstance(s.get("net"), bool) else None,
                      "explanation": _text(s.get("explanation"), 300)}
                     for s in data["scenarios"] if isinstance(s, dict)]
        adv = data.get("repair_first_advantage")
        return {"currency": _text(data.get("currency"), 3), "scenarios": scenarios,
                "repair_first_advantage": adv if isinstance(adv, int | float) and not isinstance(adv, bool) else None,
                "assumptions": [a[:300] for a in data.get("assumptions") or [] if isinstance(a, str)][:12]}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _best_match(items: list[dict[str, Any]], manufacturer: str, model: str) -> dict[str, Any] | None:
    """Exact OEM code first (ignoring suffixes like /DS), then an exact name, then nothing (never a loose guess)."""
    code = _norm(model.split("/")[0])
    if not code:
        return None
    for it in items:
        if any(_norm(str(c).split("/")[0]) == code for c in it.get("oem_codes") or []):
            return it
    for it in items:
        full = _norm(f"{it.get('manufacturer', '')}{it.get('name', '')}")
        if code in (_norm(str(it.get("name", ""))), full) or _norm(f"{manufacturer}{model}") == full:
            return it
    return None
