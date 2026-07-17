"""Remediation for the low-confidence ("best guess") pins the native-name backfill
flagged needs_review. A reviewer confirms a correct pin, supplies a corrected Korean
name to re-geocode a wrong one, or rejects it (dropping the pin but keeping the row).

SECURITY: intentionally ungated, mirroring the per-place marks endpoints — this is
a single-user personal app behind an unadvertised URL. Unlike marks (which touch only
per-user rating/wishlist state), `reject`/`regeocode` mutate SHARED place coordinates,
so on a public deployment any anonymous caller could wipe or move pins. Before this app
takes real multi-user traffic, gate this router with `Depends(_require_admin)` (as the
admin curation routes do) and have the frontend send `X-Admin-Token`; that also requires
provisioning `ADMIN_TOKEN` locally and as a Fly secret (it is currently unset).
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Place
from routes.jobs import _place_to_response
from schemas import PlaceResponse, ReviewRequest
from services import geocoder

router = APIRouter()


@router.post("/api/places/{place_id}/review", response_model=PlaceResponse)
def review_place(place_id: str, body: ReviewRequest, db: Session = Depends(get_db)):
    place = db.get(Place, place_id)
    if not place:
        raise HTTPException(status_code=404, detail="Place not found")

    if body.action == "confirm":
        # The pin is correct — clear the flag, leave coordinates untouched.
        place.needs_review = False

    elif body.action == "reject":
        # The pin is wrong — drop the coordinates so it leaves the map. The row
        # survives (still browsable, unmapped); a future backfill/regeocode can retry.
        place.lat = None
        place.lng = None
        place.geocoder = None
        place.geocoder_place_id = None
        place.needs_review = False

    elif body.action == "regeocode":
        native = (body.native_name or "").strip()
        if not native:
            raise HTTPException(status_code=422,
                                detail="regeocode requires a native_name (Korean 한글 name).")
        # Re-run through the same guarded geocoder the backfill uses; a wrong-region
        # hit is rejected internally and surfaces here as "no match". Fall back to
        # "South Korea" (as the backfill hardcoded) so a row with a null country still
        # takes the Kakao native-name path rather than a spurious "no match".
        geo = geocoder.geocode_full(
            place.location_name, country=place.country or "South Korea",
            expected_city=place.city, native_name=native,
        )
        if geo.lat is None:
            raise HTTPException(
                status_code=422,
                detail=f"No Kakao match for '{native}' in {place.city or 'the stored region'}.",
            )
        _, needs_review = geocoder.review_confidence(native, geo.canonical_name)
        place.lat = geo.lat
        place.lng = geo.lng
        place.geocoder = geo.provider
        place.geocoder_place_id = geo.place_id
        place.native_name = native
        place.needs_review = needs_review
        if place.city is None and geo.city:
            place.city = geocoder.canonicalize_city(geo.city)

    db.commit()
    return _place_to_response(place, db)
