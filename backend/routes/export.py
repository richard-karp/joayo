import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Job, Place

router = APIRouter()


@router.get("/api/export")
def export_all_csv(country: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Place).filter(Place.is_place == True)
    if country:
        q = q.filter(Place.country == country)
    places = q.order_by(Place.created_at.desc()).all()

    rows = []
    for p in places:
        rows.append({
            "location_name": p.location_name,
            "category": p.category,
            "subcategory": p.subcategory,
            "country": p.country,
            "city": p.city,
            "summary": p.summary,
            "insider_tips": p.insider_tips,
            "labels": json.dumps(p.labels or []),
            "lat": p.lat,
            "lng": p.lng,
            "source_urls": json.dumps(p.source_urls or []),
            "platform": p.platform,
            "primary_author": p.primary_author,
            "all_authors": json.dumps(p.all_authors or []),
            "tagged_accounts": json.dumps(p.tagged_accounts or []),
            "transcript_missing": p.transcript_missing,
        })

    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)

    filename = f"places-{country.lower().replace(' ', '-')}.csv" if country else "places-all.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/api/export/{job_id}")
def export_csv(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    place_ids = job.updated_place_ids or []
    if not place_ids:
        raise HTTPException(status_code=404, detail="No places found for this job")

    places = db.query(Place).filter(Place.id.in_(place_ids)).all()

    rows = []
    for p in places:
        rows.append({
            "location_name": p.location_name,
            "category": p.category,
            "subcategory": p.subcategory,
            "summary": p.summary,
            "insider_tips": p.insider_tips,
            "labels": json.dumps(p.labels or []),
            "lat": p.lat,
            "lng": p.lng,
            "source_urls": json.dumps(p.source_urls or []),
            "platform": p.platform,
            "primary_author": p.primary_author,
            "all_authors": json.dumps(p.all_authors or []),
            "tagged_accounts": json.dumps(p.tagged_accounts or []),
            "transcript_missing": p.transcript_missing,
        })

    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)

    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=places-{job_id}.csv"},
    )
