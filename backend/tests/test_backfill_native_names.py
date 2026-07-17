"""Tests for the native-name pin backfill (mocked LLM + mocked Kakao).

Covers the "accept all, flag guesses" decision: a confident match is written
clean, a low-confidence match is written but stamped needs_review, and a
region-rejected / unmatched row stays NULL.
"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backfill_native_names as backfill
from database import Base
from models import Place
from services import extractor, geocoder
from services.geocoder import GeoResult


# ── confidence gate ───────────────────────────────────────────────────────────

def test_confidence_high_match_not_flagged():
    geo = GeoResult(canonical_name="경복궁", address="서울특별시 종로구")
    score, needs_review = backfill._confidence("경복궁", geo)
    assert score == 100
    assert needs_review is False


def test_confidence_low_match_flagged():
    # The wrong-venue clinic risk: 수아의원 → 수아로피부과의원 (a different clinic).
    geo = GeoResult(canonical_name="수아로피부과의원", address="서울특별시 강남구")
    score, needs_review = backfill._confidence("수아의원", geo)
    assert score < backfill._CONF_THRESHOLD
    assert needs_review is True


# ── LLM lookup wrapper ────────────────────────────────────────────────────────

def _fake_client(native_name):
    block = SimpleNamespace(type="tool_use", name="report_native_name",
                            input={"native_name": native_name})
    resp = SimpleNamespace(content=[block])
    return SimpleNamespace(messages=SimpleNamespace(create=lambda **_: resp))


def test_llm_native_name_returns_name():
    client = _fake_client("경복궁")
    assert backfill._llm_native_name(client, "Gyeongbokgung", "Seoul", None, None) == "경복궁"


def test_llm_native_name_null_returns_none():
    client = _fake_client(None)
    assert backfill._llm_native_name(client, "Mystery Spot", "Seoul", None, None) is None


# ── end-to-end run() over a temp DB ───────────────────────────────────────────

def _seeded_engine():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add_all([
        Place(id="r1", is_place=True, lat=None, location_name="Samgyejangin",
              city="Seoul", neighborhood="Seocho", source_urls=["u1"]),
        Place(id="r2", is_place=True, lat=None, location_name="Sooa Clinic",
              city="Seoul", neighborhood="Gangnam", source_urls=["u2"]),
        Place(id="r3", is_place=True, lat=None, location_name="Ghost Place",
              city="Seoul", source_urls=["u3"]),
    ])
    s.commit()
    s.close()
    return Session


_NATIVE = {"Samgyejangin": "삼계장인", "Sooa Clinic": "수아의원", "Ghost Place": "유령"}

_GEO = {
    "삼계장인": GeoResult(lat=37.49, lng=127.01, city="Seoul", provider="kakao",
                       place_id="P1", canonical_name="3대삼계장인", address="서울 서초구"),
    "수아의원": GeoResult(lat=37.51, lng=127.05, city="Seoul", provider="kakao",
                       place_id="P2", canonical_name="수아로피부과의원", address="서울 강남구"),
    "유령": GeoResult(),  # no Kakao match / region-rejected → stays NULL
}


def test_run_accepts_flags_and_skips(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("KAKAO_REST_API_KEY", "test")
    Session = _seeded_engine()

    monkeypatch.setattr(backfill, "SessionLocal", Session)
    monkeypatch.setattr(backfill, "_backup_db", lambda: "backup")
    monkeypatch.setattr(extractor, "_get_client", lambda: object())
    monkeypatch.setattr(backfill, "_llm_native_name",
                        lambda client, name, city, nbhd, cap: _NATIVE.get(name))
    monkeypatch.setattr(geocoder, "geocode_full",
                        lambda name, country=None, expected_city=None, native_name=None:
                        _GEO.get(native_name, GeoResult()))

    backfill.run(apply=True, limit=None)

    s = Session()
    r1, r2, r3 = s.get(Place, "r1"), s.get(Place, "r2"), s.get(Place, "r3")

    # Confident match: written clean.
    assert (round(r1.lat, 2), round(r1.lng, 2)) == (37.49, 127.01)
    assert r1.geocoder_place_id == "P1"
    assert r1.native_name == "삼계장인"
    assert r1.needs_review is False

    # Low-confidence match: written but flagged for review.
    assert r2.lat is not None
    assert r2.native_name == "수아의원"
    assert r2.needs_review is True

    # No geocode: left untouched.
    assert r3.lat is None
    assert r3.geocoder_place_id is None
    s.close()
