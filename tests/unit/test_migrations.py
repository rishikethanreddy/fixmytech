import sqlite3

from fixmytech.server.db import SCHEMA_VERSION, Database

V1_SCHEMA = """CREATE TABLE diagnostic_sessions (
    id VARCHAR(36) PRIMARY KEY, token_hash VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL, expires_at DATETIME NOT NULL, mode VARCHAR(10) NOT NULL, device JSON NOT NULL,
    consent JSON NOT NULL, scan JSON, answers JSON NOT NULL, quotes JSON NOT NULL, valuation JSON, decision JSON)"""


def test_fresh_database_gets_current_schema(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'fresh.db'}")
    assert db.migrate() == list(range(1, SCHEMA_VERSION + 1))
    assert db.schema_version() == SCHEMA_VERSION
    assert db.migrate() == []  # idempotent


def test_pre_migration_database_is_upgraded_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(V1_SCHEMA)
    con.execute("INSERT INTO diagnostic_sessions VALUES ('s1','h','2026-10-01','2026-10-01','2099-01-01','manual',"
                "'{}','{}',NULL,'{\"primary_symptoms\": [\"slow\"]}','[]',NULL,NULL)")
    con.commit()
    con.close()
    db = Database(f"sqlite:///{path}")
    assert db.migrate() == [1, 2]
    con = sqlite3.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(diagnostic_sessions)")}
    assert {"guided_tests", "phone_token_hash", "phone_token_expires_at"} <= cols
    assert con.execute("SELECT answers FROM diagnostic_sessions WHERE id='s1'").fetchone()[0].startswith("{")
    con.close()
