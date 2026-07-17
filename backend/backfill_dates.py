#!/usr/bin/env python3
"""Backfill earliest_date_posted for rows that never captured a post date.

Every row came in through the caption-only export path, which sets date_posted=None,
so no post date was ever recorded. This re-fetches each source reel through the
app's fetcher (Instagram goes through the yt-dlp **cookies** path, which honors
INSTAGRAM_COOKIES_FILE and parses upload_date) and sets each place's
earliest_date_posted to the earliest date across its source_urls.

One **resumable, throttled** pass: it skips rows that already have a date (so a
re-run continues where a previous run stopped), sleeps between Instagram calls,
and continues past failures. URLs shared across places are fetched once.
Unrecoverable URLs are appended to backfill_dates_failures.log (on --apply).

Defaults to DRY RUN. --apply commits (a places.db.pre-dates-<ts> backup is written
first). --limit N processes only the first N undated rows; --sleep S sets the
per-fetch delay (default 1.0s — cookies runs are slow, and some URLs will 404).

    python backend/backfill_dates.py --limit 5      # sample preview
    python backend/backfill_dates.py                # full dry run
    python backend/backfill_dates.py --apply        # commit

Needs a valid INSTAGRAM_COOKIES_FILE — without logged-in cookies the fetches 404.
"""
import argparse
import os
import shutil
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(raise_error_if_not_found=False))
# Resolve the DB relative to this file (not the CWD) unless DB_PATH is already set,
# so the script hits backend/places.db no matter where it's launched from, while
# production's absolute DB_PATH (the Fly volume) still takes precedence.
os.environ.setdefault("DB_PATH", os.path.join(os.path.dirname(__file__), "places.db"))

from database import SessionLocal  # noqa: E402  (import after DB_PATH is resolved)
from models import Place  # noqa: E402
from services.deduplicator import _naive_utc  # noqa: E402
from services.fetchers import fetch_post  # noqa: E402

_FAILURE_LOG = os.path.join(os.path.dirname(__file__), "backfill_dates_failures.log")


def _backup_db() -> str:
    db_path = os.environ["DB_PATH"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{db_path}.pre-dates-{ts}"
    shutil.copy2(db_path, dst)
    return dst


def _fetch_date(url: str):
    """Return (date, error) — date is a naive-UTC datetime or None."""
    try:
        rp = fetch_post(url)
    except Exception as e:  # 404s, private posts, unsupported platform, …
        return None, str(e)[:200]
    date = _naive_utc(rp.date_posted)
    return date, (None if date else "fetched but no post date")


def run(apply: bool, limit: int | None, sleep: float) -> None:
    if not os.getenv("INSTAGRAM_COOKIES_FILE"):
        print("WARNING: INSTAGRAM_COOKIES_FILE is not set — Instagram fetches will likely 404.\n")

    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== backfill_dates.py [{mode}] ===\n")

    if apply:
        backup = _backup_db()
        print(f"Backup written: {backup}\n")

    db = SessionLocal()
    failures: list[tuple[str, str]] = []
    try:
        # Resumable: only rows still missing a date.
        q = db.query(Place).filter(
            Place.earliest_date_posted.is_(None)
        ).order_by(Place.created_at)
        candidates = [p for p in (q.limit(limit).all() if limit else q.all()) if p.source_urls]
        print(f"{len(candidates)} undated row(s) with source URLs.")

        # Fetch each distinct URL once (URLs are shared across places).
        unique_urls: list[str] = []
        seen: set[str] = set()
        for p in candidates:
            for u in p.source_urls:
                if u not in seen:
                    seen.add(u)
                    unique_urls.append(u)
        print(f"{len(unique_urls)} distinct URL(s) to fetch.\n")

        url_date: dict[str, "datetime | None"] = {}
        for i, u in enumerate(unique_urls, 1):
            date, err = _fetch_date(u)
            url_date[u] = date
            if date is None:
                failures.append((u, err))
                print(f"  [{i}/{len(unique_urls)}] ✗ {u}  ({err})")
            else:
                print(f"  [{i}/{len(unique_urls)}] ✓ {u}  → {date.date().isoformat()}")
            time.sleep(sleep)

        # Assign the earliest recovered date to each place.
        updated = 0
        for p in candidates:
            dates = [d for d in (url_date.get(u) for u in p.source_urls) if d is not None]
            if dates:
                p.earliest_date_posted = min(dates)
                updated += 1

        if apply:
            db.commit()
            if failures:
                with open(_FAILURE_LOG, "a") as fh:
                    fh.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} run\n")
                    for u, err in failures:
                        fh.write(f"{u}\t{err}\n")

        print(
            f"\nDated {updated} place(s); {len(failures)} URL(s) unrecoverable "
            f"(of {len(unique_urls)} fetched)."
        )
        if not apply:
            db.rollback()
            print("Done (dry run — no writes). Re-run with --apply to commit.")
        else:
            if failures:
                print(f"Unrecoverable URLs appended to {_FAILURE_LOG}")
            print("Done. Re-run to pick up any rows still undated.")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Recover post dates by re-fetching source reels via the yt-dlp cookies path."
    )
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry run).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N undated rows (sampling).")
    ap.add_argument("--sleep", type=float, default=1.0,
                    help="Seconds to sleep between fetches (default 1.0).")
    args = ap.parse_args()
    run(args.apply, args.limit, args.sleep)
