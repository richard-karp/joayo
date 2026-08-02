"""Data-derived ambient-noise filter for the places table.

Demotes (sets is_context=True) — never deletes — the entries that are the
collection's ambient *setting* rather than a recommendation:

  - the DOMINANT country: the value that is the `country` of more than
    `country_threshold` of all places, when it appears as its own item
    (e.g. "South Korea" in a Korea trip).
  - the DOMINANT city: likewise for `city` above `city_threshold`, when it
    appears as a BARE item with no neighborhood (e.g. "Seoul").
  - ANY administrative geography: an item whose name is just a city or
    neighborhood label that this collection already uses as a location *field* —
    "Busan", "Hongdae", "Jeju Island". These are the setting a recommendation
    sits in, not the recommendation; geocoding them drops a pin on an area
    centroid (our "Busan" landed on Haeundae Beach). Unlike the dominant-city
    rule this needs no *frequency* threshold — naming the area you are already
    browsing adds nothing however often it comes up — but it does require the
    item's own subcategory to be an area type, and a neighborhood label has to
    be reused (`_MIN_LABEL_USES`) and confined to a single city before it counts
    as a label at all. See the constants below for why each guard is load-bearing.
  - known media titles: a tiny denylist — the one class that can't be derived
    from the data (a TV show isn't a place at any frequency).

Everything that is one-of-many is KEPT. A lesser-known country or city (Albania,
Gangneung) is only ever flagged when THIS collection is overwhelmingly about it,
in which case naming it adds no signal anyway. On a multi-country / multi-city
collection no single value clears the threshold, so nothing is flagged.

Idempotent: recomputes is_context for every row on each run, so flags self-correct
as a collection grows or its center of gravity shifts.

Note: this filter fully OWNS the is_context column — every run overwrites it for
all rows. Manual or out-of-band is_context edits will not survive the next job.
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
# Fold alias targets into the name set so a single membership test covers both — and
# so the no-pycountry fallback still recognizes these canonical names.
_COUNTRY_NAMES |= set(_COUNTRY_ALIASES.values())


# Generic geography words that trail an area name ("Jeju Island", "Busan City").
# Stripped before matching a name against the collection's own location labels, so
# the label "Jeju" still catches the item "Jeju Island". Deliberately excludes the
# Korean administrative suffixes (-dong, -gu, -ro) — those are load-bearing parts of
# real venue names ("Ikseondong Hanok Village") and stripping them over-matches.
_GEO_SUFFIXES = frozenset({
    "island", "city", "province", "prefecture", "county", "district",
    "area", "region", "neighborhood", "neighbourhood",
})

# Subcategories that describe an AREA rather than somewhere with a front door. The
# geography rule requires one of these, so a venue that merely shares its name with a
# location label ("Seoul Forest" the park, "Gwangjang Market", "Incheon Airport")
# stays on the map. Extraction assigns these, so they track the LLM's own judgement.
_AREA_SUBCATEGORIES = frozenset({
    "neighborhood", "neighbourhood", "district", "shopping_district",
    "island", "city", "region", "province", "day_trip", "area",
})

# A neighborhood label has to be shared by at least this many rows to count as real
# geography. Venue names leak into the neighborhood column one-off ("Seoul Forest",
# "Miryang Market" each appear once) and would otherwise become false labels; genuine
# areas are reused across posts. City labels skip this — they come from the geocoder.
_MIN_LABEL_USES = 2


def _norm(s):
    return (s or "").strip().casefold()


def _geo_key(name):
    """Casefolded name with a trailing generic geography word removed.

    "Jeju Island" -> "jeju", "Busan" -> "busan". Returns "" for a name that is
    ONLY a suffix word, so a bare "Island" never matches anything.
    """
    tokens = _norm(name).replace(",", " ").split()
    while tokens and tokens[-1] in _GEO_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _canon_country(name):
    """Return a canonical casefolded country name if `name` denotes a country, else None."""
    n = _norm(name)
    if not n:
        return None
    n = _COUNTRY_ALIASES.get(n, n)
    return n if n in _COUNTRY_NAMES else None


def compute_ambient(session, *, country_threshold=0.6, city_threshold=0.5,
                    media_denylist=None):
    """Pure computation — no writes. Returns dict with the derived home base and a
    per-place plan: [(place_id, reason_or_None), ...]."""
    media = {_norm(m) for m in (DEFAULT_MEDIA if media_denylist is None else media_denylist)}
    rows = session.query(Place.id, Place.location_name, Place.country,
                         Place.city, Place.neighborhood, Place.subcategory).all()
    total = len(rows)
    if not total:
        return {"dominant_country": None, "dominant_city": None, "plan": []}

    # Dominance is computed over the WHOLE places table — this assumes one collection
    # per DB (as the leaderboard/places views also treat places globally). If multiple
    # distinct trips ever share a DB, scope these counts per-collection instead.
    country_counts = Counter(_norm(r.country) for r in rows if _norm(r.country))
    city_counts = Counter(_norm(r.city) for r in rows if _norm(r.city))

    dominant_country = None
    if country_counts:
        name, cnt = country_counts.most_common(1)[0]
        if cnt / total >= country_threshold:
            # Canonicalize so home_country matching (which canonicalizes each
            # location_name) aligns even when the stored country string isn't
            # already canonical (e.g. "USA" → "united states").
            dominant_country = _canon_country(name) or name

    dominant_city = None
    if city_counts:
        name, cnt = city_counts.most_common(1)[0]
        if cnt / total >= city_threshold:
            dominant_city = name

    # Every city / neighborhood label this collection uses as a location FIELD.
    # An item whose own name is one of these is describing the setting, not a venue.
    # Derived from the data, so it needs no hardcoded gazetteer and adapts to any
    # destination — a Tokyo collection yields Tokyo's wards the same way.
    geo_labels = {_geo_key(r.city) for r in rows if _norm(r.city)}

    # Neighborhood labels are noisier than city labels, so they carry two guards.
    nb_cities: dict[str, set[str]] = {}
    for r in rows:
        if _norm(r.neighborhood) and _norm(r.city):
            nb_cities.setdefault(_geo_key(r.neighborhood), set()).add(_norm(r.city))
    nb_uses = Counter(_geo_key(r.neighborhood) for r in rows if _norm(r.neighborhood))
    geo_labels |= {
        nb for nb, c in nb_uses.items()
        if c >= _MIN_LABEL_USES
        # A label found in more than one city is a generic descriptor, not this
        # collection's setting — "Chinatown" (Incheon and New York) and "Jung-gu"
        # (Incheon and Seoul) name a kind of district that many cities have. The
        # specific instance is a destination you go to, so it keeps its pin; the
        # city shown alongside it is what disambiguates which one it is.
        and len(nb_cities.get(nb, ())) <= 1
    }
    geo_labels.discard("")

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
        elif (_norm(r.subcategory) in _AREA_SUBCATEGORIES
                and _geo_key(r.location_name) in geo_labels):
            reason = "geography"
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
    counts = {"home_country": 0, "home_city": 0, "media": 0, "geography": 0}
    flag_ids = set()
    for pid, reason in res["plan"]:
        if reason is not None:
            counts[reason] += 1
            flag_ids.add(pid)

    changed = 0
    if apply:
        # single scan + in-memory mutate, rather than one session.get() per place
        for place in session.query(Place).all():
            should = place.id in flag_ids
            if bool(place.is_context) != should:
                place.is_context = should
                changed += 1
        session.commit()
    return {
        "dominant_country": res["dominant_country"],
        "dominant_city": res["dominant_city"],
        "flagged": counts,
        "changed": changed,
    }
