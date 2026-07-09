from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
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
    include_context: bool = Query(False, description="Include ambient home-base / media places (is_context=True)"),
    db: Session = Depends(get_db),
):
    q = db.query(Place)
    if not include_context:
        q = q.filter(Place.is_context.isnot(True))
    if country:
        q = q.filter(Place.country == country)
    if city:
        q = q.filter(Place.city == city)
    places = q.order_by(Place.created_at.desc()).all()

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
    # Facet counts reflect actual places only — exclude non-place items
    # (dishes, products, tips) and ambient-context rows, matching the default
    # /api/places list. Without this, counts include is_place=False rows.
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
    return {
        "countries": [{"name": c, "place_count": n} for c, n in country_rows],
        "cities": [{"name": c, "country": co, "place_count": n} for c, co, n in city_rows],
    }
