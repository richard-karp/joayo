import os
import time
from dataclasses import dataclass

import requests
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

_nominatim = Nominatim(user_agent="social-data-extractor")


@dataclass
class GeoResult:
    """Result of a geocode lookup. Replaces the growing return tuples."""
    lat: float | None = None
    lng: float | None = None
    city: str | None = None
    provider: str | None = None        # "kakao" | "nominatim"
    place_id: str | None = None        # stable external POI id
    canonical_name: str | None = None  # provider's canonical name for the POI

# Maps the first word of a Kakao address to an English city/province name.
# Major metros are their own city; provinces fall back to province name.
_KR_CITY = {
    "서울특별시": "Seoul",     "서울": "Seoul",
    "부산광역시": "Busan",     "부산": "Busan",
    "인천광역시": "Incheon",   "인천": "Incheon",
    "대구광역시": "Daegu",     "대구": "Daegu",
    "광주광역시": "Gwangju",   "광주": "Gwangju",
    "대전광역시": "Daejeon",   "대전": "Daejeon",
    "울산광역시": "Ulsan",     "울산": "Ulsan",
    "세종특별자치시": "Sejong", "세종": "Sejong",
    "경기도": "Gyeonggi",      "경기": "Gyeonggi",
    "강원특별자치도": "Gangwon","강원도": "Gangwon", "강원": "Gangwon",
    "충청북도": "North Chungcheong", "충북": "North Chungcheong",
    "충청남도": "South Chungcheong", "충남": "South Chungcheong",
    "전북특별자치도": "North Jeolla", "전라북도": "North Jeolla", "전북": "North Jeolla",
    "전라남도": "South Jeolla", "전남": "South Jeolla",
    "경상북도": "North Gyeongsang", "경북": "North Gyeongsang",
    "경상남도": "South Gyeongsang", "경남": "South Gyeongsang",
    "제주특별자치도": "Jeju",   "제주": "Jeju",
}


# In 2026 Kakao merged Gwangju metro and South Jeolla province into a single
# region_1depth ("전남광주통합특별시"); the metro/province distinction now lives at
# region_2depth. A region_2depth in Gwangju's five autonomous districts (구) means
# Gwangju, anything else (목포시, 여수시, 무안군 …) is South Jeolla. Without this,
# every Gwangju/South Jeolla coord and address resolves to None.
_MERGED_JEONNAM_GWANGJU = "전남광주통합특별시"
_GWANGJU_DISTRICTS = frozenset({"동구", "서구", "남구", "북구", "광산구"})


def _region_from_parts(region_1: str | None, region_2: str | None) -> str | None:
    """Map a Kakao region_1depth name (plus region_2depth for the merged
    Jeonnam-Gwangju region) to an English city/province name."""
    if region_1 == _MERGED_JEONNAM_GWANGJU:
        return "Gwangju" if (region_2 or "") in _GWANGJU_DISTRICTS else "South Jeolla"
    return _KR_CITY.get(region_1 or "")


# English names Kakao maps addresses to — used to distinguish a genuine
# metro/province conflict from a sub-province city Claude named (e.g. "Suwon").
_KNOWN_KR_REGIONS = frozenset(v.lower() for v in _KR_CITY.values())


# Free-form city labels Claude emits that denote a canonical Kakao region. Normalizing
# these before the region-conflict check lets "Jeju Island" (which isn't itself a known
# region name) be recognized as "Jeju", so wrong-region coordinates are discarded
# instead of slipping past the check. Keyed lowercase.
_EXPECTED_CITY_ALIASES = {
    "jeju island": "Jeju",
    "jeju-do": "Jeju",
    "jeju do": "Jeju",
    "jejudo": "Jeju",
    "jeju city": "Jeju",
    "seoul city": "Seoul",
    "busan city": "Busan",
}


def _normalize_expected_city(expected_city: str | None) -> str | None:
    """Map a free-form Claude city label onto a canonical Kakao region name when it
    clearly denotes one, so `_kakao_region_conflict` can compare like with like.

    Strips a trailing country qualifier ("Jeju Island, South Korea") and applies a
    small alias map. Non-aliased sub-province cities (Suwon, Jeonju, …) pass through
    unchanged so their legitimate results are still kept.
    """
    if not expected_city:
        return expected_city
    s = expected_city.strip()
    for suffix in (", south korea", ", korea"):
        if s.lower().endswith(suffix):
            s = s[: -len(suffix)].strip()
            break
    return _EXPECTED_CITY_ALIASES.get(s.lower(), s)


def _city_from_address(address: str) -> str | None:
    """Extract English city/province name from a Kakao address string."""
    if not address:
        return None
    parts = address.split()
    if not parts:
        return None
    return _region_from_parts(parts[0], parts[1] if len(parts) > 1 else None)


def _kakao_region_conflict(expected_city: str | None, result_city: str | None) -> bool:
    """True only when expected and result are BOTH recognized metros/provinces that differ.

    If Claude's city is a sub-province city not in the known set (e.g. "Suwon" while
    Kakao reports the province "Gyeonggi"), that is NOT a conflict — the result is kept.
    """
    if not expected_city or not result_city:
        return False
    e, r = expected_city.strip().lower(), result_city.strip().lower()
    if e == r:
        return False
    return e in _KNOWN_KR_REGIONS and r in _KNOWN_KR_REGIONS


def _kakao_full(location_name: str, expected_city: str | None = None) -> GeoResult:
    """Call Kakao keyword search. Requests several docs and prefers one whose
    address city matches expected_city (chain-store disambiguation)."""
    key = os.getenv("KAKAO_REST_API_KEY")
    if not key:
        return GeoResult()
    try:
        resp = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            headers={"Authorization": f"KakaoAK {key}"},
            params={"query": location_name, "size": 10},
            timeout=10,
        )
        resp.raise_for_status()
        docs = resp.json().get("documents", [])
        if not docs:
            return GeoResult()

        def _doc_city(doc: dict) -> str | None:
            return _city_from_address(doc.get("address_name") or doc.get("road_address_name") or "")

        chosen = docs[0]
        if expected_city:
            ec = expected_city.strip().lower()
            for doc in docs:
                dc = _doc_city(doc)
                if dc and dc.lower() == ec:
                    chosen = doc
                    break

        return GeoResult(
            lat=float(chosen["y"]),
            lng=float(chosen["x"]),
            city=_doc_city(chosen),
            provider="kakao",
            place_id=chosen.get("id"),
            canonical_name=chosen.get("place_name"),
        )
    except Exception:
        pass
    return GeoResult()


def _cities_compatible(expected_city: str | None, result_city: str | None) -> bool:
    """Lenient city cross-check for global (non-Kakao) results: treat as compatible
    unless both are present and neither contains the other (avoids "New York" vs
    "New York City" false discards while still catching "Paris" vs "Lyon")."""
    if not expected_city or not result_city:
        return True
    e, r = expected_city.strip().lower(), result_city.strip().lower()
    return e in r or r in e


def _nominatim_geocode(location_name: str, country: str | None) -> GeoResult:
    query = f"{location_name}, {country}" if country else location_name
    for attempt in range(2):
        try:
            time.sleep(1)  # Nominatim TOS: 1 req/sec
            loc = _nominatim.geocode(query, timeout=10, addressdetails=True)
            if not loc:
                return GeoResult()
            raw = getattr(loc, "raw", {}) or {}
            addr = raw.get("address", {}) or {}
            city = (addr.get("city") or addr.get("town") or addr.get("village")
                    or addr.get("municipality") or addr.get("state"))
            osm_type = raw.get("osm_type")
            osm_id = raw.get("osm_id")
            place_id = f"{osm_type}:{osm_id}" if osm_type and osm_id else (
                str(raw["place_id"]) if raw.get("place_id") else None
            )
            return GeoResult(
                lat=loc.latitude,
                lng=loc.longitude,
                city=city,
                provider="nominatim",
                place_id=place_id,
                canonical_name=raw.get("name") or raw.get("display_name"),
            )
        except GeocoderTimedOut:
            if attempt == 0:
                time.sleep(3)
                continue
        except GeocoderServiceError:
            break
    return GeoResult()


def geocode_full(
    location_name: str,
    country: str | None = None,
    expected_city: str | None = None,
) -> GeoResult:
    """Geocode a place, returning a rich GeoResult (provider, place_id, city).

    Applies an expected_city cross-check to discard wrong-city chain-store hits —
    strictly for genuine Korean metro/province conflicts, leniently elsewhere.
    """
    if country == "South Korea":
        expected_city = _normalize_expected_city(expected_city)
        result = _kakao_full(location_name, expected_city=expected_city)
        if result.lat is not None:
            if _kakao_region_conflict(expected_city, result.city):
                return GeoResult()
            return result
    result = _nominatim_geocode(location_name, country)
    if result.lat is not None and not _cities_compatible(expected_city, result.city):
        return GeoResult()
    return result


def geocode(
    location_name: str,
    country: str | None = None,
) -> tuple[float | None, float | None]:
    result = geocode_full(location_name, country)
    return result.lat, result.lng


def city_from_coords(lat: float, lng: float) -> str | None:
    """Reverse geocode coordinates to a city name using Kakao coord2regioncode."""
    key = os.getenv("KAKAO_REST_API_KEY")
    if not key:
        return None
    try:
        resp = requests.get(
            "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json",
            headers={"Authorization": f"KakaoAK {key}"},
            params={"x": lng, "y": lat},
            timeout=10,
        )
        resp.raise_for_status()
        docs = resp.json().get("documents", [])
        for doc in docs:
            city = _region_from_parts(
                doc.get("region_1depth_name"), doc.get("region_2depth_name")
            )
            if city:
                return city
    except Exception:
        pass
    return None


def geocode_with_city(
    location_name: str,
    country: str | None = None,
    expected_city: str | None = None,
) -> tuple[float | None, float | None, str | None]:
    """Backward-compatible tuple wrapper around geocode_full()."""
    result = geocode_full(location_name, country, expected_city)
    return result.lat, result.lng, result.city
