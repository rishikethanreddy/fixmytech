import httpx
import pytest

from fixmytech.agent.collectors import run_scan
from fixmytech.core.capabilities import CHECKS
from tests.conftest import SERIAL, FakeAdb

DEVICE = {"manufacturer": "samsung", "model": "SM-S928B", "storage_gb": 256, "age_months": 30, "region": "IN"}


def new_session(c, mode="manual", device=DEVICE, store=False):
    r = c.post("/api/v1/sessions", json={"mode": mode, "device": device,
                                         "consent": {"diagnostic_processing": True, "store_report": store}})
    assert r.status_code == 201, r.text
    body = r.json()
    return body["session_id"], {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def client(make_backend):
    return make_backend()


def test_health_and_config(client):
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok" and h["database"] is True
    assert h["flipvalue"]["mode"] == "mock"
    cfg = client.get("/api/v1/config").json()
    assert {"region": "IN", "currency": "INR", "has_price_data": True} in cfg["regions"]
    assert {"region": "US", "currency": "USD", "has_price_data": False} in cfg["regions"]
    assert cfg["pricing_demo"] is False
    ps = client.get("/api/v1/pricing/status").json()
    assert ps["records"] > 0 and ps["sources"]["samsung_in_parts"]["last_success_at"]
    assert {c["source_id"]: c["usage_policy"] for c in ps["catalog"]}["apple_in_service"] == "prohibited"
    assert client.get("/api/v1/catalog/checks").json()["not_assessable"]
    assert len(client.get("/api/v1/catalog/faults").json()["faults"]) >= 8


def test_consent_required(client):
    r = client.post("/api/v1/sessions", json={"mode": "manual", "device": DEVICE,
                                              "consent": {"diagnostic_processing": False}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"


def test_access_control(client):
    sid, h = new_session(client)
    assert client.get(f"/api/v1/sessions/{sid}").status_code == 401
    assert client.get(f"/api/v1/sessions/{sid}", headers={"Authorization": "Bearer nope"}).status_code == 404
    other_sid, other_h = new_session(client)
    assert client.get(f"/api/v1/sessions/{sid}", headers=other_h).status_code == 404
    assert client.get("/api/v1/sessions/not-a-uuid", headers=h).status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}", headers=h).status_code == 200


def test_delete_session(client):
    sid, h = new_session(client)
    assert client.delete(f"/api/v1/sessions/{sid}", headers=h).status_code == 204
    assert client.get(f"/api/v1/sessions/{sid}", headers=h).status_code == 404


def test_scan_upload_is_validated_and_idempotent(client):
    sid, h = new_session(client, mode="usb", device={"region": "US"})
    scan = run_scan(FakeAdb(), SERIAL, b"0123456789abcdef").model_dump(mode="json")
    r = client.put(f"/api/v1/sessions/{sid}/scan", json=scan, headers=h)
    assert r.json() == {"stored": True, "duplicate": False}
    assert client.put(f"/api/v1/sessions/{sid}/scan", json=scan, headers=h).json()["duplicate"] is True
    # device details auto-filled from the scan identity
    assert client.get(f"/api/v1/sessions/{sid}", headers=h).json()["device"]["model"] == "SM-G991B"
    obs = client.get(f"/api/v1/sessions/{sid}/observations", headers=h).json()
    assert obs["has_scan"] and len(obs["observations"]) == len([c for c in CHECKS if c.default_in_scan])

    bad = {**scan, "observations": [{**scan["observations"][0], "value": {"raw_log": "x" * 5000}}]}
    assert client.put(f"/api/v1/sessions/{sid}/scan", json=bad, headers=h).status_code == 422
    extra = {**scan, "raw_logcat": "secret"}
    assert client.put(f"/api/v1/sessions/{sid}/scan", json=extra, headers=h).status_code == 422


def test_scan_rejected_for_manual_session(client):
    sid, h = new_session(client)
    scan = run_scan(FakeAdb(), SERIAL, b"0123456789abcdef").model_dump(mode="json")
    assert client.put(f"/api/v1/sessions/{sid}/scan", json=scan, headers=h).status_code == 409


def test_questionnaire_flow_and_validation(client):
    sid, h = new_session(client)
    q = client.get(f"/api/v1/sessions/{sid}/questions/next", headers=h).json()
    assert q["question"]["id"] == "primary_symptoms"
    r = client.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": "slow"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_answer"
    assert client.put(f"/api/v1/sessions/{sid}/answers/nope", json={"value": "x"}, headers=h).status_code == 404
    nxt = client.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["not_charging"]},
                     headers=h).json()
    assert nxt["question"] and nxt["asked"] == 1
    answers = client.get(f"/api/v1/sessions/{sid}/answers", headers=h).json()["answers"]
    assert answers[0]["labels"] == ["Won't charge, or charges slowly"]
    back = client.delete(f"/api/v1/sessions/{sid}/answers/primary_symptoms", headers=h).json()
    assert back["question"]["id"] == "primary_symptoms"


def test_quotes(client):
    sid, h = new_session(client)
    r = client.post(f"/api/v1/sessions/{sid}/quotes", json={"repair_type": "battery_replacement", "amount": 2825,
                                                             "currency": "INR"}, headers=h)
    assert r.status_code == 201
    assert r.json()["quotes"][0]["position"] == "within_range"
    assert client.post(f"/api/v1/sessions/{sid}/quotes", json={"repair_type": "unicorn_swap", "amount": 1,
                                                               "currency": "USD"}, headers=h).status_code == 422
    assert client.delete(f"/api/v1/sessions/{sid}/quotes/0", headers=h).json()["quotes"] == []
    assert client.delete(f"/api/v1/sessions/{sid}/quotes/5", headers=h).status_code == 404


def _answer_battery(client, sid, h):
    for qid, v in [("primary_symptoms", ["battery_drain", "shutdowns"]), ("battery_drain_pattern", "sudden_drops"),
                   ("shutdown_at_charge", "yes_above_20"), ("physical_event", "none")]:
        assert client.put(f"/api/v1/sessions/{sid}/answers/{qid}", json={"value": v}, headers=h).status_code == 200


def test_diagnosis_includes_repair_costs(client):
    sid, h = new_session(client)
    _answer_battery(client, sid, h)
    d = client.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    assert "repair_costs" in d, "diagnosis response should include repair_costs"
    costs = d["repair_costs"]
    # battery_wear is a top candidate after answering battery questions
    assert "battery_wear" in costs
    bc = costs["battery_wear"]
    assert bc["repair_type"] == "battery_replacement"
    assert bc["repair_name"] == "Battery replacement"
    assert bc["status"] in ("estimated", "parts_only", "insufficient_pricing_data")
    assert bc["conditional_label"] == "if this component is the cause"
    if bc["status"] in ("estimated", "parts_only"):
        assert bc["currency"] == "INR"
        assert bc["low"] is not None and bc["high"] is not None


def test_valuation_with_mock_and_decision(client):
    sid, h = new_session(client)
    _answer_battery(client, sid, h)
    sug = client.get(f"/api/v1/sessions/{sid}/valuation", headers=h).json()["suggested_condition"]
    assert sug["grade"] == "faulty" and sug["repair_type"] == "battery_replacement"
    v = client.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "faulty"}, headers=h)
    assert v.status_code == 200, v.text
    body = v.json()
    assert body["source"] == "flipvalue_mock" and body["response"]["mock"] is True
    d = client.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={
        "repair_source": "estimate:battery_replacement", "replacement_price": 70000}).json()["result"]
    assert d["currency"] == "INR"
    assert any("before tax" in c for c in d["caveats"])
    assert any("MOCK" in c for c in d["caveats"])
    assert {s["id"] for s in d["scenarios"] if s["available"]} == {"repair_and_keep", "replace", "sell_as_is",
                                                                   "repair_and_resell"}
    report = client.get(f"/api/v1/sessions/{sid}/report", headers=h).json()
    assert report["decision"]["result"]["recommendation_text"] and report["valuation"]["ok"]
    assert "access_token" not in str(report) and "token_hash" not in str(report)


def test_decision_with_manual_values_and_quote(client):
    sid, h = new_session(client)
    _answer_battery(client, sid, h)
    client.post(f"/api/v1/sessions/{sid}/quotes", json={"repair_type": "battery_replacement", "amount": 80,
                                                        "currency": "INR"}, headers=h)
    d = client.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={
        "repair_source": "quote:0", "value_source": "manual", "replacement_price": 500,
        "manual_values": {"as_is": {"low": 100, "high": 140}, "repaired": {"low": 220, "high": 260}}}).json()
    res = d["result"]
    assert any("entered by you" in c for c in res["caveats"])
    keep = next(s for s in res["scenarios"] if s["id"] == "repair_and_keep")
    assert keep["breakdown"]["repair_cost"] == 80
    r = client.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={"repair_source": "quote:9"})
    assert r.status_code == 422


def test_decision_currency_mismatch(client):
    sid, h = new_session(client)
    client.post(f"/api/v1/sessions/{sid}/quotes", json={"repair_type": "battery_replacement", "amount": 80,
                                                        "currency": "EUR"}, headers=h)
    r = client.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={"repair_source": "quote:0",
                                                                         "currency": "INR"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "currency_mismatch"


def test_flipvalue_disabled_keeps_diagnosis_working(make_backend):
    c = make_backend(flipvalue_mode="disabled")
    sid, h = new_session(c)
    _answer_battery(c, sid, h)
    r = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "faulty"}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "valuation_not_configured"
    assert c.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()["outcome"] == "candidates_found"
    d = c.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={
        "repair_source": "estimate:battery_replacement", "replacement_price": 600}).json()["result"]
    assert d["comparisons"][0]["verdict"] == "incomplete"
    assert any("Resale value was not considered" in c for c in d["caveats"])
    assert "as-is value" in d["missing_inputs"]


def test_decision_without_valuation(client):
    """User skips the resale value step entirely and goes straight to decision."""
    sid, h = new_session(client)
    _answer_battery(client, sid, h)
    # Skip directly to decision, no valuation call
    d = client.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={
        "repair_source": "estimate:battery_replacement", "replacement_price": 60000}).json()["result"]
    assert d["currency"] == "INR"
    # Resale-based comparisons should list missing resale values, not crash
    assert any("as-is" in mi.lower() or "resale" in mi.lower() for mi in d["missing_inputs"])


@pytest.mark.parametrize("status,code,http", [
    (401, "flipvalue_auth_failed", 502), (503, "flipvalue_unavailable", 502),
    (404, "flipvalue_unsupported_device", 422), (429, "flipvalue_rate_limited", 503),
])
def test_flipvalue_failures_map_to_structured_errors(make_backend, status, code, http):
    c = make_backend(flipvalue_mode="live", transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    sid, h = new_session(c)
    r = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert r.status_code == http and r.json()["error"]["code"] == code
    stored = c.get(f"/api/v1/sessions/{sid}/valuation", headers=h).json()["stored"]
    assert stored["ok"] is False and "response" not in stored  # no fabricated values


def test_flipvalue_timeout(make_backend):
    def slow(req):
        raise httpx.ReadTimeout("t", request=req)

    c = make_backend(flipvalue_mode="live", transport=httpx.MockTransport(slow))
    sid, h = new_session(c)
    r = c.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert r.status_code == 504 and r.json()["error"]["code"] == "flipvalue_timeout"


def test_valuation_requires_device_details(client):
    sid, h = new_session(client, device={"region": "US"})
    r = client.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "device_details_required"


def test_device_update_invalidates_valuation(client):
    sid, h = new_session(client)
    client.post(f"/api/v1/sessions/{sid}/valuation", json={"condition_grade": "good"}, headers=h)
    client.patch(f"/api/v1/sessions/{sid}/device", json={**DEVICE, "model": "Pixel 8"}, headers=h)
    assert client.get(f"/api/v1/sessions/{sid}/valuation", headers=h).json()["stored"] is None


def test_repair_estimate_is_source_attributed(client):
    sid, h = new_session(client)
    for qid, v in [("primary_symptoms", ["battery_drain"]), ("battery_drain_pattern", "sudden_drops"),
                   ("shutdown_at_charge", "yes_above_20")]:
        client.put(f"/api/v1/sessions/{sid}/answers/{qid}", json={"value": v}, headers=h)
    rep = client.get(f"/api/v1/sessions/{sid}/repair-estimate", headers=h).json()
    batt = next(e for e in rep["estimates"] if e["repair_type"] == "battery_replacement")
    assert batt["status"] == "estimated" and batt["data_kind"] == "source_attributed"
    assert batt["model_match"] == "exact_model_code" and batt["currency"] == "INR"
    assert all(line["source_url"].startswith("https://www.samsung.com/in/") for line in batt["evidence"])
    assert batt["tax"] == "excluded" and rep["demo_data_in_use"] is False


def test_unknown_model_gets_insufficient_pricing_not_a_guess(client):
    sid, h = new_session(client, device={**DEVICE, "manufacturer": "Google", "model": "Pixel 7"})
    client.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["battery_drain"]}, headers=h)
    client.put(f"/api/v1/sessions/{sid}/answers/battery_drain_pattern", json={"value": "sudden_drops"}, headers=h)
    rep = client.get(f"/api/v1/sessions/{sid}/repair-estimate", headers=h).json()
    batt = next(e for e in rep["estimates"] if e["repair_type"] == "battery_replacement")
    assert batt["status"] == "insufficient_pricing_data" and batt["low"] is None
    r = client.post(f"/api/v1/sessions/{sid}/decision", headers=h,
                    json={"repair_source": "estimate:battery_replacement"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "estimate_unavailable"


def test_demo_pricing_is_labelled(make_backend):
    c = make_backend(FIXMYTECH_PRICING_DEMO=True)
    sid, h = new_session(c, device={**DEVICE, "manufacturer": "Nokia", "model": "X", "region": "US"})
    c.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["battery_drain"]}, headers=h)
    c.put(f"/api/v1/sessions/{sid}/answers/battery_drain_pattern", json={"value": "sudden_drops"}, headers=h)
    rep = c.get(f"/api/v1/sessions/{sid}/repair-estimate", headers=h).json()
    assert rep["demo_data_in_use"] is True
    assert all(e["data_kind"] in ("demo_fixture", "none") for e in rep["estimates"])


def test_scan_identity_overrides_typed_device(client):
    sid, h = new_session(client, mode="usb", device={"manufacturer": "Pixel", "model": "6A", "region": "IN"})
    scan = run_scan(FakeAdb(), SERIAL, b"0123456789abcdef").model_dump(mode="json")
    client.put(f"/api/v1/sessions/{sid}/scan", json=scan, headers=h)
    dev = client.get(f"/api/v1/sessions/{sid}", headers=h).json()["device"]
    assert dev["manufacturer"] == "samsung" and dev["model"] == "SM-G991B"
    assert dev["customer_entered"] == {"manufacturer": "Pixel", "model": "6A"}
