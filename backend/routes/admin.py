import math
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from rapidfuzz import fuzz as _fuzz
from sqlalchemy.orm import Session

from database import get_db
from models import Place, Vote
from services.text_utils import _transcript_matches_caption

router = APIRouter(prefix="/api/admin")

# --- Thresholds (kept in sync with deduplicator.py) ---
_MATCH_RADIUS_M = 150
_FUZZY_COORD_RADIUS_M = 500
_FUZZY_TOKEN_SET_THRESHOLD = 85
_FUZZY_RATIO_THRESHOLD = 70
_COORD_NAME_PLAUSIBILITY = 85  # coord-proximity match requires same TSR bar as fuzzy step

# --- Generic name detection ---

# Known Seoul/Korea area and neighbourhood names used as prefixes in bad extractions.
_AREA_NAMES = frozenset({
    "insadong", "hongdae", "myeongdong", "itaewon", "sinchon",
    "gangnam", "jongno", "mapo", "seongsu", "bukchon", "apgujeong",
    "hapjeong", "yeonnam", "mangwon", "euljiro", "cheongdam",
    "hannam", "ikseon", "samcheong", "sinsa", "dongdaemun",
    "namdaemun", "yeouido", "noryangjin", "daehakno",
})

_AREA_QUALIFIERS = frozenset({
    "neighborhood", "neighbourhood", "district", "area",
})

# Bare type words and known compound generics — names that are ONLY these are always junk.
_GENERIC_EXACT = frozenset({
    "korean bbq", "street food", "bbq", "restaurant", "cafe",
    "shop", "bar", "spa", "clinic", "pharmacy", "bakery",
    "coffee", "coffee shop", "food stall", "snack", "stall",
    "korean bbq restaurant", "bbq restaurant", "street food stall",
    "korean restaurant", "korean cafe",
})


def _is_generic_name(location_name: str) -> bool:
    lower = (location_name or "").lower().strip()

    # 1. Exact bare-type or known compound match
    if lower in _GENERIC_EXACT:
        return True

    # 2. "[area] [optional qualifier] [generic type]"
    #    e.g. "Insadong Korean BBQ", "Hongdae cafe", "Myeongdong neighborhood restaurant"
    for area in _AREA_NAMES:
        if lower.startswith(area):
            rest = lower[len(area):].strip().strip("()")
            for qual in _AREA_QUALIFIERS:
                if rest.startswith(qual):
                    rest = rest[len(qual):].strip()
                    break
            if rest and rest in _GENERIC_EXACT:
                return True

    # 3. "[generic type] (area)" — e.g. "Korean BBQ restaurant (Insadong)"
    if "(" in lower and lower.endswith(")"):
        before = lower[: lower.rfind("(")].strip()
        inside = lower[lower.rfind("(") + 1 : -1].strip()
        if inside in _AREA_NAMES and before in _GENERIC_EXACT:
            return True

    return False


# --- Duplicate detection ---
def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _places_match(
    a: Place,
    b: Place,
    *,
    a_coords: tuple[float | None, float | None] | None = None,
    b_coords: tuple[float | None, float | None] | None = None,
) -> bool:
    a_name = (a.location_name or "").strip().lower()
    b_name = (b.location_name or "").strip().lower()
    if not a_name or not b_name:
        return False

    a_lat, a_lng = a_coords if a_coords is not None else (a.lat, a.lng)
    b_lat, b_lng = b_coords if b_coords is not None else (b.lat, b.lng)
    both_coords = all(v is not None for v in [a_lat, a_lng, b_lat, b_lng])

    dist: float | None = None
    if both_coords:
        dist = _haversine_m(a_lat, a_lng, b_lat, b_lng)
        if (dist <= _MATCH_RADIUS_M
                and _fuzz.token_set_ratio(a_name, b_name) >= _COORD_NAME_PLAUSIBILITY
                and _fuzz.ratio(a_name, b_name) >= _FUZZY_RATIO_THRESHOLD):
            return True

    if a_name == b_name:
        return True

    tsr = _fuzz.token_set_ratio(a_name, b_name)
    ratio = _fuzz.ratio(a_name, b_name)
    if tsr >= _FUZZY_TOKEN_SET_THRESHOLD and ratio >= _FUZZY_RATIO_THRESHOLD:
        if both_coords:
            return dist <= _FUZZY_COORD_RADIUS_M  # type: ignore[operator]
        return True

    return False


def _absorb(target: Place, source: Place, db: Session) -> None:
    """Merge source's data into target, then delete source (including vote reassignment)."""
    # Merge source_urls
    urls = list(target.source_urls or [])
    for url in (source.source_urls or []):
        if url not in urls:
            urls.append(url)
    target.source_urls = urls

    # Merge authors by platform_id
    authors = list(target.all_authors or [])
    existing_pids = {a.get("platform_id") for a in authors if a.get("platform_id")}
    existing_users = {a.get("username") for a in authors}
    for author in (source.all_authors or []):
        pid = author.get("platform_id")
        if pid and pid in existing_pids:
            continue
        uname = author.get("username")
        if uname not in existing_users:
            authors.append(author)
            if pid:
                existing_pids.add(pid)
            if uname:
                existing_users.add(uname)
    target.all_authors = authors

    # Update primary author if source posted earlier
    src_dt = source.earliest_date_posted
    tgt_dt = target.earliest_date_posted
    if src_dt is not None and (tgt_dt is None or src_dt < tgt_dt):
        target.primary_author = source.primary_author
        target.primary_author_id = source.primary_author_id
        target.primary_author_profile_url = source.primary_author_profile_url
        target.earliest_date_posted = src_dt

    # Fill missing geocoords and city
    if target.lat is None and source.lat is not None:
        target.lat = source.lat
    if target.lng is None and source.lng is not None:
        target.lng = source.lng
    if target.city is None and source.city is not None:
        target.city = source.city

    # Fill missing transcript
    if target.transcript is None and source.transcript is not None:
        target.transcript = source.transcript
        target.transcript_missing = source.transcript_missing

    # Reassign votes; drop source votes that conflict with an existing target vote
    target_voters = {
        v.voter for v in db.query(Vote).filter(Vote.place_id == target.id).all()
    }
    if target_voters:
        db.query(Vote).filter(
            Vote.place_id == source.id,
            Vote.voter.in_(target_voters),
        ).delete(synchronize_session=False)
    db.query(Vote).filter(Vote.place_id == source.id).update(
        {"place_id": target.id}, synchronize_session=False
    )

    db.delete(source)


# --- Auth ---
def _require_admin(request: Request):
    token = os.getenv("ADMIN_TOKEN")
    if not token or request.headers.get("X-Admin-Token") != token:
        raise HTTPException(status_code=403, detail="Forbidden")


# --- Endpoints ---
@router.post("/scrub-transcripts")
def scrub_transcripts(request: Request, db: Session = Depends(get_db), _: None = Depends(_require_admin)):
    """Reset transcripts that don't match their caption (CDN collision artefacts).

    Sets transcript=None and transcript_missing=True on affected Place records
    so they become eligible for re-transcription when URLs are re-submitted.
    """
    candidates = db.query(Place).filter(
        Place.transcript.isnot(None),
        Place.transcript_missing == False,  # noqa: E712
    ).all()

    scrubbed = []
    for place in candidates:
        if not _transcript_matches_caption(place.transcript, place.raw_caption or ""):
            place.transcript = None
            place.transcript_missing = True
            scrubbed.append(place.id)

    if scrubbed:
        db.commit()

    return {
        "checked": len(candidates),
        "scrubbed": len(scrubbed),
        "scrubbed_ids": scrubbed,
    }


@router.post("/scrub-generic-names")
def scrub_generic_names(request: Request, db: Session = Depends(get_db), _: None = Depends(_require_admin)):
    """Delete Place records whose location_name is a generic category description.

    Targets names like "Insadong Korean BBQ restaurant", "Korean BBQ restaurant (Insadong)",
    "Hongdae cafe", etc. — area + category combinations that Claude should have skipped
    but extracted anyway. Votes for deleted places are also removed.
    """
    candidates = db.query(Place).all()
    deleted = []
    for place in candidates:
        if _is_generic_name(place.location_name or ""):
            db.query(Vote).filter(Vote.place_id == place.id).delete(synchronize_session=False)
            db.delete(place)
            deleted.append({"id": place.id, "name": place.location_name})

    if deleted:
        db.commit()

    return {
        "checked": len(candidates),
        "deleted": len(deleted),
        "deleted_records": deleted,
    }


@router.post("/merge-duplicates")
def merge_duplicates(request: Request, db: Session = Depends(get_db), _: None = Depends(_require_admin)):
    """Retroactive deduplication pass over all Place records.

    For each place, checks whether an older record with a matching name and/or
    coordinates already exists. If so, merges the newer record into the older one
    (combining source_urls, authors, votes) and deletes the newer duplicate.
    """
    places = db.query(Place).order_by(Place.created_at).all()
    # Snapshot coords before any _absorb calls mutate them, preventing coord-chaining
    coord_snapshot: dict[str, tuple[float | None, float | None]] = {
        p.id: (p.lat, p.lng) for p in places
    }
    deleted: set[str] = set()
    pairs: list[dict] = []

    for i, place in enumerate(places):
        if place.id in deleted:
            continue
        for older in places[:i]:
            if older.id in deleted:
                continue
            if _places_match(
                place, older,
                a_coords=coord_snapshot[place.id],
                b_coords=coord_snapshot[older.id],
            ):
                _absorb(older, place, db)
                deleted.add(place.id)
                pairs.append({
                    "kept_id": older.id,
                    "kept_name": older.location_name,
                    "merged_id": place.id,
                    "merged_name": place.location_name,
                })
                break

    if pairs:
        db.commit()

    return {
        "checked": len(places),
        "merged": len(pairs),
        "merged_pairs": pairs,
    }
