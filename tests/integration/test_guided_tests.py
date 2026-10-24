from datetime import UTC, datetime, timedelta

from fixmytech.server.db import DiagnosticSession

DEVICE = {"manufacturer": "samsung", "model": "SM-S928B", "storage_gb": 256, "age_months": 30, "region": "IN"}


def session(c, mode="manual"):
    r = c.post("/api/v1/sessions", json={"mode": mode, "device": DEVICE, "consent": {"diagnostic_processing": True}})
    return r.json()["session_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_catalog_and_recommendations(make_backend):
    c = make_backend()
    sid, h = session(c)
    c.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["screen"]}, headers=h)
    tests = {t["id"]: t for t in c.get(f"/api/v1/sessions/{sid}/tests", headers=h).json()["tests"]}
    assert tests["touch_grid"]["recommended"] and tests["display_colors"]["recommended"]
    assert tests["charging_connector"]["available"] is False  # agent test needs a USB session
    assert all(t["result"] is None for t in tests.values())


def test_manual_result_feeds_diagnosis_as_user_observed(make_backend):
    c = make_backend()
    sid, h = session(c)
    c.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["network"]}, headers=h)
    r = c.put(f"/api/v1/sessions/{sid}/tests/mobile_data", json={"outcome": "fail"}, headers=h)
    assert r.status_code == 200 and r.json()["stored"]["outcome_kind"] == "user_observed"
    d = c.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    conn = next(x for x in d["candidates"] if x["fault_id"] == "connectivity_issue")
    item = next(e for e in conn["supporting"] if e["rule_id"] == "TEST-DATA-FAIL")
    assert item["facts"][0]["source"] == "user_observed"
    assert conn["has_test_evidence"] is True
    assert d["evidence_counts"]["test_results"] == 1


def test_not_run_is_never_evidence(make_backend):
    c = make_backend()
    sid, h = session(c)
    c.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["camera"]}, headers=h)
    r = c.put(f"/api/v1/sessions/{sid}/tests/camera_rear", json={"outcome": "not_run",
                                                                  "not_run_reason": "permission_denied"}, headers=h)
    assert r.status_code == 200
    d = c.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    cam = next(x for x in d["candidates"] if x["fault_id"] == "camera_fault")
    assert not any(e["rule_id"].startswith("TEST-CAMERA") for e in cam["supporting"] + cam["contradicting"])
    assert "camera_rear" in cam["suggested_tests"]  # still worth running


def test_result_validation(make_backend):
    c = make_backend()
    sid, h = session(c)
    bad = [("mobile_data", {"outcome": "partial"}),  # not an allowed outcome for this test
           ("mobile_data", {"outcome": "not_run"}),  # needs a reason
           ("mobile_data", {"outcome": "pass", "measures": {"nonsense": 1}}),
           ("touch_grid", {"outcome": "pass"}),  # phone-browser tests must come from the phone page
           ("charging_connector", {"outcome": "pass"})]  # agent tests must come from the agent
    for tid, body in bad:
        assert c.put(f"/api/v1/sessions/{sid}/tests/{tid}", json=body, headers=h).status_code == 422, (tid, body)
    assert c.put(f"/api/v1/sessions/{sid}/tests/nope", json={"outcome": "pass"}, headers=h).status_code == 404


def test_agent_result_only_for_usb_sessions(make_backend):
    c = make_backend()
    sid, h = session(c, mode="manual")
    body = {"outcome": "pass", "measures": {"charging_observed": True, "plug_events": 1}}
    url = f"/api/v1/sessions/{sid}/tests/charging_connector/agent-result"
    assert c.post(url, json=body, headers=h).status_code == 409
    sid, h = session(c, mode="usb")
    r = c.post(f"/api/v1/sessions/{sid}/tests/charging_connector/agent-result", json=body, headers=h)
    assert r.status_code == 200 and r.json()["stored"]["measures_kind"] == "agent_measured"
    assert c.post(f"/api/v1/sessions/{sid}/tests/speaker/agent-result", json=body, headers=h).status_code == 422


def test_phone_link_scope_and_expiry(make_backend):
    c = make_backend()
    sid, h = session(c)
    link = c.post(f"/api/v1/sessions/{sid}/phone-link", headers=h).json()
    token = link["token"]
    over = c.get(f"/api/v1/phone/{token}").json()
    assert over["device_label"] == "samsung SM-S928B"
    assert {t["method"] for t in over["tests"]} == {"phone_browser"}
    r = c.put(f"/api/v1/phone/{token}/tests/touch_grid", json={
        "outcome": "fail", "measures": {"cells_total": 40, "cells_touched": 31, "coverage_pct": 77.5}})
    assert r.status_code == 200 and r.json()["stored"]["measures_kind"] == "phone_measured"
    assert c.put(f"/api/v1/phone/{token}/tests/mobile_data", json={"outcome": "pass"}).status_code == 422
    # the phone token cannot read the session or the report
    assert c.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {token}"}).status_code == 404
    assert c.get(f"/api/v1/sessions/{sid}/report", headers={"Authorization": f"Bearer {token}"}).status_code == 404
    d = c.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    disp = next(x for x in d["candidates"] if x["fault_id"] == "display_damage")
    assert any(e["rule_id"] == "TEST-TOUCH-GAPS" for e in disp["supporting"])
    # expiry
    with c.app.state.fmt.db._factory() as s:
        rec = s.get(DiagnosticSession, sid)
        rec.phone_token_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        s.commit()
    assert c.get(f"/api/v1/phone/{token}").status_code == 404
    assert c.get("/api/v1/phone/short").status_code == 404


def test_phone_link_revocation(make_backend):
    c = make_backend()
    sid, h = session(c)
    token = c.post(f"/api/v1/sessions/{sid}/phone-link", headers=h).json()["token"]
    assert c.delete(f"/api/v1/sessions/{sid}/phone-link", headers=h).status_code == 204
    assert c.get(f"/api/v1/phone/{token}").status_code == 404


def test_report_includes_tests_and_next_step(make_backend):
    c = make_backend()
    sid, h = session(c)
    c.put(f"/api/v1/sessions/{sid}/answers/primary_symptoms", json={"value": ["screen"]}, headers=h)
    d = c.get(f"/api/v1/sessions/{sid}/diagnosis", headers=h).json()
    assert d["recommended_next_step"]["kind"] in ("test", "question")
    assert d["remaining_uncertainty"]
    c.put(f"/api/v1/sessions/{sid}/tests/buttons", json={"outcome": "fail"}, headers=h)
    rep = c.get(f"/api/v1/sessions/{sid}/report", headers=h).json()
    assert rep["guided_tests"]["buttons"]["outcome"] == "fail"
    assert any("button" in n["text"].lower() for n in rep["diagnosis"]["notes"])  # note-only rule fired


def test_session_progress_tracks_tests_and_questions(make_backend):
    c = make_backend()
    sid, h = session(c)
    p = c.get(f"/api/v1/sessions/{sid}", headers=h).json()["progress"]
    assert p["tests_available"] == 11 and p["tests_done"] == 0  # the agent test needs USB
    assert p["tests_complete"] is False and p["questions_complete"] is False
    c.put(f"/api/v1/sessions/{sid}/tests/buttons", json={"outcome": "pass"}, headers=h)
    c.put(f"/api/v1/sessions/{sid}/tests/mobile_data", json={"outcome": "not_run", "not_run_reason": "skipped"},
          headers=h)
    p = c.get(f"/api/v1/sessions/{sid}", headers=h).json()["progress"]
    assert (p["tests_done"], p["tests_passed"]) == (2, 1)


def test_next_question_more_flag(make_backend):
    c = make_backend()
    sid, h = session(c)
    r = c.get(f"/api/v1/sessions/{sid}/questions/next?more=true", headers=h)
    assert r.status_code == 200 and r.json()["question"]["id"] == "primary_symptoms"
    assert r.json()["skipped_by_tests"] == []
