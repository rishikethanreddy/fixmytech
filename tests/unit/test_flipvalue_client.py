import json

import httpx
import pytest

from fixmytech.integrations.flipvalue import client as fv
from fixmytech.integrations.flipvalue.mock_server import create_mock_app
from fixmytech.integrations.flipvalue.schemas import ValuationRequest
from tests.conftest import asgi_transport_for

REQ = ValuationRequest.model_validate({
    "device": {"manufacturer": "Google", "model": "Pixel 7", "storage_gb": 128, "age_months": 20},
    "region": "US", "currency": "USD",
    "condition": {"grade": "faulty", "faults": ["battery_wear"]},
    "repair_scenario": {"repair_type": "battery_replacement"},
})

GOOD = {"api_version": "1", "valuation_id": "v-1", "currency": "USD",
        "as_is": {"value": 180, "low": 150, "high": 210}, "after_repair": {"value": 260},
        "data_timestamp": "2026-10-18T00:00:00Z", "method": "comparable-sales-v2", "status": "ok", "warnings": []}


def make(handler, mode="live", retries=2, base="https://flipvalue.example"):
    sleeps: list[float] = []
    c = fv.FlipValueClient(fv.FlipValueSettings(mode=mode, base_url=base, api_key="k-secret", timeout_s=1,
                                                max_retries=retries, backoff_s=0.1),
                           transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return c, sleeps


def test_success_sends_contract_headers_and_body():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["headers"], seen["body"], seen["path"] = req.headers, json.loads(req.content), req.url.path
        return httpx.Response(200, json=GOOD)

    c, _ = make(handler)
    out = c.get_valuation(REQ)
    assert out.source == "flipvalue_live" and out.attempts == 1
    assert out.response.as_is.value == 180 and out.response.after_repair.value == 260
    assert seen["path"] == "/v1/valuations"
    assert seen["headers"]["x-api-key"] == "k-secret"
    assert seen["headers"]["idempotency-key"] == seen["headers"]["x-request-id"]
    assert seen["body"]["condition"]["grade"] == "faulty"


@pytest.mark.parametrize("status,exc", [
    (401, fv.FlipValueAuthError), (403, fv.FlipValueAuthError), (404, fv.FlipValueUnsupportedDeviceError),
    (422, fv.FlipValueRejectedRequestError), (400, fv.FlipValueRejectedRequestError),
    (418, fv.FlipValueInvalidResponseError),
])
def test_non_retryable_errors_fail_fast(status, exc):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(status, json={"error": {"code": "x", "message": "nope"}})

    c, sleeps = make(handler)
    with pytest.raises(exc):
        c.get_valuation(REQ)
    assert len(calls) == 1 and sleeps == []


def test_retry_then_success_on_503_reuses_idempotency_key():
    keys = []

    def handler(req):
        keys.append(req.headers["idempotency-key"])
        return httpx.Response(503) if len(keys) < 2 else httpx.Response(200, json=GOOD)

    c, sleeps = make(handler)
    out = c.get_valuation(REQ)
    assert out.attempts == 2 and len(set(keys)) == 1 and sleeps == [0.1]


def test_retries_are_bounded():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(502)

    c, sleeps = make(handler, retries=2)
    with pytest.raises(fv.FlipValueUnavailableError):
        c.get_valuation(REQ)
    assert len(calls) == 3 and len(sleeps) == 2


def test_rate_limit_respects_retry_after_with_cap():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "100"})

    c, sleeps = make(handler, retries=1)
    with pytest.raises(fv.FlipValueRateLimitedError):
        c.get_valuation(REQ)
    assert sleeps == [fv.MAX_RETRY_AFTER_S]


def test_timeout_mapped():
    def handler(req):
        raise httpx.ReadTimeout("slow", request=req)

    c, _ = make(handler, retries=0)
    with pytest.raises(fv.FlipValueTimeoutError):
        c.get_valuation(REQ)


def test_connection_error_mapped():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    c, _ = make(handler, retries=0)
    with pytest.raises(fv.FlipValueUnavailableError):
        c.get_valuation(REQ)


@pytest.mark.parametrize("body", [
    {"api_version": "1"},
    {**GOOD, "as_is": {"value": -5}},
    {**GOOD, "as_is": {"value": 100, "low": 150}},
    {**GOOD, "status": "maybe"},
    {**GOOD, "currency": "dollars"},
])
def test_invalid_responses_rejected(body):
    c, _ = make(lambda req: httpx.Response(200, json=body))
    with pytest.raises(fv.FlipValueInvalidResponseError):
        c.get_valuation(REQ)


def test_non_json_response_rejected():
    c, _ = make(lambda req: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(fv.FlipValueInvalidResponseError):
        c.get_valuation(REQ)


def test_live_mode_refuses_mock_responses():
    c, _ = make(lambda req: httpx.Response(200, json={**GOOD, "mock": True}))
    with pytest.raises(fv.FlipValueInvalidResponseError, match="mock"):
        c.get_valuation(REQ)


def test_configuration_errors():
    c, _ = make(lambda r: httpx.Response(200, json=GOOD), mode="disabled")
    with pytest.raises(fv.FlipValueDisabledError):
        c.get_valuation(REQ)
    c, _ = make(lambda r: httpx.Response(200, json=GOOD), base="http://flipvalue.example")
    with pytest.raises(fv.FlipValueConfigError, match="https"):
        c.get_valuation(REQ)
    c = fv.FlipValueClient(fv.FlipValueSettings(mode="live", base_url="https://x", api_key=None))
    with pytest.raises(fv.FlipValueConfigError):
        c.get_valuation(REQ)


def test_status_never_exposes_key():
    c, _ = make(lambda r: httpx.Response(200, json=GOOD))
    assert "k-secret" not in json.dumps(c.status())


def test_against_mock_server_contract():
    transport = asgi_transport_for(create_mock_app(api_key="test-key", timeout_sleep_s=0))
    c = fv.FlipValueClient(fv.FlipValueSettings(mode="mock", base_url="http://mock", api_key="test-key"),
                           transport=transport)
    out = c.get_valuation(REQ)
    assert out.source == "flipvalue_mock" and out.response.mock is True
    assert out.response.after_repair is not None
    assert out.response.after_repair.value >= out.response.as_is.value
    assert "MOCK" in out.response.method

    bad = fv.FlipValueClient(fv.FlipValueSettings(mode="mock", base_url="http://mock", api_key="wrong"),
                             transport=transport)
    with pytest.raises(fv.FlipValueAuthError):
        bad.get_valuation(REQ)


def test_request_schema_rejects_unknown_categories():
    with pytest.raises(ValueError):
        ValuationRequest.model_validate({**REQ.model_dump(), "condition": {"grade": "faulty", "faults": ["gremlins"]}})
    with pytest.raises(ValueError):
        ValuationRequest.model_validate({**REQ.model_dump(), "repair_scenario": {"repair_type": "magic"}})
