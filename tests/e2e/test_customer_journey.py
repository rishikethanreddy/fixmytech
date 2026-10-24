"""End-to-end journey using recorded ADB output, the real agent API, the real backend API and the MOCK
FlipValue service. The browser's role (relaying the scan from agent to backend) is played by the test."""

from fastapi.testclient import TestClient

from fixmytech.agent.server import create_agent_app
from tests.conftest import SERIAL, FakeAdb

TOKEN = "e2e-token-" + "x" * 20


def test_usb_journey(make_backend):
    backend = make_backend()
    agent = TestClient(create_agent_app(TOKEN, ["http://127.0.0.1:8000"], adb=FakeAdb(), port=8765,
                                        run_in_thread=False), base_url="http://127.0.0.1:8765")
    ah = {"X-Agent-Token": TOKEN, "Origin": "http://127.0.0.1:8000"}

    # 1. connect & pick device
    devices = agent.get("/agent/v1/devices", headers=ah).json()["devices"]
    ready = [d for d in devices if d["ready"]]
    assert len(ready) == 1

    # 2. create session with consent, run scan, relay result
    s = backend.post("/api/v1/sessions", json={"mode": "usb", "device": {"region": "IN", "age_months": 34},
                                               "consent": {"diagnostic_processing": True, "store_report": True}}).json()
    h = {"Authorization": f"Bearer {s['access_token']}"}
    sid = s["session_id"]
    scan_id = agent.post("/agent/v1/scans", json={"serial": ready[0]["serial"], "consent": True},
                         headers=ah).json()["scan_id"]
    scan = agent.get(f"/agent/v1/scans/{scan_id}", headers=ah).json()["result"]
    assert backend.put(f"/api/v1/sessions/{sid}/scan", json=scan, headers=h).status_code == 200

    # 3. findings
    obs = backend.get(f"/api/v1/sessions/{sid}/observations", headers=h).json()
    assert any(o["check_id"] == "storage.data" and o["status"] == "ok" for o in obs["observations"])

    # 4. adaptive questionnaire, answering as a customer whose phone is slow and drains
    script = {"primary_symptoms": ["slow", "battery_drain"], "physical_event": "none",
              "battery_drain_pattern": "sudden_drops", "safe_mode": "continues", "crash_scope": "many_apps",
              "recent_update": "no", "battery_swelling": "no", "battery_history": "original"}
    asked = []
    nq = backend.get(f"/api/v1/sessions/{sid}/questions/next", headers=h).json()
    while nq["question"]:
        q = nq["question"]
        asked.append(q["id"])
        value = script.get(q["id"]) or (q["options"][0]["value"] if q["type"] == "single_choice"
                                        else [q["options"][0]["value"]])
        nq = backend.put(f"/api/v1/sessions/{sid}/answers/{q['id']}", json={"value": value}, headers=h).json()
    assert nq["stop_reason"] and len(asked) <= nq["budget"]
    assert "charging_behavior" not in asked  # irrelevant questions not asked

    # 5. diagnosis
    d = backend.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    assert d["outcome"] == "candidates_found"
    top = {c["fault_id"] for c in d["candidates"][:3]}
    assert {"storage_pressure", "battery_wear"} & top
    for c in d["candidates"]:
        for e in c["supporting"]:
            assert e["rationale"] and e["facts"]

    # 6. repair estimates
    rep = backend.get(f"/api/v1/sessions/{sid}/repair-estimate", headers=h).json()
    # the scanned SM-G991B is not in the Samsung India excerpt: no price is invented
    batt = next(e for e in rep["estimates"] if e["repair_type"] == "battery_replacement")
    assert batt["status"] == "insufficient_pricing_data" and batt["official_links"]
    backend.post(f"/api/v1/sessions/{sid}/quotes", headers=h,
                 json={"repair_type": "battery_replacement", "amount": 3200, "currency": "INR"})

    # 7. valuation (mock) + decision
    sug = backend.get(f"/api/v1/sessions/{sid}/valuation", headers=h).json()["suggested_condition"]
    v = backend.post(f"/api/v1/sessions/{sid}/valuation", headers=h,
                     json={"condition_grade": sug["grade"] or "good", "storage_gb": 256}).json()
    assert v["ok"] and v["source"] == "flipvalue_mock"
    dec = backend.post(f"/api/v1/sessions/{sid}/decision", headers=h, json={
        "repair_source": "quote:0", "replacement_price": 45000, "transaction_fee_pct": 5}).json()
    assert dec["result"]["recommendation_text"]

    # 8. report
    report = backend.get(f"/api/v1/sessions/{sid}/report", headers=h).json()
    assert report["session"]["mode"] == "usb" and report["disclaimer"]
    assert SERIAL not in str(report)


def test_manual_journey_without_flipvalue(make_backend):
    backend = make_backend(flipvalue_mode="disabled")
    s = backend.post("/api/v1/sessions", json={"mode": "manual", "device": {"manufacturer": "Apple",
                                                                             "model": "Unknown", "region": "IN"},
                                               "consent": {"diagnostic_processing": True}}).json()
    h = {"Authorization": f"Bearer {s['access_token']}"}
    sid = s["session_id"]
    for qid, v in [("primary_symptoms", ["screen"]), ("physical_event", "dropped"),
                   ("screen_symptom", ["cracked_glass"]), ("touch_symptom", "dead_areas")]:
        backend.put(f"/api/v1/sessions/{sid}/answers/{qid}", json={"value": v}, headers=h)
    d = backend.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    assert d["candidates"][0]["fault_id"] == "display_damage"
    assert d["candidates"][0]["has_device_evidence"] is False  # only customer-reported evidence
    rep = backend.get(f"/api/v1/sessions/{sid}/repair-estimate", headers=h).json()
    screen = next(e for e in rep["estimates"] if e["repair_type"] == "screen_replacement")
    assert screen["status"] == "insufficient_pricing_data"  # Apple prices are never collected (terms)
    assert any("apple.com" in link["url"] for link in screen["official_links"])
    v = backend.post(f"/api/v1/sessions/{sid}/valuation", headers=h, json={"condition_grade": "faulty"})
    assert v.status_code == 409
