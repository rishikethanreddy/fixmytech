import json

import httpx
import pytest

from fixmytech.integrations.flipvalue.mock_server import create_mock_app
from fixmytech.server.errors import ApiError
from fixmytech.server.partner import require_admin, validate_base_url
from tests.conftest import asgi_transport_for

DEVICE = {"manufacturer": "samsung", "model": "SM-S928B", "storage_gb": 256, "age_months": 30, "region": "IN"}
KEY = "secret-partner-key-1234"


def real_looking(transport, key):
    """Stand-in for a real partner service: the dev mock's answers with the mock markers removed."""
    def handler(request: httpx.Request) -> httpx.Response:
        resp = transport.handle_request(request)
        resp.read()
        data = resp.json()
        if isinstance(data, dict) and "mock" in data:
            data["mock"] = False
        return httpx.Response(resp.status_code, json=data)
    return httpx.MockTransport(handler)


def session(c):
    r = c.post("/api/v1/sessions", json={"mode": "manual", "device": DEVICE,
                                         "consent": {"diagnostic_processing": True}})
    return r.json()["session_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_no_key_means_no_numbers_and_not_the_mock(make_backend):
    c = make_backend(flipvalue_mode="mock", FIXMYTECH_ALLOW_MOCK_VALUATION=False)  # mock not explicitly allowed
    sid, h = session(c)
    v = c.get(f"/api/v1/sessions/{sid}/valuation", headers=h).json()
    assert v["configured"] is False and v["stored"] is None
    r = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "valuation_not_configured"
    assert c.get("/api/v1/config").json()["valuation_configured"] is False


def test_saved_key_is_used_never_returned_and_never_stored_in_reports(make_backend):
    transport = real_looking(asgi_transport_for(create_mock_app(api_key=KEY)), KEY)
    c = make_backend(flipvalue_mode="disabled", transport=transport)
    base = "https://flipvalue.test"
    r = c.put("/api/v1/settings/partner", json={"base_url": base, "api_key": KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True and body["key_hint"].endswith(KEY[-4:])
    assert KEY not in json.dumps(body) and KEY not in c.get("/api/v1/settings/partner").text
    assert KEY not in c.get("/api/v1/config").text and KEY not in c.get("/api/v1/health").text
    # the saved client talks to the saved URL; point it at the mock app through the test transport
    c.app.state.fmt.partner_clients.clear()
    c.app.state.fmt.partner_clients[(base, KEY)] = type(c.app.state.fmt.flipvalue)(
        c.app.state.fmt.flipvalue.settings.__class__(mode="live", base_url=base, api_key=KEY, timeout_s=2.0,
                                                     max_retries=0, backoff_s=0.0), transport=transport)
    sid, h = session(c)
    ok = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert ok.status_code == 200, ok.text
    assert ok.json()["source"] == "flipvalue_live" and KEY not in ok.text
    assert KEY not in json.dumps(c.get(f"/api/v1/sessions/{sid}/report", headers=h).json())
    assert c.post("/api/v1/settings/partner/test").json()["ok"] is True
    assert c.delete("/api/v1/settings/partner").json()["configured"] is False
    again = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert again.status_code == 409


def test_wrong_key_is_reported_clearly(make_backend):
    transport = real_looking(asgi_transport_for(create_mock_app(api_key="the-real-key-0000")), "the-real-key-0000")
    c = make_backend(flipvalue_mode="disabled", transport=transport)
    c.put("/api/v1/settings/partner", json={"base_url": "https://flipvalue.test", "api_key": "wrong-key-12345"})
    base_client = c.app.state.fmt.flipvalue
    c.app.state.fmt.partner_clients[("https://flipvalue.test", "wrong-key-12345")] = type(base_client)(
        base_client.settings.__class__(mode="live", base_url="https://flipvalue.test", api_key="wrong-key-12345",
                                       timeout_s=2.0, max_retries=0, backoff_s=0.0), transport=transport)
    res = c.post("/api/v1/settings/partner/test").json()
    assert res["ok"] is False and res["code"] == "flipvalue_auth_failed"


@pytest.mark.parametrize("url", ["ftp://x.example", "https://user:pw@x.example", "https://x.example/?k=1", "nothing"])
def test_url_validation_rejects_bad_values(url):
    with pytest.raises(ApiError):
        validate_base_url(url, "development")


def test_production_blocks_private_and_plain_http_addresses():
    for url in ["http://api.example.com", "https://127.0.0.1", "https://localhost", "https://192.168.1.5",
                "https://169.254.169.254", "https://service.internal"]:
        with pytest.raises(ApiError):
            validate_base_url(url, "production", resolve=False)
    assert validate_base_url("https://api.example.com/v1/", "production", resolve=False) == "https://api.example.com/v1"
    assert validate_base_url("http://localhost:8090", "development") == "http://localhost:8090"


def test_admin_gate():
    with pytest.raises(ApiError):
        require_admin("10.0.0.5", None, None, "development")  # remote client, no token configured
    require_admin("127.0.0.1", None, None, "development")
    with pytest.raises(ApiError):
        require_admin("127.0.0.1", None, None, "production")  # production needs an admin token
    with pytest.raises(ApiError):
        require_admin("1.2.3.4", "nope", "right", "production")
    require_admin("1.2.3.4", "right", "right", "production")


def test_mock_cannot_be_enabled_in_production():
    from fixmytech.server.config import Settings

    with pytest.raises(ValueError):
        Settings(FIXMYTECH_ENV="production", FIXMYTECH_ALLOW_MOCK_VALUATION=True,
                 FIXMYTECH_PRICING_ALLOWED_SOURCES="")


def test_saved_file_holds_the_key_only_in_the_partner_file(make_backend, tmp_path):
    c = make_backend(flipvalue_mode="disabled")
    c.put("/api/v1/settings/partner", json={"base_url": "https://flipvalue.test", "api_key": KEY})
    stored = c.app.state.fmt.partner_store.path
    assert json.loads(stored.read_text())["api_key"] == KEY
    assert httpx.URL("https://flipvalue.test").host == "flipvalue.test"


def test_mock_service_is_refused_when_configured_as_live(make_backend):
    """A saved key pointing at something that identifies itself as the dev mock must not produce numbers."""
    transport = asgi_transport_for(create_mock_app(api_key=KEY))
    c = make_backend(flipvalue_mode="disabled", transport=transport)
    c.put("/api/v1/settings/partner", json={"base_url": "https://flipvalue.test", "api_key": KEY})
    base_client = c.app.state.fmt.flipvalue
    c.app.state.fmt.partner_clients[("https://flipvalue.test", KEY)] = type(base_client)(
        base_client.settings.__class__(mode="live", base_url="https://flipvalue.test", api_key=KEY, timeout_s=2.0,
                                       max_retries=0, backoff_s=0.0), transport=transport)
    sid, h = session(c)
    r = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert r.status_code == 502 and r.json()["error"]["code"] == "flipvalue_invalid_response"
