import json

import httpx
import pytest

from fixmytech.integrations.flipvalue import client as fv
from fixmytech.integrations.flipvalue import market as mk

CATALOGUE = [
    {"id": "samsung-galaxy-s21", "manufacturer": "Samsung", "name": "Galaxy S21", "release_date": "2021-01-29",
     "storage_gb": [128, 256], "oem_codes": ["SM-G991B", "SM-G991B/DS"], "evidence": {"IN": "estimated"}},
    {"id": "samsung-galaxy-s21-fe", "manufacturer": "Samsung", "name": "Galaxy S21 FE", "storage_gb": [128],
     "oem_codes": ["SM-G990B"]},
]
TREND = {"available": True, "currency": "INR", "reason": None, "series": {"estimate": [
    {"month": "2026-08", "median": 15590.0, "count": 3}, {"month": "2026-09", "median": 15300.0, "count": 3},
    {"month": "bad", "median": 1.0}, {"month": "2026-10", "median": 15000.0, "count": 3}]}}
OFFERS = {"currency": "INR", "groups": {
    "new": [],
    "used": [{"seller": "B", "price": 14000, "source": {"name": "S", "estimated": True}},
             {"seller": "A", "price": 12000, "url": "javascript:alert(1)", "source": {"name": "S"}},
             {"seller": "M", "price": 1, "is_mock": True}],
    "refurbished": [{"seller": "C", "price": 19000, "url": "https://example.com/c", "source": {"is_mock": True}}]}}


def market(routes: dict[str, httpx.Response], seen: list[str] | None = None) -> mk.MarketClient:
    mk._cache.clear()

    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req.url.path)
        return routes.get(req.url.path, httpx.Response(404, json={"detail": "Not Found"}))

    c = fv.FlipValueClient(fv.FlipValueSettings(mode="live", base_url="https://fv.example", api_key="k-secret"),
                           transport=httpx.MockTransport(handler))
    return mk.MarketClient(c)


def test_falls_back_to_public_endpoints_and_matches_oem_code():
    seen: list[str] = []
    m = market({"/api/catalogue": httpx.Response(200, json=CATALOGUE)}, seen)
    p = m.find_product("samsung", "SM-G991B/DS")
    assert p["id"] == "samsung-galaxy-s21" and p["storage_gb"] == [128, 256]
    assert seen[:2] == ["/api/v1/products", "/api/catalogue"]


def test_keyed_endpoint_is_used_when_present():
    seen: list[str] = []
    m = market({"/api/v1/products": httpx.Response(200, json={"items": CATALOGUE})}, seen)
    assert m.find_product("Samsung", "Galaxy S21 FE")["id"] == "samsung-galaxy-s21-fe"
    assert seen == ["/api/v1/products"]


def test_unknown_phone_is_no_data_never_a_loose_match():
    m = market({"/api/catalogue": httpx.Response(200, json=CATALOGUE)})
    with pytest.raises(mk.FlipValueNoDataError):
        m.find_product("Samsung", "SM-G99")


def test_trend_keeps_only_valid_points():
    m = market({"/api/trends": httpx.Response(200, json=TREND)})
    t = m.trend("samsung-galaxy-s21", 128, "IN", "good")
    assert t["available"] and [p["month"] for p in t["series"]["estimate"]] == ["2026-08", "2026-09", "2026-10"]


def test_trend_without_history_is_reported_as_unavailable():
    m = market({"/api/trends": httpx.Response(200, json={"available": False, "reason": "Not enough history",
                                                         "series": {}})})
    t = m.trend("samsung-galaxy-s21", 128, "US", "good")
    assert t == {"available": False, "currency": None, "reason": "Not enough history", "series": {}}


def test_offers_drop_mock_rows_and_unsafe_links():
    m = market({"/api/market/offers": httpx.Response(200, json=OFFERS)})
    o = m.offers("samsung-galaxy-s21", 128, "IN")
    assert [r["seller"] for r in o["groups"]["used"]] == ["A", "B"]  # sorted by price, mock row removed
    assert o["groups"]["used"][0]["url"] is None and o["groups"]["used"][1]["estimated"] is True
    assert o["groups"]["refurbished"] == [] and o["groups"]["buyback"] == [] and o["available"]


def test_bad_key_is_an_auth_error_not_a_fallback():
    m = market({"/api/v1/products": httpx.Response(401, json={"error": {"code": "unauthorized", "message": "x"}}),
                "/api/catalogue": httpx.Response(200, json=CATALOGUE)})
    with pytest.raises(fv.FlipValueAuthError):
        m.find_product("Samsung", "SM-G991B")


def test_domain_404_means_no_data():
    m = market({"/api/trends": httpx.Response(404, json={"error": {"code": "unsupported_model",
                                                                   "message": "Not in the catalogue."}})})
    with pytest.raises(mk.FlipValueNoDataError, match="Not in the catalogue"):
        m.trend("x", 128, "IN", "good")


def test_service_without_market_data_is_unavailable():
    with pytest.raises(fv.FlipValueUnavailableError):
        market({}).offers("x", 128, "IN")


def test_compare_sends_values_and_keeps_scenarios():
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/decision":
            sent.update(json.loads(req.content))
            return httpx.Response(200, json={"currency": "INR", "repair_first_advantage": 2000.0, "assumptions": ["a"],
                                             "scenarios": [{"key": "sell_as_is", "title": "Sell as-is",
                                                            "available": True, "missing": [], "net": 15000.0}]})
        return httpx.Response(404, json={"detail": "Not Found"})

    c = fv.FlipValueClient(fv.FlipValueSettings(mode="live", base_url="https://fv.example", api_key="k"),
                           transport=httpx.MockTransport(handler))
    out = mk.MarketClient(c).compare({"currency": "INR", "as_is_value": 15000})
    assert sent == {"currency": "INR", "as_is_value": 15000}
    assert out["scenarios"][0]["net"] == 15000.0 and out["repair_first_advantage"] == 2000.0
