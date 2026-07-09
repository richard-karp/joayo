from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from database import get_db
from models import Place, Vote
from schemas import PlaceResponse, Author

router = APIRouter()


def _to_response(place: Place, score: int, current_vote_val: int | None) -> PlaceResponse:
    current_vote = None
    if current_vote_val == 1:
        current_vote = "up"
    elif current_vote_val == -1:
        current_vote = "down"

    return PlaceResponse(
        id=place.id,
        created_by_job_id=place.created_by_job_id,
        source_urls=place.source_urls or [],
        platform=place.platform,
        primary_author=place.primary_author,
        primary_author_id=place.primary_author_id,
        primary_author_profile_url=place.primary_author_profile_url,
        all_authors=[Author(**a) for a in (place.all_authors or [])],
        earliest_date_posted=place.earliest_date_posted,
        location_name=place.location_name,
        category=place.category,
        subcategory=place.subcategory,
        is_place=place.is_place if place.is_place is not None else True,
        is_context=place.is_context or False,
        venue=place.venue,
        country=place.country,
        city=place.city,
        summary=place.summary,
        labels=place.labels,
        insider_tips=place.insider_tips,
        lat=place.lat,
        lng=place.lng,
        raw_caption=place.raw_caption,
        tagged_accounts=place.tagged_accounts,
        transcript_missing=place.transcript_missing or False,
        created_at=place.created_at,
        vote_score=score,
        current_vote=current_vote,
    )


@router.get("/api/places", response_model=list[PlaceResponse])
def get_places(
    country: str | None = Query(None),
    city: str | None = Query(None),
    subcategory: str | None = Query(None),
    label: str | None = Query(None, description="Exact-match filter on a single label/tag"),
    q: str | None = Query(None, description="Free-text search over name, labels, summary, subcategory"),
    include_context: bool = Query(False, description="Include ambient home-base / media places (is_context=True)"),
    db: Session = Depends(get_db),
):
    query = db.query(Place)
    if not include_context:
        query = query.filter(Place.is_context.isnot(True))
    if country:
        query = query.filter(Place.country == country)
    if city:
        query = query.filter(Place.city == city)
    if subcategory:
        query = query.filter(Place.subcategory == subcategory)
    if q:
        like = f"%{q}%"
        # labels is a JSON array; cast to text so a substring match hits any element.
        query = query.filter(or_(
            Place.location_name.ilike(like),
            Place.summary.ilike(like),
            Place.subcategory.ilike(like),
            cast(Place.labels, String).ilike(like),
        ))
    places = query.order_by(Place.created_at.desc()).all()

    # Exact label membership is a JSON-array test — cheap to do in Python over the
    # already-filtered rows, and avoids brittle JSON-substring SQL.
    if label:
        places = [p for p in places if label in (p.labels or [])]

    # Compute vote scores in bulk
    scores = {
        pid: int(s or 0)
        for pid, s in db.query(Vote.place_id, func.sum(Vote.value))
        .group_by(Vote.place_id)
        .all()
    }
    current_votes = {
        pid: v
        for pid, v in db.query(Vote.place_id, Vote.value)
        .filter(Vote.voter == "default")
        .all()
    }

    return [
        _to_response(p, scores.get(p.id, 0), current_votes.get(p.id))
        for p in places
    ]


@router.get("/api/filters")
def get_filters(db: Session = Depends(get_db)):
    # Facet counts reflect actual venues only — exclude non-place items (dishes,
    # products, tips) and ambient-context rows. Deliberately NARROWER than the
    # /api/places list, which still returns is_place=False items alongside venues;
    # the counts describe how many real places exist per country/city.
    country_rows = (
        db.query(Place.country, func.count(Place.id))
        .filter(Place.country.isnot(None))
        .filter(Place.is_place.is_(True))
        .filter(Place.is_context.isnot(True))
        .group_by(Place.country)
        .order_by(func.count(Place.id).desc())
        .all()
    )
    city_rows = (
        db.query(Place.city, Place.country, func.count(Place.id))
        .filter(Place.city.isnot(None))
        .filter(Place.is_place.is_(True))
        .filter(Place.is_context.isnot(True))
        .group_by(Place.city, Place.country)
        .order_by(func.count(Place.id).desc())
        .all()
    )
    # Subcategory facet is a *filter* dimension, so its counts match what
    # /api/places?subcategory=… actually returns: non-context items including
    # is_place=False ones (e.g. eat/dish). Hence no is_place filter here.
    subcategory_rows = (
        db.query(Place.category, Place.subcategory, func.count(Place.id))
        .filter(Place.subcategory.isnot(None))
        .filter(Place.is_context.isnot(True))
        .group_by(Place.category, Place.subcategory)
        .order_by(func.count(Place.id).desc())
        .all()
    )
    return {
        "countries": [{"name": c, "place_count": n} for c, n in country_rows],
        "cities": [{"name": c, "country": co, "place_count": n} for c, co, n in city_rows],
        "subcategories": [
            {"name": s, "category": c, "place_count": n} for c, s, n in subcategory_rows
        ],
    }
