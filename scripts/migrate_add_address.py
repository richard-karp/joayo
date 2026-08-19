#!/usr/bin/env python3
"""Add `places.address`. Idempotent; safe to re-run.

SQLite's ALTER TABLE ADD COLUMN is O(1) and non-rewriting, so this does not touch existing rows
and cannot lose data. It takes a backup anyway, matching the `places.db.pre-*` convention already
in the repo — the cost is one file copy and the alternative is a bad afternoon.

Usage:  python3 migrate_add_address.py backend/places.db
"""
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        return print(__doc__) or 2
    db = Path(sys.argv[1])
    if not db.exists():
        sys.exit(f"no such database: {db}")

    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(places)")}
    if "address" in cols:
        have = con.execute(
            "SELECT COUNT(*) FROM places WHERE address IS NOT NULL AND address != ''"
        ).fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM places").fetchone()[0]
        print(f"column already present — {have} of {total} rows carry an address")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db.with_suffix(db.suffix + f".pre-address-{stamp}")
    shutil.copy2(db, backup)
    print(f"backup: {backup.name}")

    con.execute("ALTER TABLE places ADD COLUMN address VARCHAR")
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM places").fetchone()[0]
    fillable = con.execute(
        "SELECT COUNT(*) FROM places WHERE geocoder = 'kakao' AND geocoder_place_id IS NOT NULL"
    ).fetchone()[0]
    print(f"added places.address — {total} rows, {fillable} have a Kakao POI id and can be backfilled")
    print("next: python3 backfill_addresses.py backend/places.db   (needs KAKAO_REST_API_KEY)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
