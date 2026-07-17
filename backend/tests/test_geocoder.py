from services import geocoder
from services.geocoder import (
    GeoResult, geocode_full, _kakao_region_conflict, _cities_compatible,
    _normalize_expected_city, canonicalize_city, _region_from_parts, _city_from_address,
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


# ── city canonicalization (duplicate-city prevention) ─────────────────────────

def test_canonicalize_city_alias_map():
    # Freeform variants that denote the same region collapse onto one label, so a
    # place never lands under both "Jeju" and "Jeju Island".
    assert canonicalize_city("Jeju Island") == "Jeju"
    assert canonicalize_city("Jeju-do") == "Jeju"
    assert canonicalize_city("jejudo") == "Jeju"
    assert canonicalize_city("Jeju City") == "Jeju"


def test_canonicalize_city_strips_admin_suffix():
    # Hyphenated city (시) / county (군) suffixes are dropped — the romanized base is
    # the canonical English name, so "Yangpyeong-gun" merges into "Yangpyeong".
    assert canonicalize_city("Yangpyeong-gun") == "Yangpyeong"
    assert canonicalize_city("Sacheon-si") == "Sacheon"
    assert canonicalize_city("Gochang-gun") == "Gochang"


def test_canonicalize_city_strips_country_suffix():
    assert canonicalize_city("Jeju Island, South Korea") == "Jeju"
    assert canonicalize_city("Seoul, Korea") == "Seoul"


def test_canonicalize_city_passthrough_and_idempotent():
    # Non-aliased sub-province cities and already-canonical labels survive unchanged,
    # and canonicalization is a fixed point (safe to re-run over existing rows).
    assert canonicalize_city("Suwon") == "Suwon"
    assert canonicalize_city("Seoul") == "Seoul"
    assert canonicalize_city("Jeju") == "Jeju"
    assert canonicalize_city(canonicalize_city("Jeju Island")) == "Jeju"
    assert canonicalize_city(None) is None
    assert canonicalize_city("") == ""


# ── expected_city normalization (prevention) ──────────────────────────────────

def test_normalize_expected_city_aliases():
    assert _normalize_expected_city("Jeju Island") == "Jeju"
    assert _normalize_expected_city("Jeju-do") == "Jeju"
    assert _normalize_expected_city("jejudo") == "Jeju"


def test_normalize_expected_city_strips_country_suffix():
    assert _normalize_expected_city("Jeju Island, South Korea") == "Jeju"
    assert _normalize_expected_city("Seoul, Korea") == "Seoul"


def test_normalize_expected_city_passes_subprovince_through():
    # A sub-province city Claude named must survive unchanged so its result is kept.
    assert _normalize_expected_city("Suwon") == "Suwon"
    assert _normalize_expected_city(None) is None


def test_normalized_alias_makes_region_conflict_fire():
    # Before normalization "Jeju Island" isn't a known region so no conflict fires.
    assert _kakao_region_conflict("Jeju Island", "South Jeolla") is False
    # After normalization it's "Jeju", a known region, so the conflict is detected.
    assert _kakao_region_conflict(_normalize_expected_city("Jeju Island"), "South Jeolla") is True


def test_geocode_full_discards_mainland_poi_for_jeju_island(monkeypatch):
    """A place Claude labeled "Jeju Island" that geocodes to a mainland region has its
    mismatched coordinates discarded (normalization makes the conflict detectable)."""
    monkeypatch.setattr(
        geocoder, "_kakao_full",
        lambda name, expected_city=None: GeoResult(
            lat=34.79, lng=126.38, city="South Jeolla",
            provider="kakao", place_id="MOKPO-1", canonical_name=name,
        ),
    )
    result = geocode_full("Some Cafe", country="South Korea", expected_city="Jeju Island")
    assert result.lat is None
    assert result.place_id is None


# ── Merged Jeonnam-Gwangju region (2026 Kakao admin change) ───────────────────

def test_region_from_parts_plain_region():
    assert _region_from_parts("서울특별시", None) == "Seoul"
    assert _region_from_parts("경기도", "수원시") == "Gyeonggi"
    assert _region_from_parts("전북특별자치도", None) == "North Jeolla"


def test_region_from_parts_merged_gwangju_vs_south_jeolla():
    # Merged region_1depth: a Gwangju district → Gwangju; a 시/군 → South Jeolla.
    assert _region_from_parts("전남광주통합특별시", "서구") == "Gwangju"
    assert _region_from_parts("전남광주통합특별시", "광산구") == "Gwangju"
    assert _region_from_parts("전남광주통합특별시", "목포시") == "South Jeolla"
    assert _region_from_parts("전남광주통합특별시", "무안군") == "South Jeolla"
    assert _region_from_parts("전남광주통합특별시", None) == "South Jeolla"


def test_city_from_address_merged_region():
    assert _city_from_address("전남광주통합특별시 서구 치평동 1200") == "Gwangju"
    assert _city_from_address("전남광주통합특별시 목포시 대의동2가 1-5") == "South Jeolla"
    assert _city_from_address("서울특별시 종로구 세종로") == "Seoul"


def test_geocode_full_keeps_genuine_jeju_result_for_jeju_island(monkeypatch):
    """A real Jeju POI is kept even though Claude's label was the free-form "Jeju Island"."""
    monkeypatch.setattr(
        geocoder, "_kakao_full",
        lambda name, expected_city=None: GeoResult(
            lat=33.49, lng=126.53, city="Jeju",
            provider="kakao", place_id="JEJU-1", canonical_name=name,
        ),
    )
    result = geocode_full("Jeju Cafe", country="South Korea", expected_city="Jeju Island")
    assert result.lat == 33.49
    assert result.place_id == "JEJU-1"


# ── native_name query preference (Kakao indexes Korean names) ─────────────────

def _capture_kakao_query(monkeypatch):
    """Patch _kakao_full to record the query it was called with."""
    seen = {}

    def _fake(name, expected_city=None):
        seen["query"] = name
        return GeoResult(lat=37.5, lng=127.0, city="Seoul",
                         provider="kakao", place_id="X", canonical_name=name)

    monkeypatch.setattr(geocoder, "_kakao_full", _fake)
    return seen


def test_geocode_full_prefers_native_name_for_korea(monkeypatch):
    # Kakao indexes Korean names; the romanized location_name usually matches nothing.
    seen = _capture_kakao_query(monkeypatch)
    geocode_full("3dae Samgyejangin", country="South Korea",
                 expected_city="Seoul", native_name="삼계장인")
    assert seen["query"] == "삼계장인"


def test_geocode_full_falls_back_to_location_name_without_native(monkeypatch):
    seen = _capture_kakao_query(monkeypatch)
    geocode_full("Gyeongbokgung Palace", country="South Korea", expected_city="Seoul")
    assert seen["query"] == "Gyeongbokgung Palace"


def test_geocode_full_ignores_blank_native_name(monkeypatch):
    seen = _capture_kakao_query(monkeypatch)
    geocode_full("Gyeongbokgung Palace", country="South Korea",
                 expected_city="Seoul", native_name="   ")
    assert seen["query"] == "Gyeongbokgung Palace"


def test_geocode_full_native_name_only_for_korea(monkeypatch):
    # Outside Korea we don't hit Kakao at all — native_name must not change routing.
    called = {"kakao": False}
    monkeypatch.setattr(
        geocoder, "_kakao_full",
        lambda name, expected_city=None: called.__setitem__("kakao", True) or GeoResult(),
    )
    monkeypatch.setattr(
        geocoder, "_nominatim_geocode",
        lambda name, country: GeoResult(lat=48.85, lng=2.35, city="Paris", provider="nominatim"),
    )
    result = geocode_full("Louvre", country="France", native_name="ルーブル")
    assert called["kakao"] is False
    assert result.provider == "nominatim"
