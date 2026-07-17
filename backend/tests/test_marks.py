from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base, get_db
from models import Place, PlaceMark
from tests.conftest import make_raw_post


def _make_test_app(session):
    def override_db():
        yield session

    main.app.dependency_overrides[get_db] = override_db
    return TestClient(main.app)


def _seed_place(session, name="Gyeongbokgung Palace") -> str:
    raw = make_raw_post()
    place = Place(
        id=str(uuid4()),
        created_by_job_id="test-job",
        source_urls=[raw.url],
        platform="instagram",
        primary_author="travel_user",
        primary_author_id="111",
        all_authors=[{"username": "travel_user", "platform_id": "111", "platform": "instagram"}],
        location_name=name,
        category="see_visit",
        subcategory="palace",
        summary="Historic palace.",
        labels=[],
        insider_tips="",
        raw_caption=raw.caption,
        tagged_accounts=[],
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


# ── rating ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rating", ["down", "up", "double"])
def test_rate_place(client_and_db, rating):
    client, _ = client_and_db
    place_id = _seed_place(client_and_db[1])
    resp = client.post(f"/api/places/{place_id}/rating", json={"rating": rating})
    assert resp.status_code == 200
    assert resp.json()["my_rating"] == rating


def test_change_rating_upserts_single_row(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/rating", json={"rating": "up"})
    resp = client.post(f"/api/places/{place_id}/rating", json={"rating": "double"})
    assert resp.json()["my_rating"] == "double"
    assert session.query(PlaceMark).filter(PlaceMark.place_id == place_id).count() == 1


def test_clear_rating_deletes_row_when_no_wishlist(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/rating", json={"rating": "up"})
    resp = client.post(f"/api/places/{place_id}/rating", json={"rating": None})
    assert resp.json()["my_rating"] is None
    # Row is deleted only because both signals are now empty.
    assert session.query(PlaceMark).filter(PlaceMark.place_id == place_id).count() == 0


def test_rate_unknown_place(client_and_db):
    client, _ = client_and_db
    resp = client.post("/api/places/nonexistent/rating", json={"rating": "up"})
    assert resp.status_code == 404


# ── want to go (wishlist) ─────────────────────────────────────────────────────

def test_set_and_clear_want_to_go(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    resp = client.post(f"/api/places/{place_id}/want-to-go", json={"want_to_go": True})
    assert resp.json()["want_to_go"] is True

    resp = client.post(f"/api/places/{place_id}/want-to-go", json={"want_to_go": False})
    assert resp.json()["want_to_go"] is False
    assert session.query(PlaceMark).filter(PlaceMark.place_id == place_id).count() == 0


def test_rating_auto_clears_want_to_go(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/want-to-go", json={"want_to_go": True})
    # Rating a place marks it visited → it drops off the wishlist.
    resp = client.post(f"/api/places/{place_id}/rating", json={"rating": "up"})
    body = resp.json()
    assert body["my_rating"] == "up"
    assert body["want_to_go"] is False
    # One row still carries the rating signal (not deleted).
    mark = session.query(PlaceMark).filter(PlaceMark.place_id == place_id).one()
    assert mark.rating == "up" and mark.want_to_go is False


def test_clearing_rating_keeps_existing_wishlist(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/want-to-go", json={"want_to_go": True})
    client.post(f"/api/places/{place_id}/rating", json={"rating": "up"})   # clears wishlist
    client.post(f"/api/places/{place_id}/want-to-go", json={"want_to_go": True})  # re-add
    resp = client.post(f"/api/places/{place_id}/rating", json={"rating": None})   # clear rating
    body = resp.json()
    assert body["my_rating"] is None
    assert body["want_to_go"] is True  # wishlist survives clearing the rating


def test_marks_dont_bleed_between_places(client_and_db):
    client, session = client_and_db
    id1 = _seed_place(session)
    id2 = _seed_place(session)
    client.post(f"/api/places/{id1}/rating", json={"rating": "double"})
    client.post(f"/api/places/{id2}/want-to-go", json={"want_to_go": True})
    m1 = session.query(PlaceMark).filter(PlaceMark.place_id == id1).one()
    m2 = session.query(PlaceMark).filter(PlaceMark.place_id == id2).one()
    assert m1.rating == "double" and m1.want_to_go is False
    assert m2.rating is None and m2.want_to_go is True


# ── /api/places filters ───────────────────────────────────────────────────────

def test_places_filters_rated_and_want_to_go(client_and_db):
    client, session = client_and_db
    rated_id = _seed_place(session, name="Rated Place")
    wish_id = _seed_place(session, name="Wishlist Place")
    _seed_place(session, name="Untouched Place")
    client.post(f"/api/places/{rated_id}/rating", json={"rating": "up"})
    client.post(f"/api/places/{wish_id}/want-to-go", json={"want_to_go": True})

    rated = client.get("/api/places?rated=true").json()
    assert {p["id"] for p in rated} == {rated_id}

    wish = client.get("/api/places?want_to_go=true").json()
    assert {p["id"] for p in wish} == {wish_id}

    all_places = client.get("/api/places").json()
    assert len(all_places) == 3
