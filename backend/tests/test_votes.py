from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base, get_db
from models import Place, Vote
from tests.conftest import make_raw_post


def _make_test_app(session):
    def override_db():
        yield session

    main.app.dependency_overrides[get_db] = override_db
    return TestClient(main.app)


def _seed_place(session) -> str:
    raw = make_raw_post()
    place = Place(
        id=str(uuid4()),
        created_by_job_id="test-job",
        source_urls=[raw.url],
        platform="instagram",
        primary_author="travel_user",
        primary_author_id="111",
        all_authors=[{"username": "travel_user", "platform_id": "111", "platform": "instagram"}],
        location_name="Gyeongbokgung Palace",
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


def test_vote_up(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    resp = client.post(f"/api/places/{place_id}/vote", json={"vote": "up"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["vote_score"] == 1
    assert data["current_vote"] == "up"


def test_vote_down(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    resp = client.post(f"/api/places/{place_id}/vote", json={"vote": "down"})
    assert resp.status_code == 200
    assert resp.json()["vote_score"] == -1
    assert resp.json()["current_vote"] == "down"


def test_vote_up_then_down(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/vote", json={"vote": "up"})
    resp = client.post(f"/api/places/{place_id}/vote", json={"vote": "down"})
    assert resp.json()["vote_score"] == -1


def test_vote_down_then_up(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/vote", json={"vote": "down"})
    resp = client.post(f"/api/places/{place_id}/vote", json={"vote": "up"})
    assert resp.json()["vote_score"] == 1


def test_undo_vote(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/vote", json={"vote": "up"})
    resp = client.post(f"/api/places/{place_id}/vote", json={"vote": None})
    assert resp.json()["vote_score"] == 0
    assert resp.json()["current_vote"] is None


def test_votes_dont_bleed_between_places(client_and_db):
    client, session = client_and_db
    id1 = _seed_place(session)
    id2 = _seed_place(session)
    client.post(f"/api/places/{id1}/vote", json={"vote": "up"})
    resp = client.post(f"/api/places/{id2}/vote", json={"vote": "down"})
    assert resp.json()["vote_score"] == -1

    resp1 = client.get(f"/api/jobs/nonexistent")  # just check id1 score separately
    vote_row = session.query(Vote).filter(Vote.place_id == id1, Vote.voter == "default").first()
    assert vote_row.value == 1


def test_upsert_no_duplicate_vote_rows(client_and_db):
    client, session = client_and_db
    place_id = _seed_place(session)
    client.post(f"/api/places/{place_id}/vote", json={"vote": "up"})
    client.post(f"/api/places/{place_id}/vote", json={"vote": "up"})
    count = session.query(Vote).filter(Vote.place_id == place_id).count()
    assert count == 1


def test_vote_unknown_place(client_and_db):
    client, _ = client_and_db
    resp = client.post("/api/places/nonexistent/vote", json={"vote": "up"})
    assert resp.status_code == 404
