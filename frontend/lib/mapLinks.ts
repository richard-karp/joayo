import type { Place } from "@/types";

// Deep links into Korean map apps. Kakao is preferred because our geocoder is Kakao,
// so we usually have a stable POI id; Naver is a name/coord search fallback.

/** "Open in Kakao Map" — the POI page when we have its id, else a pinned-location link. */
export function kakaoMapUrl(place: Place): string | null {
  if (place.geocoder_place_id) {
    return `https://place.map.kakao.com/${place.geocoder_place_id}`;
  }
  if (place.lat != null && place.lng != null) {
    const name = encodeURIComponent(place.location_name ?? "place");
    return `https://map.kakao.com/link/map/${name},${place.lat},${place.lng}`;
  }
  return null;
}

/** "Directions" — Kakao route-to-here for a coordinate. */
export function kakaoDirectionsUrl(place: Place): string | null {
  if (place.lat == null || place.lng == null) return null;
  const name = encodeURIComponent(place.location_name ?? "place");
  return `https://map.kakao.com/link/to/${name},${place.lat},${place.lng}`;
}

/** Naver web-map search fallback (we have no stable Naver POI id in our data). */
export function naverMapUrl(place: Place): string | null {
  const q = (place.location_name ?? "").trim();
  if (!q) return null;
  return `https://map.naver.com/p/search/${encodeURIComponent(q)}`;
}
