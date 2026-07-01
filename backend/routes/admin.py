from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models import Place
from routes.extract import _transcript_matches_caption

router = APIRouter(prefix="/api/admin")


@router.post("/scrub-transcripts")
def scrub_transcripts(db: Session = Depends(get_db)):
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
