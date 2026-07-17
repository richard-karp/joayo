"""Tests for the needs_review remediation endpoint (confirm / regeocode / reject)
and the needs_review list filter. geocode_full is monkeypatched; review_confidence
runs for real so the confidence gate is exercised end-to-end.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base, get_db
from models import Place
from services import geocoder
from services.geocoder import GeoResult
from tests.conftest import make_raw_post


def _make_test_app(session):
    def override_db():
        yield session

    main.app.dependency_overrides[get_db] = override_db
    return TestClient(main.app)


def _seed_flagged(session, name="Sooa Clinic", native="수아클리닉") -> str:
    """A geocoded-but-flagged pin, as the backfill would have written it."""
    raw = make_raw_post()
    place = Place(
        id=str(uuid4()),
        created_by_job_id="test-job",
        source_urls=[raw.url],
        platform="instagram",
        primary_author="u",
        all_authors=[],
        location_name=name,
        category="service",
        country="South Korea",
        city="Seoul",
        lat=37.51, lng=127.05,
        geocoder="kakao", geocoder_place_id="P_OLD",
        native_name=native,
        needs_review=True,
        labels=[], insider_tips="", raw_caption=raw.caption, tagged_accounts=[],
        transcript_missing=False,
    )
    session.add(place)
    session.commit()
    return place.id


@pytest.fixture()
def client_and_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    client = _make_test_app(session)
    yield client, session
    session.close()
    Base.metadata.drop_all(engine)
    main.app.dependency_overrides.clear()


# ── confirm ─────────────────────────────────────────────────────────────────

def test_confirm_clears_flag_keeps_coords(client_and_db):
    client, session = client_and_db
    pid = _seed_flagged(session)
    resp = client.post(f"/api/places/{pid}/review", json={"action": "confirm"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_review"] is False
    assert (round(body["lat"], 2), round(body["lng"], 2)) == (37.51, 127.05)


# ── reject ──────────────────────────────────────────────────────────────────

def test_reject_nulls_coords_and_clears_flag(client_and_db):
    client, session = client_and_db
    pid = _seed_flagged(session)
    resp = client.post(f"/api/places/{pid}/review", json={"action": "reject"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_review"] is False
    assert body["lat"] is None and body["lng"] is None
    assert body["geocoder_place_id"] is None


# ── regeocode ───────────────────────────────────────────────────────────────

def test_regeocode_strong_match_updates_and_clears_flag(client_and_db, monkeypatch):
    client, session = client_and_db
    pid = _seed_flagged(session)
    # A corrected name that Kakao resolves exactly → confidence 100 → flag cleared.
    monkeypatch.setattr(geocoder, "geocode_full",
                        lambda name, country=None, expected_city=None, native_name=None:
                        GeoResult(lat=37.5288, lng=126.9667, city="Seoul", provider="kakao",
                                  place_id="P_NEW", canonical_name="수아한의원"))
    resp = client.post(f"/api/places/{pid}/review",
                       json={"action": "regeocode", "native_name": "수아한의원"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_review"] is False
    assert body["geocoder_place_id"] == "P_NEW"
    assert (round(body["lat"], 3), round(body["lng"], 3)) == (37.529, 126.967)


def test_regeocode_weak_match_updates_but_stays_flagged(client_and_db, monkeypatch):
    client, session = client_and_db
    pid = _seed_flagged(session)
    # New coords, but the returned name still barely matches → stays flagged.
    monkeypatch.setattr(geocoder, "geocode_full",
                        lambda name, country=None, expected_city=None, native_name=None:
                        GeoResult(lat=37.60, lng=126.99, city="Seoul", provider="kakao",
                                  place_id="P_WEAK", canonical_name="전혀다른병원"))
    resp = client.post(f"/api/places/{pid}/review",
                       json={"action": "regeocode", "native_name": "수아의원"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_review"] is True
    assert body["geocoder_place_id"] == "P_WEAK"


def test_regeocode_no_match_is_422_and_leaves_place_untouched(client_and_db, monkeypatch):
    client, session = client_and_db
    pid = _seed_flagged(session)
    monkeypatch.setattr(geocoder, "geocode_full",
                        lambda name, country=None, expected_city=None, native_name=None: GeoResult())
    resp = client.post(f"/api/places/{pid}/review",
                       json={"action": "regeocode", "native_name": "존재하지않는곳"})
    assert resp.status_code == 422
    place = session.get(Place, pid)
    assert place.geocoder_place_id == "P_OLD"  # unchanged
    assert place.needs_review is True


def test_regeocode_requires_native_name(client_and_db):
    client, session = client_and_db
    pid = _seed_flagged(session)
    resp = client.post(f"/api/places/{pid}/review",
                       json={"action": "regeocode", "native_name": "   "})
    assert resp.status_code == 422


# ── misc ────────────────────────────────────────────────────────────────────

def test_review_unknown_place_404(client_and_db):
    client, _ = client_and_db
    resp = client.post("/api/places/does-not-exist/review", json={"action": "confirm"})
    assert resp.status_code == 404


def test_invalid_action_422(client_and_db):
    client, session = client_and_db
    pid = _seed_flagged(session)
    resp = client.post(f"/api/places/{pid}/review", json={"action": "bogus"})
    assert resp.status_code == 422


def test_needs_review_filter_lists_only_flagged(client_and_db):
    client, session = client_and_db
    flagged = _seed_flagged(session, name="Flagged Place")
    # A clean, unflagged place should not appear in the needs_review list.
    clean = _seed_flagged(session, name="Clean Place")
    session.get(Place, clean).needs_review = False
    session.commit()

    resp = client.get("/api/places?needs_review=true")
    assert resp.status_code == 200
    ids = [p["id"] for p in resp.json()]
    assert flagged in ids
    assert clean not in ids
