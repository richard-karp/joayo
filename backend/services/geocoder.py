import os
import time

import requests
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

_nominatim = Nominatim(user_agent="social-data-extractor")

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


def _city_from_address(address: str) -> str | None:
    """Extract English city name from a Kakao address string."""
    if not address:
        return None
    parts = address.split()
    if not parts:
        return None
    city = _KR_CITY.get(parts[0])
    return city


def _kakao_full(location_name: str) -> tuple[float | None, float | None, str | None]:
    """Call Kakao keyword search; returns (lat, lng, city)."""
    key = os.getenv("KAKAO_REST_API_KEY")
    if not key:
        return None, None, None
    try:
        resp = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            headers={"Authorization": f"KakaoAK {key}"},
            params={"query": location_name, "size": 1},
            timeout=10,
        )
        resp.raise_for_status()
        docs = resp.json().get("documents", [])
        if docs:
            doc = docs[0]
            lat = float(doc["y"])
            lng = float(doc["x"])
            city = _city_from_address(doc.get("address_name") or doc.get("road_address_name") or "")
            return lat, lng, city
    except Exception:
        pass
    return None, None, None


def _nominatim_geocode(location_name: str, country: str | None) -> tuple[float | None, float | None]:
    query = f"{location_name}, {country}" if country else location_name
    for attempt in range(2):
        try:
            time.sleep(1)  # Nominatim TOS: 1 req/sec
            loc = _nominatim.geocode(query, timeout=10)
            return (loc.latitude, loc.longitude) if loc else (None, None)
        except GeocoderTimedOut:
            if attempt == 0:
                time.sleep(3)
                continue
        except GeocoderServiceError:
            break
    return None, None


def geocode(
    location_name: str,
    country: str | None = None,
) -> tuple[float | None, float | None]:
    if country == "South Korea":
        lat, lng, _ = _kakao_full(location_name)
        if lat is not None:
            return lat, lng
    return _nominatim_geocode(location_name, country)


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
            region_1 = doc.get("region_1depth_name", "")
            city = _KR_CITY.get(region_1)
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
    """Like geocode() but also returns a city string.

    expected_city: if provided and Kakao returns a different city, the geocoded
    result is discarded — catches chain stores where keyword search returns a
    location in a different city than the post describes.
    """
    if country == "South Korea":
        lat, lng, city = _kakao_full(location_name)
        if lat is not None:
            if expected_city and city and expected_city.lower() != city.lower():
                return None, None, None
            return lat, lng, city
    lat, lng = _nominatim_geocode(location_name, country)
    return lat, lng, None
