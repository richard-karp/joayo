import math
from uuid import uuid4

from rapidfuzz import fuzz as _fuzz
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from models import Place
from schemas import ExtractedPlace
from services.raw_post import RawPost
from services.text_utils import normalize_name

_MATCH_RADIUS_M = 150
_FUZZY_COORD_RADIUS_M = 500    # looser radius when name fuzzy-matches but coords differ slightly
_NEAR_COORD_M = 25             # "same door" — relax the name gate to a category match at this range
_FUZZY_TOKEN_SET_THRESHOLD = 85
_FUZZY_RATIO_THRESHOLD = 70    # prevents "Cafe" from matching "Cafe Bora"
_COORD_BBOX_DEG = 0.005        # ~550m bounding-box pre-filter
_COORD_NAME_PLAUSIBILITY = 85  # coord-proximity match requires same TSR bar as fuzzy step


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _naive_utc(dt) -> "datetime | None":
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _build_author_entry(raw_post: RawPost) -> dict:
    entry: dict = {
        "username": raw_post.author,
        "platform_id": raw_post.author_platform_id or "",
        "platform": raw_post.platform,
    }
    if raw_post.author_profile_url:
        entry["profile_url"] = raw_post.author_profile_url
    return entry


def _city_conflict(a: str | None, b: str | None) -> bool:
    """Chain guard: same name in different cities are distinct venues."""
    if a and b and a.strip().lower() != b.strip().lower():
        return True
    return False


def _match_score(
    extracted: ExtractedPlace,
    norm: str,
    lat: float | None,
    lng: float | None,
    place: Place,
) -> tuple[int, float] | None:
    """Score how well `place` matches the incoming is_place item.

    Returns (tier, value) — higher tier wins, higher value breaks ties — or None.
      tier 3: coordinate proximity (<=150m) with a plausible name or same-category same-door
      tier 2: exact normalized name (no coords)
      tier 1: fuzzy name (within coord radius, or city-guarded when no coords)
    """
    pnorm = normalize_name(place.location_name)
    if not norm or not pnorm:
        return None

    both_coords = (lat is not None and lng is not None
                   and place.lat is not None and place.lng is not None)
    dist = _haversine_m(lat, lng, place.lat, place.lng) if both_coords else None

    exact = norm == pnorm
    tsr = _fuzz.token_set_ratio(norm, pnorm)
    rat = _fuzz.ratio(norm, pnorm)
    cat_match = bool(extracted.category and place.category
                     and extracted.category == place.category)

    # Tier 3 — coordinate proximity
    if both_coords and dist <= _MATCH_RADIUS_M:
        name_ok = exact or (tsr >= _COORD_NAME_PLAUSIBILITY and rat >= _FUZZY_RATIO_THRESHOLD)
        near_same_category = dist <= _NEAR_COORD_M and cat_match
        if name_ok or near_same_category:
            return (3, -dist)

    # Tier 2 — exact normalized name, records without full coords
    if exact and not both_coords:
        if _city_conflict(extracted.city, place.city):
            return None
        return (2, 0.0)

    # Tier 1 — fuzzy name
    if tsr >= _FUZZY_TOKEN_SET_THRESHOLD and rat >= _FUZZY_RATIO_THRESHOLD:
        if both_coords:
            if dist <= _FUZZY_COORD_RADIUS_M:
                return (1, -dist)
            return None
        if _city_conflict(extracted.city, place.city):
            return None
        return (1, 0.0)

    return None


def _find_match(
    extracted: ExtractedPlace,
    lat: float | None,
    lng: float | None,
    session: Session,
    geocoder: str | None = None,
    geocoder_place_id: str | None = None,
) -> Place | None:
    name = extracted.location_name.strip()
    norm = normalize_name(name)
    country = extracted.country
    city = extracted.city

    # ── Non-place items (dishes/products): must match name AND venue ──────────
    if not extracted.is_place:
        venue_norm = normalize_name(extracted.venue) if extracted.venue else ""
        for place in session.query(Place).filter(Place.is_place == False).all():  # noqa: E712
            if normalize_name(place.location_name) != norm:
                continue
            if _city_conflict(city, place.city):
                continue
            place_venue_norm = normalize_name(place.venue) if place.venue else ""
            if venue_norm != place_venue_norm:
                continue  # same dish name at a different venue → distinct
            return place
        return None

    # ── Step A: provider-id-first (is_place only) ─────────────────────────────
    if geocoder and geocoder_place_id:
        match = session.query(Place).filter(
            Place.is_place == True,  # noqa: E712
            Place.geocoder == geocoder,
            Place.geocoder_place_id == geocoder_place_id,
        ).first()
        if match:
            return match

    # ── Steps B/C: build a candidate pool, then pick the best match ───────────
    pool: dict[str, Place] = {}

    if lat is not None and lng is not None:
        bbox = session.query(Place).filter(
            Place.is_place == True,  # noqa: E712
            Place.lat.isnot(None),
            Place.lng.isnot(None),
            Place.lat.between(lat - _COORD_BBOX_DEG, lat + _COORD_BBOX_DEG),
            Place.lng.between(lng - _COORD_BBOX_DEG, lng + _COORD_BBOX_DEG),
        ).all()
        for place in bbox:
            pool[place.id] = place

    name_q = session.query(Place).filter(Place.is_place == True)  # noqa: E712
    if country:
        name_q = name_q.filter(or_(Place.country == country, Place.country.is_(None)))
    if city:
        name_q = name_q.filter(or_(Place.city == city, Place.city.is_(None)))
    for place in name_q.all():
        pool[place.id] = place

    best: Place | None = None
    best_key: tuple[int, float] | None = None
    for place in pool.values():
        score = _match_score(extracted, norm, lat, lng, place)
        if score is None:
            continue
        if best_key is None or score > best_key:
            best_key = score
            best = place
    return best


def find_or_merge_place(
    extracted: ExtractedPlace,
    raw_post: RawPost,
    lat: float | None,
    lng: float | None,
    job_id: str,
    session: Session,
    transcript: str | None = None,
    transcript_missing: bool = False,
    geocoder: str | None = None,
    geocoder_place_id: str | None = None,
) -> tuple[str, bool]:
    author_entry = _build_author_entry(raw_post)
    existing = _find_match(extracted, lat, lng, session,
                           geocoder=geocoder, geocoder_place_id=geocoder_place_id)

    if existing:
        # Add source URL if not already present
        urls = list(existing.source_urls or [])
        if raw_post.url not in urls:
            urls.append(raw_post.url)
            existing.source_urls = urls

        # Add author by platform_id (stable key); update username if handle changed
        authors = list(existing.all_authors or [])
        pid = author_entry["platform_id"]
        existing_pids = {a.get("platform_id") for a in authors if a.get("platform_id")}
        if pid and pid in existing_pids:
            # Update username and profile_url in case handle or URL changed
            existing.all_authors = [
                {**a, "username": raw_post.author,
                 **({"profile_url": raw_post.author_profile_url} if raw_post.author_profile_url else {})}
                if a.get("platform_id") == pid else a
                for a in authors
            ]
        elif raw_post.author not in {a.get("username") for a in authors}:
            authors.append(author_entry)
            existing.all_authors = authors

        # Update primary_author if this post is earlier (compare as naive UTC)
        post_dt = _naive_utc(raw_post.date_posted)
        existing_dt = _naive_utc(existing.earliest_date_posted)
        if post_dt and (existing_dt is None or post_dt < existing_dt):
            existing.primary_author = raw_post.author
            existing.primary_author_id = raw_post.author_platform_id
            existing.primary_author_profile_url = raw_post.author_profile_url
            existing.earliest_date_posted = post_dt
        # Also fill in missing profile URL for existing primary author match by platform_id
        elif (
            raw_post.author_profile_url
            and not existing.primary_author_profile_url
            and raw_post.author_platform_id == existing.primary_author_id
        ):
            existing.primary_author_profile_url = raw_post.author_profile_url

        # Fill in missing geocoords, city, provider ids, and derived fields
        if existing.lat is None and lat is not None:
            existing.lat = lat
        if existing.lng is None and lng is not None:
            existing.lng = lng
        if existing.city is None and extracted.city is not None:
            existing.city = extracted.city
        if existing.neighborhood is None and extracted.neighborhood is not None:
            existing.neighborhood = extracted.neighborhood
        if existing.geocoder is None and geocoder is not None:
            existing.geocoder = geocoder
        if existing.geocoder_place_id is None and geocoder_place_id is not None:
            existing.geocoder_place_id = geocoder_place_id
        if not existing.normalized_name:
            existing.normalized_name = normalize_name(existing.location_name)

        session.commit()
        return existing.id, False

    # No match — create new Place
    place = Place(
        id=str(uuid4()),
        created_by_job_id=job_id,
        source_urls=[raw_post.url],
        platform=raw_post.platform,
        primary_author=raw_post.author,
        primary_author_id=raw_post.author_platform_id,
        primary_author_profile_url=raw_post.author_profile_url,
        all_authors=[author_entry],
        earliest_date_posted=_naive_utc(raw_post.date_posted),
        location_name=extracted.location_name,
        normalized_name=normalize_name(extracted.location_name),
        category=extracted.category,
        subcategory=extracted.subcategory,
        is_place=extracted.is_place,
        venue=extracted.venue,
        country=extracted.country,
        city=extracted.city,
        neighborhood=extracted.neighborhood,
        summary=extracted.summary,
        labels=extracted.labels,
        insider_tips=extracted.insider_tips,
        lat=lat,
        lng=lng,
        geocoder=geocoder,
        geocoder_place_id=geocoder_place_id,
        raw_caption=raw_post.caption,
        tagged_accounts=raw_post.tagged_accounts,
        transcript=transcript,
        transcript_missing=transcript_missing,
    )
    session.add(place)
    session.commit()
    return place.id, True
