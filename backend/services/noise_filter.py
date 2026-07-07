"""Data-derived ambient-noise filter for the places table.

Demotes (sets is_context=True) — never deletes — the entries that are the
collection's ambient *setting* rather than a recommendation:

  - the DOMINANT country: the value that is the `country` of more than
    `country_threshold` of all places, when it appears as its own item
    (e.g. "South Korea" in a Korea trip).
  - the DOMINANT city: likewise for `city` above `city_threshold`, when it
    appears as a BARE item with no neighborhood (e.g. "Seoul").
  - known media titles: a tiny denylist — the one class that can't be derived
    from the data (a TV show isn't a place at any frequency).

Everything that is one-of-many is KEPT. A lesser-known country or city (Albania,
Gangneung) is only ever flagged when THIS collection is overwhelmingly about it,
in which case naming it adds no signal anyway. On a multi-country / multi-city
collection no single value clears the threshold, so nothing is flagged.

Idempotent: recomputes is_context for every row on each run, so flags self-correct
as a collection grows or its center of gravity shifts.
"""
from collections import Counter

from models import Place

# The one class that cannot be data-derived. Keep this tiny and explicit.
DEFAULT_MEDIA = {"culinary class wars", "squid game"}

# Robust country detection via pycountry when available; static fallback otherwise.
try:
    import pycountry
    _COUNTRY_NAMES = {
        v.casefold()
        for c in pycountry.countries
        for v in (getattr(c, "name", None), getattr(c, "official_name", None),
                  getattr(c, "common_name", None))
        if v
    }
except Exception:  # pragma: no cover - pycountry not installed
    _COUNTRY_NAMES = set()

# Common colloquial short forms → canonical name (covers the frequent cases).
_COUNTRY_ALIASES = {
    "korea": "south korea", "s korea": "south korea", "s. korea": "south korea",
    "usa": "united states", "us": "united states", "u.s.": "united states",
    "u.s.a.": "united states", "america": "united states",
    "uk": "united kingdom", "u.k.": "united kingdom", "britain": "united kingdom",
    "uae": "united arab emirates",
}


def _norm(s):
    return (s or "").strip().casefold()


def _canon_country(name):
    """Return a canonical casefolded country name if `name` denotes a country, else None."""
    n = _norm(name)
    if not n:
        return None
    n = _COUNTRY_ALIASES.get(n, n)
    if n in _COUNTRY_NAMES or n in set(_COUNTRY_ALIASES.values()):
        return n
    return None


def compute_ambient(session, *, country_threshold=0.6, city_threshold=0.5,
                    media_denylist=None):
    """Pure computation — no writes. Returns dict with the derived home base and a
    per-place plan: [(place_id, reason_or_None), ...]."""
    media = {_norm(m) for m in (DEFAULT_MEDIA if media_denylist is None else media_denylist)}
    rows = session.query(Place.id, Place.location_name, Place.country,
                         Place.city, Place.neighborhood).all()
    total = len(rows)
    if not total:
        return {"dominant_country": None, "dominant_city": None, "plan": []}

    country_counts = Counter(_norm(r.country) for r in rows if _norm(r.country))
    city_counts = Counter(_norm(r.city) for r in rows if _norm(r.city))

    dominant_country = None
    if country_counts:
        name, cnt = country_counts.most_common(1)[0]
        if cnt / total >= country_threshold:
            dominant_country = name

    dominant_city = None
    if city_counts:
        name, cnt = city_counts.most_common(1)[0]
        if cnt / total >= city_threshold:
            dominant_city = name

    plan = []
    for r in rows:
        ln = _norm(r.location_name)
        reason = None
        if ln in media:
            reason = "media"
        elif dominant_country and _canon_country(r.location_name) == dominant_country:
            reason = "home_country"
        elif dominant_city and ln == dominant_city and not _norm(r.neighborhood):
            reason = "home_city"
        plan.append((r.id, reason))

    return {"dominant_country": dominant_country, "dominant_city": dominant_city, "plan": plan}


def flag_ambient_places(session, *, country_threshold=0.6, city_threshold=0.5,
                        media_denylist=None, apply=True):
    """Recompute and (by default) persist is_context for every place. Idempotent.

    Returns a summary: derived home base, counts per reason, and rows changed.
    Pass apply=False for a dry run.
    """
    res = compute_ambient(session, country_threshold=country_threshold,
                          city_threshold=city_threshold, media_denylist=media_denylist)
    counts = {"home_country": 0, "home_city": 0, "media": 0}
    changed = 0
    for pid, reason in res["plan"]:
        should = reason is not None
        if should:
            counts[reason] += 1
        if apply:
            place = session.get(Place, pid)
            if bool(place.is_context) != should:
                place.is_context = should
                changed += 1
    if apply:
        session.commit()
    return {
        "dominant_country": res["dominant_country"],
        "dominant_city": res["dominant_city"],
        "flagged": counts,
        "changed": changed,
    }
