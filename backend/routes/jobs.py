from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Job, Place, PlaceMark
from schemas import JobResponse, PlaceResponse

router = APIRouter()


def _place_to_response(place: Place, session: Session) -> PlaceResponse:
    mark = (
        session.query(PlaceMark)
        .filter(PlaceMark.place_id == place.id, PlaceMark.user == "default")
        .first()
    )
    return PlaceResponse(
        **{c.name: getattr(place, c.name) for c in place.__table__.columns},
        my_rating=mark.rating if mark else None,
        want_to_go=bool(mark.want_to_go) if mark else False,
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
