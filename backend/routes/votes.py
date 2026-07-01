from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Place, Vote
from routes.jobs import _place_to_response
from schemas import PlaceResponse, VoteRequest

router = APIRouter()


@router.post("/api/places/{place_id}/vote", response_model=PlaceResponse)
def vote_on_place(place_id: str, body: VoteRequest, db: Session = Depends(get_db)):
    place = db.get(Place, place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")

    existing = db.query(Vote).filter(Vote.place_id == place_id, Vote.voter == "default").first()

    if body.vote is None:
        # Undo vote
        if existing:
            db.delete(existing)
            db.commit()
    else:
        value = 1 if body.vote == "up" else -1
        if existing:
            existing.value = value
            existing.updated_at = datetime.utcnow()
        else:
            db.add(Vote(
                id=str(uuid4()),
                place_id=place_id,
                voter="default",
                value=value,
            ))
        db.commit()

    return _place_to_response(place, db)
