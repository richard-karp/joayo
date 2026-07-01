from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Job, Place, Vote
from schemas import JobResponse, PlaceResponse

router = APIRouter()


def _place_to_response(place: Place, session: Session) -> PlaceResponse:
    vote_score = session.query(func.sum(Vote.value)).filter(Vote.place_id == place.id).scalar() or 0
    vote_row = session.query(Vote).filter(Vote.place_id == place.id, Vote.voter == "default").first()
    current_vote = None
    if vote_row:
        current_vote = "up" if vote_row.value == 1 else "down"

    return PlaceResponse(
        **{c.name: getattr(place, c.name) for c in place.__table__.columns},
        vote_score=vote_score,
        current_vote=current_vote,
    )


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("pending", "processing"):
        raise HTTPException(status_code=400, detail=f"Job is not running (status: {job.status})")
    job.status = "cancelled"
    job.current_url = None
    db.commit()
    return {"status": "cancelled"}


@router.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    place_ids = job.updated_place_ids or []
    places = []
    if place_ids:
        db_places = db.query(Place).filter(Place.id.in_(place_ids)).all()
        places = [_place_to_response(p, db) for p in db_places]

    return JobResponse(
        id=job.id,
        status=job.status,
        total_urls=job.total_urls,
        processed=job.processed,
        current_url=job.current_url,
        pending_review=job.pending_review or [],
        failed_urls=job.failed_urls or [],
        warnings=job.warnings or [],
        paused_reason=job.paused_reason,
        remaining_posts=job.remaining_posts or [],
        places=places,
        created_at=job.created_at,
    )
