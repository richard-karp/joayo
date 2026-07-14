#!/usr/bin/env python3
"""Reconcile place `city` labels against their coordinates on a joayo DB.

Runs two idempotent, re-runnable steps:

1. Patch two known-bad source rows that the reconcile-cities pass can't fix on
   its own:
     - "Baegyangsa Temple" — a wrong-POI geocode (a same-named temple in Ulsan);
       corrected to the real Jangseong temple coordinates.
     - "Monghwan" — labeled city "Ansan" but its coordinates are in Gwangju. It's
       a single-member cluster, so the pass never flags it; relabel to Gwangju.
   Each patch only touches a row still in the known-bad state, so re-running is a
   no-op once fixed, and a DB that never had these rows is left untouched.

2. Run the reconcile-cities pass repeatedly until it converges (zero changes).
   Reconciliation is convergent: moving one outlier de-pollutes a city's median
   centroid and can reveal the next one, so a single pass isn't enough.

Defaults to a DRY RUN (no writes) — it still previews full convergence by applying
proposed changes in-memory and rolling back. Pass --apply to commit.

Operates on whatever DB the app is configured for (DB_PATH), so it works the same
locally and in production:

    # local preview / apply
    python backend/reconcile_place_cities.py
    python backend/reconcile_place_cities.py --apply

    # production (Fly): the script is baked into the image at /app
    fly ssh console -a joayo-api
    python reconcile_place_cities.py            # preview
    python reconcile_place_cities.py --apply    # commit

Needs KAKAO_REST_API_KEY in the environment (already a Fly secret in prod, loaded
from .env locally). Without it, coordinates can't be reverse-geocoded and every
outlier falls into needs_review — the script warns when it detects that.
"""
import argparse
import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(raise_error_if_not_found=False))
# Resolve the DB relative to this file (not the CWD) unless DB_PATH is already set,
# so the script hits backend/places.db no matter where it's launched from, while
# production's absolute DB_PATH (the Fly volume) still takes precedence.
os.environ.setdefault("DB_PATH", os.path.join(os.path.dirname(__file__), "places.db"))

from database import SessionLocal  # noqa: E402  (import after DB_PATH is resolved)
from models import Place  # noqa: E402
from routes.admin import reconcile_cities  # noqa: E402
from services.geocoder import city_from_coords  # noqa: E402

MAX_PASSES = 10

# Known-correct POI for the real Baegyangsa (백양사), Jangseong, South Jeolla.
_BAEGYANGSA_FIX = {"lat": 35.43938477713701, "lng": 126.88339439583986, "place_id": "8178021"}
# The stored bad coord points at a same-named temple in Ulsan (~129.3°E); the real
# one is ~126.9°E. Only patch a row still sitting east of this line.
_BAEGYANGSA_WRONG_MIN_LNG = 128.0


def patch_known_rows(db) -> list[str]:
    """Mutate the two known-bad rows in-memory (the caller decides whether to commit).
    Returns a human-readable description of each patch actually made."""
    patched: list[str] = []

    for p in db.query(Place).filter(Place.location_name == "Baegyangsa Temple").all():
        if p.lng is not None and p.lng > _BAEGYANGSA_WRONG_MIN_LNG:
            patched.append(
                f"Baegyangsa Temple: coord ({p.lat:.4f},{p.lng:.4f}) -> "
                f"({_BAEGYANGSA_FIX['lat']:.4f},{_BAEGYANGSA_FIX['lng']:.4f})"
            )
            p.lat = _BAEGYANGSA_FIX["lat"]
            p.lng = _BAEGYANGSA_FIX["lng"]
            p.geocoder = "kakao"
            p.geocoder_place_id = _BAEGYANGSA_FIX["place_id"]

    for p in db.query(Place).filter(
        Place.location_name == "Monghwan", Place.city == "Ansan"
    ).all():
        # Coords win — only relabel if the coordinate actually reverse-geocodes to
        # Gwangju, so a genuinely-Ansan "Monghwan" (correct Gyeonggi coords) is left
        # alone. (If the Kakao key is missing, city_from_coords returns None and the
        # row is conservatively skipped.)
        if p.lat is None or p.lng is None or city_from_coords(p.lat, p.lng) != "Gwangju":
            continue
        patched.append(
            f"Monghwan: city 'Ansan' -> 'Gwangju' "
            f"(coord ({p.lat:.4f},{p.lng:.4f}) reverse-geocodes to Gwangju)"
        )
        p.city = "Gwangju"

    return patched


def run(apply: bool) -> None:
    db = SessionLocal()
    try:
        mode = "APPLY" if apply else "DRY RUN"
        print(f"=== reconcile_place_cities.py [{mode}] ===\n")

        # Step 1 — known-bad rows.
        print("Step 1: patch known bad rows")
        patches = patch_known_rows(db)
        for line in patches:
            print(f"  - {line}")
        if not patches:
            print("  (nothing to patch — already clean)")
        if apply and patches:
            db.commit()
        print()

        # Step 2 — reconcile to convergence.
        print("Step 2: reconcile-cities" + ("" if apply else " (previewing convergence)"))
        total = 0
        for i in range(1, MAX_PASSES + 1):
            res = reconcile_cities(request=None, dry_run=not apply, db=db, _=None)
            n, nr = len(res["changes"]), len(res["needs_review"])
            print(
                f"  pass {i}: {n} change(s), {nr} needs_review "
                f"(checked {res['checked']}, mismatched {res['mismatched']})"
            )
            for ch in res["changes"]:
                print(f"      {ch['name'][:36]:38} {ch['old_city']!r} -> {ch['new_city']!r}")
            for r in res["needs_review"]:
                print(f"      needs_review: {r['name'][:34]!r:36} {r.get('reason')}")

            if i == 1 and n == 0 and nr > 0 and all(
                r.get("reason") == "no region from coords" for r in res["needs_review"]
            ):
                print("  ! every outlier failed reverse-geocoding — is KAKAO_REST_API_KEY set?")

            total += n
            if n == 0:
                if i == 1:
                    print("  already converged — nothing to relabel.")
                else:
                    print(f"  converged after {i} pass(es).")
                break

            if not apply:
                # Apply proposed relabels in-memory so the next pass can converge;
                # the whole session is rolled back below, so nothing is written.
                for ch in res["changes"]:
                    obj = db.get(Place, ch["id"])
                    if obj is not None:
                        obj.city = ch["new_city"]
        else:
            print(f"  ! still changing after {MAX_PASSES} passes — stopping (investigate).")

        print()
        if apply:
            print(f"Done. Committed {total} relabel(s) plus {len(patches)} row patch(es).")
        else:
            db.rollback()  # discard the in-memory preview mutations
            print(f"Done (dry run — no writes). Would relabel {total} row(s) "
                  f"and patch {len(patches)} row(s). Re-run with --apply to commit.")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Patch known bad rows and reconcile place city labels to convergence."
    )
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry run).")
    run(ap.parse_args().apply)
