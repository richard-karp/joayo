from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, text
from sqlalchemy.orm import Session

from database import get_db
from models import Place, PlaceMark
from schemas import PlaceResponse, Author

router = APIRouter()


def _to_response(place: Place, mark: PlaceMark | None) -> PlaceResponse:
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
        neighborhood=place.neighborhood,
        summary=place.summary,
        labels=place.labels,
        insider_tips=place.insider_tips,
        lat=place.lat,
        lng=place.lng,
        geocoder_place_id=place.geocoder_place_id,
        raw_caption=place.raw_caption,
        tagged_accounts=place.tagged_accounts,
        transcript_missing=place.transcript_missing or False,
        created_at=place.created_at,
        needs_review=place.needs_review or False,
        my_rating=mark.rating if mark else None,
        want_to_go=bool(mark.want_to_go) if mark else False,
    )


@router.get("/api/places", response_model=list[PlaceResponse])
def get_places(
    country: str | None = Query(None),
    city: str | None = Query(None),
    neighborhood: str | None = Query(None),
    subcategory: str | None = Query(None),
    label: str | None = Query(None, description="Exact-match filter on a single label/tag"),
    q: str | None = Query(None, description="Free-text search over name, labels, summary, subcategory"),
    include_context: bool = Query(False, description="Include ambient home-base / media places (is_context=True)"),
    rated: bool = Query(False, description="Only places the user has rated (visited)"),
    want_to_go: bool = Query(False, description="Only places on the user's 'want to go' wishlist"),
    sort: str | None = Query(None, description="'new' = most recent post first; default = recently added"),
    db: Session = Depends(get_db),
):
    query = db.query(Place)
    if not include_context:
        query = query.filter(Place.is_context.isnot(True))
    if neighborhood:
        query = query.filter(Place.neighborhood == neighborhood)
    if rated:
        query = query.filter(Place.id.in_(
            db.query(PlaceMark.place_id).filter(
                PlaceMark.user == "default", PlaceMark.rating.isnot(None)
            )
        ))
    if want_to_go:
        query = query.filter(Place.id.in_(
            db.query(PlaceMark.place_id).filter(
                PlaceMark.user == "default", PlaceMark.want_to_go.is_(True)
            )
        ))
    if country:
        query = query.filter(Place.country == country)
    if city:
        query = query.filter(Place.city == city)
    if subcategory:
        query = query.filter(Place.subcategory == subcategory)
    if label:
        # Exact membership in the JSON labels array, evaluated in SQL (SQLite json_each)
        # so the DB filters rather than materializing every row first.
        query = query.filter(
            text("EXISTS (SELECT 1 FROM json_each(places.labels) WHERE value = :label)")
            .bindparams(label=label)
        )
    if q:
        # Escape LIKE metacharacters so a literal % or _ in the query is matched
        # verbatim rather than acting as a wildcard. (Escape the backslash first.)
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        # labels is a JSON array; cast to text so a substring match hits any element.
        query = query.filter(or_(
            Place.location_name.ilike(like, escape="\\"),
            Place.summary.ilike(like, escape="\\"),
            Place.subcategory.ilike(like, escape="\\"),
            cast(Place.labels, String).ilike(like, escape="\\"),
        ))
    if sort == "new":
        # Most recent post first; rows with no recovered date sort last.
        query = query.order_by(
            Place.earliest_date_posted.is_(None),
            Place.earliest_date_posted.desc(),
        )
    else:
        query = query.order_by(Place.created_at.desc())
    places = query.all()

    # Fetch the caller's marks (rating + wishlist) in bulk, keyed by place_id.
    marks = {
        m.place_id: m
        for m in db.query(PlaceMark).filter(PlaceMark.user == "default").all()
    }

    return [_to_response(p, marks.get(p.id)) for p in places]


@router.get("/api/places/{place_id}", response_model=PlaceResponse)
def get_place(place_id: str, db: Session = Depends(get_db)):
    place = db.get(Place, place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    mark = (
        db.query(PlaceMark)
        .filter(PlaceMark.place_id == place_id, PlaceMark.user == "default")
        .first()
    )
    return _to_response(place, mark)


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
    # Neighborhood facet, grouped under its city so the UI can nest chips beneath the
    # selected city. Venues only (same narrowing as the country/city facets).
    neighborhood_rows = (
        db.query(Place.neighborhood, Place.city, func.count(Place.id))
        .filter(Place.neighborhood.isnot(None))
        .filter(Place.is_place.is_(True))
        .filter(Place.is_context.isnot(True))
        .group_by(Place.neighborhood, Place.city)
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
        "neighborhoods": [
            {"name": nb, "city": ci, "place_count": n} for nb, ci, n in neighborhood_rows
        ],
        "subcategories": [
            {"name": s, "category": c, "place_count": n} for c, s, n in subcategory_rows
        ],
    }
