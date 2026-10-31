import httpx

from fixmytech.integrations.flipvalue import market as mk

DEVICE = {"manufacturer": "samsung", "model": "SM-S928B", "storage_gb": 256, "age_months": 30, "region": "IN"}
PRODUCT = {"id": "samsung-galaxy-s24-ultra", "manufacturer": "Samsung", "name": "Galaxy S24 Ultra",
           "storage_gb": [256, 512], "oem_codes": ["SM-S928B"], "evidence": {"IN": "estimated"}}


DECISION = {"currency": "INR",
            "scenarios": [{"key": "sell_as_is", "title": "Sell as-is", "available": True, "net": 50000}]}


def flipvalue(catalogue, trend=None, offers=None):
    def handler(req: httpx.Request) -> httpx.Response:
        body = {"/api/catalogue": catalogue, "/api/trends": trend, "/api/market/offers": offers,
                "/api/decision": DECISION}.get(req.url.path)
        return httpx.Response(200, json=body) if body is not None else httpx.Response(404, json={"detail": "x"})
    return httpx.MockTransport(handler)


def session(c):
    r = c.post("/api/v1/sessions", json={"mode": "manual", "device": DEVICE,
                                         "consent": {"diagnostic_processing": True}})
    return r.json()["session_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_market_data_for_a_known_phone(make_backend):
    mk._cache.clear()
    trend = {"available": True, "currency": "INR", "series": {"estimate": [
        {"month": f"2026-0{i}", "median": 60000 - i * 500, "count": 3} for i in range(1, 6)]}}
    offers = {"currency": "INR", "groups": {"used": [{"seller": "A", "price": 55000, "source": {}}]}}
    c = make_backend(flipvalue_mode="live", transport=flipvalue([PRODUCT], trend, offers))
    sid, h = session(c)
    m = c.get(f"/api/v1/sessions/{sid}/market?grade=fair", headers=h).json()
    assert m["status"] == "ok" and m["product"]["id"] == PRODUCT["id"] and m["storage_gb"] == 256
    assert m["grade"] == "fair" and m["trend"]["available"] and m["offers"]["groups"]["used"][0]["price"] == 55000
    cmp = c.post(f"/api/v1/sessions/{sid}/market/compare", json={"currency": "INR", "as_is_value": 50000}, headers=h)
    assert cmp.status_code == 200 and cmp.json()["scenarios"][0]["net"] == 50000


def test_unknown_phone_says_no_data(make_backend):
    mk._cache.clear()
    c = make_backend(flipvalue_mode="live", transport=flipvalue([]))
    sid, h = session(c)
    m = c.get(f"/api/v1/sessions/{sid}/market", headers=h).json()
    assert m["status"] == "no_product" and "no data" in m["message"] and m["trend"] is None


def test_sections_without_data_are_marked_not_invented(make_backend):
    mk._cache.clear()
    trend = {"available": False, "reason": "Not enough price history", "series": {}}
    c = make_backend(flipvalue_mode="live", transport=flipvalue([PRODUCT], trend, None))
    sid, h = session(c)
    m = c.get(f"/api/v1/sessions/{sid}/market", headers=h).json()
    assert m["trend"] == {"available": False, "currency": None, "reason": "Not enough price history", "series": {}}
    assert m["offers"]["available"] is False and m["offers"]["error_code"] == "flipvalue_unavailable"


def test_market_needs_a_configured_service(make_backend):
    c = make_backend(flipvalue_mode="mock", FIXMYTECH_ALLOW_MOCK_VALUATION=False)
    sid, h = session(c)
    r = c.get(f"/api/v1/sessions/{sid}/market", headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "valuation_not_configured"
