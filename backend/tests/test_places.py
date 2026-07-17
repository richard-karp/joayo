from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import main
from database import Base, get_db
from models import Place


@pytest.fixture()
def client_and_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_db():
        yield session

    main.app.dependency_overrides[get_db] = override_db
    yield TestClient(main.app), session
    session.close()
    Base.metadata.drop_all(engine)
    main.app.dependency_overrides.clear()


def _add(session, **kw) -> str:
    defaults = dict(
        id=str(uuid4()), is_place=True, country="South Korea", city="Seoul",
        location_name="A Place", source_urls=["u"],
    )
    defaults.update(kw)
    p = Place(**defaults)
    session.add(p)
    session.commit()
    return p.id


# ── GET /api/places/{id} ──────────────────────────────────────────────────────

def test_get_place_detail(client_and_db):
    client, session = client_and_db
    pid = _add(session, location_name="Gyeongbokgung Palace",
               neighborhood="Jongno", geocoder_place_id="KAKAO-1")
    resp = client.get(f"/api/places/{pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == pid
    assert body["neighborhood"] == "Jongno"
    assert body["geocoder_place_id"] == "KAKAO-1"


def test_get_place_detail_404(client_and_db):
    client, _ = client_and_db
    assert client.get("/api/places/nope").status_code == 404


# ── neighborhood filter + facet ───────────────────────────────────────────────

def test_neighborhood_filter(client_and_db):
    client, session = client_and_db
    _add(session, location_name="Insadong Spot", neighborhood="Insadong")
    _add(session, location_name="Hongdae Spot", neighborhood="Hongdae")
    resp = client.get("/api/places?neighborhood=Insadong").json()
    assert [p["location_name"] for p in resp] == ["Insadong Spot"]


def test_filters_include_neighborhoods(client_and_db):
    client, session = client_and_db
    _add(session, neighborhood="Insadong")
    _add(session, neighborhood="Insadong")
    _add(session, neighborhood="Hongdae")
    facets = client.get("/api/filters").json()
    nbhds = {n["name"]: n["place_count"] for n in facets["neighborhoods"]}
    assert nbhds == {"Insadong": 2, "Hongdae": 1}
    assert facets["neighborhoods"][0]["city"] == "Seoul"


# ── sort=new (earliest_date_posted) ───────────────────────────────────────────

def test_sort_new_orders_by_post_date(client_and_db):
    client, session = client_and_db
    _add(session, location_name="Old", earliest_date_posted=datetime(2022, 1, 1))
    _add(session, location_name="New", earliest_date_posted=datetime(2024, 6, 1))
    _add(session, location_name="Undated", earliest_date_posted=None)
    names = [p["location_name"] for p in client.get("/api/places?sort=new").json()]
    # Most recent first; the undated row sorts last.
    assert names[:2] == ["New", "Old"]
    assert names[-1] == "Undated"
