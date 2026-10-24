from datetime import UTC, datetime, timedelta

from fixmytech.server.db import DiagnosticSession


def test_security_headers(make_backend):
    c = make_backend()
    r = c.get("/api/v1/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-request-id"]


def test_body_size_limit(make_backend):
    c = make_backend(FIXMYTECH_MAX_BODY_BYTES=2048)
    r = c.post("/api/v1/sessions", content=b"{" + b" " * 5000 + b"}", headers={"content-type": "application/json"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_rate_limit(make_backend):
    c = make_backend(FIXMYTECH_RATE_LIMIT_PER_MINUTE=10)
    codes = [c.get("/api/v1/health").status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[-1] == 429


def test_validation_errors_are_structured_without_internals(make_backend):
    c = make_backend()
    r = c.post("/api/v1/sessions", json={"mode": "telepathy"})
    err = r.json()["error"]
    assert r.status_code == 422 and err["code"] == "validation_error" and err["request_id"]
    assert "Traceback" not in r.text


def test_expired_sessions_are_purged_and_inaccessible(make_backend):
    c = make_backend()
    r = c.post("/api/v1/sessions", json={"mode": "manual", "device": {}, "consent": {"diagnostic_processing": True}})
    sid, tok = r.json()["session_id"], r.json()["access_token"]
    state = c.app.state.fmt
    with state.db._factory() as s:
        rec = s.get(DiagnosticSession, sid)
        rec.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    assert c.get(f"/api/v1/sessions/{sid}", headers={"Authorization": f"Bearer {tok}"}).status_code == 404
    assert state.db.purge_expired() == 1


def test_tokens_stored_hashed(make_backend):
    c = make_backend()
    r = c.post("/api/v1/sessions", json={"mode": "manual", "device": {}, "consent": {"diagnostic_processing": True}})
    tok = r.json()["access_token"]
    with c.app.state.fmt.db._factory() as s:
        rec = s.get(DiagnosticSession, r.json()["session_id"])
        assert rec.token_hash != tok and len(rec.token_hash) == 64


def test_retention_depends_on_consent(make_backend):
    c = make_backend()
    temp = c.post("/api/v1/sessions", json={"mode": "manual", "device": {},
                                            "consent": {"diagnostic_processing": True}}).json()
    kept = c.post("/api/v1/sessions", json={"mode": "manual", "device": {},
                                            "consent": {"diagnostic_processing": True, "store_report": True}}).json()
    t = datetime.fromisoformat(temp["expires_at"])
    k = datetime.fromisoformat(kept["expires_at"])
    assert k - t > timedelta(days=20)


def test_ready_endpoint(make_backend):
    c = make_backend()
    r = c.get("/api/v1/ready")
    assert r.status_code == 200 and r.json()["ready"] is True
    assert r.json()["checks"]["schema_current"] is True and r.json()["checks"]["pricing_records"] > 0


def test_ready_reports_missing_pricing(make_backend, tmp_path):
    c = make_backend(FIXMYTECH_PRICING_DIR=str(tmp_path / "empty"))
    body = c.get("/api/v1/ready").json()
    assert body["checks"]["pricing_records"] == 0 and any("No repair price data" in w for w in body["warnings"])


def test_database_failure_is_structured_503(make_backend, monkeypatch):
    from sqlalchemy.exc import OperationalError

    c = make_backend()
    sid_tok = c.post("/api/v1/sessions", json={"mode": "manual", "device": {},
                                               "consent": {"diagnostic_processing": True}}).json()
    from sqlalchemy.orm import Session

    def broken(*a, **k):
        raise OperationalError("SELECT", {}, Exception("disk I/O error"))

    monkeypatch.setattr(Session, "get", broken)
    auth = {"Authorization": f"Bearer {sid_tok['access_token']}"}
    r = c.get(f"/api/v1/sessions/{sid_tok['session_id']}", headers=auth)
    assert r.status_code == 503 and r.json()["error"]["code"] == "database_unavailable"
    assert "disk I/O" not in r.text


def test_backup_script(tmp_path):
    import sqlite3
    import sys

    sys.path.insert(0, "scripts")
    from backup_db import backup

    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (a)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    for _ in range(3):
        out = backup(db, tmp_path / "b", keep=2)
    assert sqlite3.connect(out).execute("SELECT a FROM t").fetchone() == (1,)
    assert len(list((tmp_path / "b").glob("*.db"))) <= 2


def test_site_files_are_revalidated_so_updates_show_up(make_backend):
    from fastapi.testclient import TestClient

    from fixmytech.server.app import create_app
    from fixmytech.server.config import Settings

    app = create_app(Settings(FIXMYTECH_DATABASE_URL="sqlite:///:memory:", FIXMYTECH_ENV="test"))
    r = TestClient(app).get("/js/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
