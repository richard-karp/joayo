from services import geocoder
from services.geocoder import (
    GeoResult, geocode_full, _kakao_region_conflict, _cities_compatible,
)


# ── Province-vs-city cross-check (#3) ─────────────────────────────────────────

def test_region_conflict_two_metros():
    # Two recognized metros/provinces that differ → genuine conflict
    assert _kakao_region_conflict("Seoul", "Busan") is True


def test_region_conflict_subprovince_city_not_a_conflict():
    # Claude's city "Suwon" is a sub-province city (not a known region); Kakao
    # reports the province "Gyeonggi" — this must NOT be treated as a conflict.
    assert _kakao_region_conflict("Suwon", "Gyeonggi") is False


def test_region_conflict_same_region():
    assert _kakao_region_conflict("Seoul", "Seoul") is False


def test_region_conflict_missing_values():
    assert _kakao_region_conflict(None, "Seoul") is False
    assert _kakao_region_conflict("Seoul", None) is False


def test_geocode_full_keeps_suwon_result(monkeypatch):
    """A Suwon place keeps its Kakao coords despite the province mismatch."""
    monkeypatch.setattr(
        geocoder, "_kakao_full",
        lambda name, expected_city=None: GeoResult(
            lat=37.2636, lng=127.0286, city="Gyeonggi",
            provider="kakao", place_id="SUWON-1", canonical_name=name,
        ),
    )
    result = geocode_full("Suwon Hwaseong Fortress", country="South Korea", expected_city="Suwon")
    assert result.lat == 37.2636
    assert result.provider == "kakao"
    assert result.place_id == "SUWON-1"


def test_geocode_full_discards_wrong_metro(monkeypatch):
    """A Seoul place that geocodes to Busan (both known metros) is discarded."""
    monkeypatch.setattr(
        geocoder, "_kakao_full",
        lambda name, expected_city=None: GeoResult(
            lat=35.1, lng=129.0, city="Busan", provider="kakao", place_id="BUSAN-1",
        ),
    )
    result = geocode_full("Some Chain Store", country="South Korea", expected_city="Seoul")
    assert result.lat is None
    assert result.place_id is None


# ── Global (Nominatim) city compatibility ─────────────────────────────────────

def test_cities_compatible_substring():
    assert _cities_compatible("New York", "New York City") is True
    assert _cities_compatible("Seoul", "Seoul") is True


def test_cities_compatible_missing():
    assert _cities_compatible(None, "Paris") is True
    assert _cities_compatible("Paris", None) is True


def test_cities_incompatible_different_cities():
    assert _cities_compatible("Paris", "Lyon") is False
