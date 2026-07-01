import math
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Place
from schemas import ExtractedPlace
from services.raw_post import RawPost

_MATCH_RADIUS_M = 150


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


def _find_match(location_name: str, lat: float | None, lng: float | None, session: Session) -> Place | None:
    name = location_name.strip()
    candidates = session.query(Place).filter(
        func.trim(func.lower(Place.location_name)) == name.lower()
    ).all()

    for place in candidates:
        # If either record has no coords, name match alone is sufficient
        if lat is None or lng is None or place.lat is None or place.lng is None:
            return place
        if _haversine_m(lat, lng, place.lat, place.lng) <= _MATCH_RADIUS_M:
            return place

    return None


def find_or_merge_place(
    extracted: ExtractedPlace,
    raw_post: RawPost,
    lat: float | None,
    lng: float | None,
    job_id: str,
    session: Session,
    transcript: str | None = None,
    transcript_missing: bool = False,
) -> tuple[str, bool]:
    author_entry = _build_author_entry(raw_post)
    existing = _find_match(extracted.location_name, lat, lng, session)

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
        category=extracted.category,
        subcategory=extracted.subcategory,
        is_place=extracted.is_place,
        venue=extracted.venue,
        country=extracted.country,
        city=extracted.city,
        summary=extracted.summary,
        labels=extracted.labels,
        insider_tips=extracted.insider_tips,
        lat=lat,
        lng=lng,
        raw_caption=raw_post.caption,
        tagged_accounts=raw_post.tagged_accounts,
        transcript=transcript,
        transcript_missing=transcript_missing,
    )
    session.add(place)
    session.commit()
    return place.id, True
