import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base, get_db
from models import Job, Place


@pytest.fixture()
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_db():
        yield session

    main.app.dependency_overrides[get_db] = override_db
    with TestClient(main.app) as c:
        yield c, session

    session.close()
    Base.metadata.drop_all(engine)
    main.app.dependency_overrides.clear()


def _seed_job(session, place_ids=None) -> str:
    job_id = str(uuid4())
    session.add(Job(
        id=job_id, status="complete", total_urls=1, processed=1,
        pending_review=[], failed_urls=[], updated_place_ids=place_ids or [],
    ))
    session.commit()
    return job_id


def _seed_place(session, job_id: str) -> str:
    place_id = str(uuid4())
    session.add(Place(
        id=place_id, created_by_job_id=job_id,
        source_urls=["https://www.instagram.com/p/A1/"], platform="instagram",
        primary_author="test_user", primary_author_id="1",
        all_authors=[{"username": "test_user", "platform_id": "1", "platform": "instagram"}],
        location_name="Gyeongbokgung Palace", category="see_visit", subcategory="palace",
        summary="Historic palace.", labels=["iconic"], insider_tips="Go early.",
        lat=37.579, lng=126.977,
        raw_caption="Great palace!", tagged_accounts=[], transcript_missing=False,
    ))
    session.commit()
    return place_id


# ── /api/extract ─────────────────────────────────────────────────────────────

def test_extract_with_json_file(client, mocker):
    c, session = client
    mocker.patch("routes.extract.process_job")  # don't run background task
    data = json.dumps({"posts": [{"link": "https://www.instagram.com/p/ABC123/"}]})
    resp = c.post("/api/extract", files={"file": ("saved_posts.json", data, "application/json")})
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_extract_with_url_text(client, mocker):
    c, session = client
    mocker.patch("routes.extract.process_job")
    resp = c.post("/api/extract", data={"urls": "https://www.instagram.com/p/ABC123/"})
    assert resp.status_code == 200
    assert "job_id" in resp.json()


def test_extract_no_input_returns_422(client):
    c, _ = client
    resp = c.post("/api/extract")
    assert resp.status_code == 422


def test_extract_no_supported_urls_returns_422(client):
    c, _ = client
    resp = c.post("/api/extract", data={"urls": "https://example.com/some-random-page"})
    assert resp.status_code == 422


# ── /api/jobs/{job_id} ────────────────────────────────────────────────────────

def test_get_job_returns_correct_shape(client):
    c, session = client
    place_id = _seed_place(session, "tmp")
    job_id = _seed_job(session, place_ids=[place_id])
    # Fix place's job reference
    place = session.get(Place, place_id)
    place.created_by_job_id = job_id
    session.commit()

    resp = c.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "complete"
    assert len(data["places"]) == 1
    assert data["places"][0]["location_name"] == "Gyeongbokgung Palace"


def test_get_job_404_on_unknown(client):
    c, _ = client
    resp = c.get("/api/jobs/nonexistent")
    assert resp.status_code == 404


# ── /api/export/{job_id} ─────────────────────────────────────────────────────

def test_export_csv_returns_csv(client):
    c, session = client
    place_id = _seed_place(session, "tmp")
    job_id = _seed_job(session, place_ids=[place_id])
    resp = c.get(f"/api/export/{job_id}")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "location_name" in resp.text
    assert "Gyeongbokgung Palace" in resp.text


def test_export_404_on_unknown_job(client):
    c, _ = client
    resp = c.get("/api/export/nonexistent")
    assert resp.status_code == 404


# ── /api/leaderboard ─────────────────────────────────────────────────────────

def test_leaderboard_returns_sorted_list(client):
    c, session = client
    job_id = _seed_job(session)
    _seed_place(session, job_id)
    resp = c.get("/api/leaderboard")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["username"] == "test_user"
    assert "total_score" in data[0]
    assert "attributed_count" in data[0]


# ── /api/admin/backfill-normalized-names (#15) ───────────────────────────────

def test_backfill_normalized_names(client, monkeypatch):
    c, session = client
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    job_id = _seed_job(session)
    place_id = _seed_place(session, job_id)  # seeded with normalized_name unset

    resp = c.post("/api/admin/backfill-normalized-names", headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 200
    assert resp.json()["updated"] == 1

    session.expire_all()
    assert session.get(Place, place_id).normalized_name == "gyeongbokgung palace"


def test_backfill_requires_admin_token(client):
    c, _ = client
    resp = c.post("/api/admin/backfill-normalized-names")
    assert resp.status_code == 403


# ── generic-name detection (scrub-generic-names) ─────────────────────────────

@pytest.mark.parametrize("name", [
    "Korean BBQ restaurant (Insadong)",
    "Korean BBQ restaurant (Insadong neighborhood)",
    "Insadong Korean BBQ (unnamed restaurant)",
    "Famous Korean BBQ restaurant in Insadong",
    "Pharmacy (Yakguk)",
    "Hongdae cafe",
    "street food",
])
def test_generic_names_detected(name):
    from routes.admin import _is_generic_name
    assert _is_generic_name(name) is True


@pytest.mark.parametrize("name", [
    "Insadong",                       # a real neighbourhood, not generic
    "Insadong neighborhood",          # the neighbourhood, kept
    "Cafe Bora",                      # a named venue
    "Gwangjang Market",               # a named place containing no bare generic
    "Mankai Apgujeong",               # named venue in an area
])
def test_real_places_not_flagged_generic(name):
    from routes.admin import _is_generic_name
    assert _is_generic_name(name) is False


# ── /api/admin/merge-duplicates ──────────────────────────────────────────────

def _add_place(session, job_id, name, *, category="see_visit", subcategory="landmark",
               lat=None, lng=None, source_urls=None, summary=""):
    pid = str(uuid4())
    session.add(Place(
        id=pid, created_by_job_id=job_id,
        source_urls=source_urls if source_urls is not None else ["https://x/" + pid],
        platform="instagram", primary_author="u", primary_author_id="1",
        all_authors=[{"username": "u", "platform_id": "1", "platform": "instagram"}],
        location_name=name, category=category, subcategory=subcategory,
        summary=summary, labels=[], insider_tips="", lat=lat, lng=lng,
        raw_caption="", tagged_accounts=[], transcript_missing=False,
    ))
    session.commit()
    return pid


def test_merge_duplicates_skips_manual_restorations(client, monkeypatch):
    """A manually-restored record (source_urls emptied) must not be re-merged, even when
    it fuzzy-matches an older record at identical coords in the same category."""
    c, session = client
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    job_id = _seed_job(session)
    # Older real landmark with a source URL
    _add_place(session, job_id, "Cheomseongdae Observatory",
               lat=35.8347, lng=129.2190)
    # Newer manual restoration at identical coords, near-homograph name, no source URLs
    restored_id = _add_place(
        session, job_id, "Cheongwadae Observatory", lat=35.8347, lng=129.2190,
        source_urls=[], summary="Restored: was incorrectly merged into 'Cheomseongdae Observatory'.")

    resp = c.post("/api/admin/merge-duplicates", headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 200
    assert resp.json()["merged"] == 0
    session.expire_all()
    assert session.get(Place, restored_id) is not None


def test_merge_duplicates_merges_exact_normalized_name(client, monkeypatch):
    """Sanity: a genuine duplicate ('Insadong' vs 'Insadong neighborhood') still merges."""
    c, session = client
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    job_id = _seed_job(session)
    kept_id = _add_place(session, job_id, "Insadong neighborhood",
                         category="see_visit", subcategory="neighborhood",
                         lat=37.5728, lng=126.9864)
    dup_id = _add_place(session, job_id, "Insadong",
                        category="see_visit", subcategory="neighborhood",
                        lat=37.5745, lng=126.9856)

    resp = c.post("/api/admin/merge-duplicates", headers={"X-Admin-Token": "secret"})
    assert resp.status_code == 200
    assert resp.json()["merged"] == 1
    session.expire_all()
    assert session.get(Place, dup_id) is None       # newer duplicate absorbed
    assert session.get(Place, kept_id) is not None   # older record kept
