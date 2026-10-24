import pytest
from fastapi.testclient import TestClient

from fixmytech.agent.server import create_agent_app
from fixmytech.core.capabilities import CHECKS
from tests.conftest import SERIAL, FakeAdb

TOKEN = "t" * 32
ORIGIN = "http://127.0.0.1:8000"


def agent(adb=None, **kw):
    app = create_agent_app(TOKEN, [ORIGIN], adb=adb or FakeAdb(), salt=b"0123456789abcdef", port=8765,
                           run_in_thread=False, **kw)
    return TestClient(app, base_url="http://127.0.0.1:8765")


H = {"X-Agent-Token": TOKEN}


def test_health_is_open_everything_else_needs_token():
    c = agent()
    assert c.get("/agent/v1/health").json()["token_required"] is True
    for path in ("/agent/v1/adb", "/agent/v1/devices", "/agent/v1/capabilities", "/agent/v1/session"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers={"X-Agent-Token": "wrong"}).status_code == 401
        assert c.get(path, headers=H).status_code == 200


def test_dns_rebinding_host_rejected():
    c = TestClient(create_agent_app(TOKEN, [ORIGIN], adb=FakeAdb(), port=8765), base_url="http://evil.example:8765")
    assert c.get("/agent/v1/health").status_code == 403


def test_foreign_origin_rejected_even_with_token():
    c = agent()
    r = c.get("/agent/v1/devices", headers={**H, "Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_origin"


def test_cors_preflight_for_allowed_origin_includes_private_network():
    c = agent()
    r = c.options("/agent/v1/scans", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "x-agent-token,content-type",
                                              "Access-Control-Request-Private-Network": "true"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert r.headers["access-control-allow-private-network"] == "true"


def test_adb_status_and_missing_adb():
    assert agent().get("/agent/v1/adb", headers=H).json()["installed"] is True
    body = agent(FakeAdb(installed=False)).get("/agent/v1/adb", headers=H).json()
    assert body["installed"] is False and body["error"]["code"] == "adb_not_installed"


def test_devices_listing_with_states_and_help():
    devs = agent().get("/agent/v1/devices", headers=H).json()["devices"]
    by = {d["serial"]: d for d in devs}
    assert by[SERIAL]["ready"] is True and by[SERIAL]["model"] == "SM G991B"
    assert "Allow" in by["emulator-5554"]["help"]
    assert by["0123456789ABCDEF"]["state"] == "offline"


def test_no_devices():
    assert agent(FakeAdb(devices_output="List of devices attached\n\n")).get(
        "/agent/v1/devices", headers=H).json()["devices"] == []


def test_device_summary():
    s = agent().get(f"/agent/v1/devices/{SERIAL}/summary", headers=H).json()
    assert s["identity"]["value"]["model"] == "SM-G991B"
    assert len(s["capabilities"]) == len(CHECKS) and s["not_assessable"]
    assert {c["check_id"]: c["in_default_scan"] for c in s["capabilities"]}["bugreport.summary"] is False


@pytest.mark.parametrize("serial,code", [("emulator-5554", 409), ("MISSING123", 404)])
def test_scan_requires_ready_device(serial, code):
    r = agent().post("/agent/v1/scans", json={"serial": serial, "consent": True}, headers=H)
    assert r.status_code == code


def test_scan_requires_consent_and_known_checks():
    c = agent()
    r = c.post("/agent/v1/scans", json={"serial": SERIAL, "consent": False}, headers=H)
    assert r.status_code == 422 and r.json()["error"]["code"] == "consent_required"
    r = c.post("/agent/v1/scans", json={"serial": SERIAL, "consent": True, "checks": ["shell.exec"]}, headers=H)
    assert r.status_code == 422
    r = c.post("/agent/v1/scans", json={"serial": "x;reboot", "consent": True}, headers=H)
    assert r.status_code == 422


def test_scan_job_lifecycle():
    c = agent()
    sid = c.post("/agent/v1/scans", json={"serial": SERIAL, "consent": True}, headers=H).json()["scan_id"]
    st = c.get(f"/agent/v1/scans/{sid}", headers=H).json()
    assert st["state"] == "done" and st["done"] == st["total"] == len([c for c in CHECKS if c.default_in_scan])
    assert st["result"]["scan_id"] == sid and SERIAL not in str(st["result"])
    assert c.get("/agent/v1/scans/unknown", headers=H).status_code == 404


def test_scan_with_disconnect():
    c = agent(FakeAdb(disconnect_after=2))
    sid = c.post("/agent/v1/scans", json={"serial": SERIAL, "consent": True}, headers=H).json()["scan_id"]
    st = c.get(f"/agent/v1/scans/{sid}", headers=H).json()
    assert st["state"] == "done" and st["result"]["complete"] is False


def test_connector_test_job():
    c = agent(sleeper=lambda _: None)
    assert c.post(f"/agent/v1/devices/{SERIAL}/connector-test", json={"consent": False}, headers=H).status_code == 422
    job = c.post(f"/agent/v1/devices/{SERIAL}/connector-test", json={"consent": True, "duration_s": 10}, headers=H)
    st = c.get(f"/agent/v1/jobs/{job.json()['job_id']}", headers=H).json()
    assert st["state"] == "done" and st["result"]["measures"]["samples"] == 10


def test_bugreport_job_returns_optin_observation():
    c = agent()
    job = c.post(f"/agent/v1/devices/{SERIAL}/bugreport", json={"consent": True}, headers=H).json()
    st = c.get(f"/agent/v1/jobs/{job['job_id']}", headers=H).json()
    assert st["state"] == "done"
    obs = st["result"]
    assert obs["check_id"] == "bugreport.summary" and obs["value"]["fatal_exceptions"] == 2


def test_open_phone_tests_requires_consent_and_valid_token():
    c = agent()
    url = f"/agent/v1/devices/{SERIAL}/phone-tests"
    assert c.post(url, json={"consent": False, "web_port": 8010, "token": "x" * 32}, headers=H).status_code == 422
    assert c.post(url, json={"consent": True, "web_port": 8010, "token": "bad;token"}, headers=H).status_code == 422
    r = c.post(url, json={"consent": True, "web_port": 8010, "token": "x" * 32}, headers=H)
    assert r.status_code == 200 and r.json()["forwarded"] and r.json()["opened"]
    assert c.request("DELETE", url, params={"web_port": 8010}, headers=H).json()["removed"] is True
    assert c.post(url, json={"consent": True, "web_port": 8010, "token": "x" * 32}).status_code == 401


def test_health_reports_features_and_live_readings():
    c = agent(sleeper=lambda _: None)
    h = c.get("/agent/v1/health").json()
    assert {"connector_test", "bugreport", "live_readings"} <= set(h["features"])
    job = c.post(f"/agent/v1/devices/{SERIAL}/connector-test", json={"consent": True, "duration_s": 10}, headers=H)
    st = c.get(f"/agent/v1/jobs/{job.json()['job_id']}", headers=H).json()
    assert st["live"]["second"] == 10 and st["live"]["plugged"] == "usb"
