from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Place, PlaceMark
from routes.jobs import _place_to_response
from schemas import PlaceResponse, RatingRequest, WantToGoRequest

router = APIRouter()

# Single-user placeholder until multi-user auth is added (mirrors the old voter default).
_USER = "default"


def _get_mark(db: Session, place_id: str) -> PlaceMark | None:
    return (
        db.query(PlaceMark)
        .filter(PlaceMark.place_id == place_id, PlaceMark.user == _USER)
        .first()
    )


def _upsert(db: Session, mark: PlaceMark | None, place_id: str,
            rating: str | None, want_to_go: bool) -> None:
    """Write the combined (rating, want_to_go) state for one place.

    The row carries both signals; it is deleted only when both are empty so it
    never lingers as a no-op.
    """
    if rating is None and not want_to_go:
        if mark is not None:
            db.delete(mark)
        db.commit()
        return
    if mark is None:
        mark = PlaceMark(id=str(uuid4()), place_id=place_id, user=_USER)
        db.add(mark)
    mark.rating = rating
    mark.want_to_go = want_to_go
    mark.updated_at = datetime.now(timezone.utc)
    db.commit()


@router.post("/api/places/{place_id}/rating", response_model=PlaceResponse)
def rate_place(place_id: str, body: RatingRequest, db: Session = Depends(get_db)):
    place = db.get(Place, place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    mark = _get_mark(db, place_id)
    # Setting a rating marks the place visited, which clears "want to go";
    # clearing the rating (null) leaves any existing wishlist flag intact.
    want_to_go = False if body.rating is not None else (mark.want_to_go if mark else False)
    _upsert(db, mark, place_id, body.rating, want_to_go)
    return _place_to_response(place, db)


@router.post("/api/places/{place_id}/want-to-go", response_model=PlaceResponse)
def set_want_to_go(place_id: str, body: WantToGoRequest, db: Session = Depends(get_db)):
    place = db.get(Place, place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")
    mark = _get_mark(db, place_id)
    rating = mark.rating if mark else None
    _upsert(db, mark, place_id, rating, body.want_to_go)
    return _place_to_response(place, db)
