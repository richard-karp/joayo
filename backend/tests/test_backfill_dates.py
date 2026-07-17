"""Tests for the post-date backfill (mocked fetcher).

Covers the resumable/throttled pass: already-dated rows are skipped, a place's
date is the earliest across its source URLs, shared URLs are fetched once, and
unrecoverable URLs leave the row undated.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backfill_dates as backfill
from database import Base
from models import Place


def _seeded_engine():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add_all([
        Place(id="r1", is_place=True, source_urls=["u1", "u2"], earliest_date_posted=None),
        # Already dated → must be skipped (resumability).
        Place(id="r2", is_place=True, source_urls=["u1"],
              earliest_date_posted=datetime(2020, 1, 1)),
        # Only URL fails to fetch → stays undated.
        Place(id="r3", is_place=True, source_urls=["u3"], earliest_date_posted=None),
        # No source URLs → skipped.
        Place(id="r4", is_place=True, source_urls=[], earliest_date_posted=None),
    ])
    s.commit()
    s.close()
    return Session


_DATES = {
    "u1": datetime(2024, 3, 1, tzinfo=timezone.utc),
    "u2": datetime(2024, 1, 15, tzinfo=timezone.utc),  # earlier than u1
}


def _fake_fetch_post(url, embedded_caption=None):
    if url not in _DATES:
        raise RuntimeError("HTTP 404: not found")
    return SimpleNamespace(date_posted=_DATES[url])


def test_run_dates_skips_and_recovers(monkeypatch):
    Session = _seeded_engine()

    fetch_calls = []

    def _counting_fetch(url, embedded_caption=None):
        fetch_calls.append(url)
        return _fake_fetch_post(url, embedded_caption)

    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", "/tmp/cookies.txt")
    monkeypatch.setattr(backfill, "SessionLocal", Session)
    monkeypatch.setattr(backfill, "_backup_db", lambda: "backup")
    monkeypatch.setattr(backfill, "fetch_post", _counting_fetch)

    backfill.run(apply=True, limit=None, sleep=0)

    s = Session()
    r1, r2, r3 = s.get(Place, "r1"), s.get(Place, "r2"), s.get(Place, "r3")

    # Earliest across u1/u2 (stored naive UTC).
    assert r1.earliest_date_posted == datetime(2024, 1, 15)
    # Already-dated row untouched, and its URL was never re-fetched.
    assert r2.earliest_date_posted == datetime(2020, 1, 1)
    # Failed fetch leaves the row undated.
    assert r3.earliest_date_posted is None
    s.close()

    # u1 is shared by r1 and r2, but r2 was skipped and u1 is fetched once.
    assert sorted(fetch_calls) == ["u1", "u2", "u3"]


def test_run_dry_run_writes_nothing(monkeypatch):
    Session = _seeded_engine()
    monkeypatch.setenv("INSTAGRAM_COOKIES_FILE", "/tmp/cookies.txt")
    monkeypatch.setattr(backfill, "SessionLocal", Session)
    monkeypatch.setattr(backfill, "fetch_post", _fake_fetch_post)

    backfill.run(apply=False, limit=None, sleep=0)

    s = Session()
    assert s.get(Place, "r1").earliest_date_posted is None  # rolled back
    s.close()
