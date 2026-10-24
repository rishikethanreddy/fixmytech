"""Online backup of the FixMyTech SQLite database (safe while the server is running).

    python scripts/backup_db.py [--db var/fixmytech.db] [--out var/backups] [--keep 14]

Uses SQLite's backup API (consistent snapshot even with WAL), verifies the copy with an integrity check,
and keeps the newest ``--keep`` backups. For PostgreSQL deployments use pg_dump / managed backups instead.
Backups contain customer reports: store them with the same access controls and delete them according to the
retention policy in docs/PRIVACY.md.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path


def backup(db: Path, out_dir: Path, keep: int) -> Path:
    if not db.is_file():
        raise FileNotFoundError(db)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"fixmytech-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.db"
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"integrity check failed: {result}")
    finally:
        src.close()
        dst.close()
    for old in sorted(out_dir.glob("fixmytech-*.db"))[:-keep]:
        old.unlink()
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=Path("var/fixmytech.db"))
    ap.add_argument("--out", type=Path, default=Path("var/backups"))
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()
    print(f"backup written: {backup(args.db, args.out, args.keep)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
