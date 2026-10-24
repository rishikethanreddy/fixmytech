"""Persistence (SQLAlchemy 2.0). SQLite for the local MVP; the schema is portable to PostgreSQL.

One row per diagnostic session. Structured sub-documents (validated by Pydantic before storage) are
kept in JSON columns. Only normalized observations are stored; raw ADB output never reaches the
backend. Access tokens are stored as SHA-256 hashes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Connection, DateTime, String, create_engine, delete, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class DiagnosticSession(Base):
    __tablename__ = "diagnostic_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    mode: Mapped[str] = mapped_column(String(10))
    device: Mapped[dict[str, Any]] = mapped_column(JSON)
    consent: Mapped[dict[str, Any]] = mapped_column(JSON)
    scan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    answers: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quotes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    valuation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # schema v2
    guided_tests: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    phone_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    phone_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Ordered schema migrations. Version 1 is the original table. Never edit a released migration; append a new one.
def _m2_guided_tests(conn: Connection) -> None:
    conn.exec_driver_sql("ALTER TABLE diagnostic_sessions ADD COLUMN guided_tests JSON")
    conn.exec_driver_sql("ALTER TABLE diagnostic_sessions ADD COLUMN phone_token_hash VARCHAR(64)")
    conn.exec_driver_sql("ALTER TABLE diagnostic_sessions ADD COLUMN phone_token_expires_at TIMESTAMP")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_diagnostic_sessions_phone_token_hash "
                         "ON diagnostic_sessions (phone_token_hash)")


MIGRATIONS: list[tuple[int, str, Callable[[Connection], None] | None]] = [
    (1, "initial diagnostic_sessions table", None),
    (2, "guided tests and phone test link", _m2_guided_tests),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in url:
                from sqlalchemy.pool import StaticPool

                kwargs["poolclass"] = StaticPool
        self.engine: Engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn: Any, _: Any) -> None:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=5000")
                cur.close()

        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def schema_version(self) -> int:
        with self.engine.connect() as conn:
            if not inspect(conn).has_table("schema_migrations"):
                return 0
            return int(conn.exec_driver_sql("SELECT MAX(version) FROM schema_migrations").scalar() or 0)

    def migrate(self) -> list[int]:
        """Bring the schema to SCHEMA_VERSION. Returns the versions applied.

        New database: create the current schema and record every version. Database created before migrations
        existed (table present, no schema_migrations): treated as version 1.
        """
        applied: list[int] = []
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, "
                                 "description VARCHAR(200) NOT NULL, applied_at VARCHAR(40) NOT NULL)")
            current = int(conn.exec_driver_sql("SELECT MAX(version) FROM schema_migrations").scalar() or 0)
            has_table = inspect(conn).has_table("diagnostic_sessions")
            now = datetime.now(UTC).isoformat()

            def record(version: int, desc: str) -> None:
                conn.exec_driver_sql("INSERT INTO schema_migrations (version, description, applied_at) "
                                     "VALUES (?, ?, ?)" if conn.dialect.name == "sqlite" else
                                     "INSERT INTO schema_migrations (version, description, applied_at) "
                                     "VALUES (%s, %s, %s)", (version, desc, now))
                applied.append(version)

            if current == 0 and not has_table:
                Base.metadata.create_all(conn)
                for version, desc, _ in MIGRATIONS:
                    record(version, desc)
                return applied
            if current == 0:
                record(1, MIGRATIONS[0][1])
                current = 1
            for version, desc, step in MIGRATIONS:
                if version > current and step is not None:
                    step(conn)
                    record(version, desc)
        return applied

    def session(self) -> Iterator[Session]:
        with self._factory() as s:
            yield s

    def ping(self) -> bool:
        with self.engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True

    def purge_expired(self, now: datetime | None = None) -> int:
        with self._factory() as s:
            res = s.execute(delete(DiagnosticSession).where(DiagnosticSession.expires_at < (now or datetime.now(UTC))))
            s.commit()
            return int(res.rowcount or 0)  # type: ignore[attr-defined]
