"""
Cleanup script — run after a job completes to fix:
  1. Places geocoded outside their expected country (re-geocode with country hint)
  2. Failed URLs — retry extraction using embedded captions from saved_collections.json
"""
import os
import sys
import json
import time
import argparse
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

from database import SessionLocal, engine
from models import Base, Place, Job
from services import geocoder, extractor, deduplicator
from services.fetchers import fetch_post
from services.url_parser import parse_posts_from_collection

Base.metadata.create_all(engine)

# South Korea bounding box
KR_LAT = (33.0, 38.7)
KR_LNG = (124.5, 132.0)


def _in_korea(lat, lng) -> bool:
    return KR_LAT[0] <= lat <= KR_LAT[1] and KR_LNG[0] <= lng <= KR_LNG[1]


def fix_geocoding(session, country: str = "South Korea"):
    """Re-geocode places whose coordinates fall outside the expected country.

    Only touches places *expected* to be in `country` — those whose stored country
    is unset or already matches it. Places explicitly tagged with a different
    country (genuinely-foreign venues, e.g. a Tokyo restaurant) are left untouched
    so their correct coordinates aren't clobbered back into Korea.
    """
    places = session.query(Place).filter(Place.lat.isnot(None), Place.is_place == True).all()
    wrong = [
        p for p in places
        if not _in_korea(p.lat, p.lng) and (not p.country or p.country == country)
    ]
    print(f"Found {len(wrong)} places with coordinates outside South Korea")

    fixed = 0
    nulled = 0
    for place in wrong:
        print(f"  Re-geocoding: {place.location_name} (was {place.lat:.2f}, {place.lng:.2f})")
        lat, lng = geocoder.geocode(place.location_name, country=country)
        if lat and lng and _in_korea(lat, lng):
            place.lat = lat
            place.lng = lng
            session.commit()
            print(f"    → fixed: {lat:.4f}, {lng:.4f}")
            fixed += 1
        else:
            place.lat = None
            place.lng = None
            session.commit()
            print(f"    → nulled (could not resolve to {country})")
            nulled += 1

    print(f"\nGeocode cleanup: {fixed} fixed, {nulled} nulled")


def retry_failures(session, job_id: str, collections_file: str | None = None, collection_name: str | None = None):
    """Retry failed URLs from a job using embedded captions when available."""
    job = session.get(Job, job_id)
    if not job:
        print(f"Job {job_id} not found")
        return

    failed = list(job.failed_urls or [])
    if not failed:
        print("No failed URLs to retry")
        return

    print(f"Retrying {len(failed)} failed URLs")

    # Build caption lookup from collections file if provided
    caption_map: dict[str, str] = {}
    if collections_file and collection_name:
        with open(collections_file, "rb") as f:
            data = f.read()
        posts = parse_posts_from_collection(data, collection_name)
        caption_map = {p["url"].rstrip("/"): p.get("caption") or "" for p in posts}
        print(f"Loaded {len(caption_map)} captions from {collection_name}")

    # Build set of already-processed URLs
    existing_urls: set[str] = set()
    for (urls,) in session.query(Place.source_urls).all():
        for u in (urls or []):
            existing_urls.add(u.rstrip("/"))

    still_failed = []
    retried = 0
    skipped = 0

    for entry in failed:
        url = entry["url"]
        normalized = url.rstrip("/")

        if normalized in existing_urls:
            print(f"  SKIP (already processed): {url}")
            skipped += 1
            continue

        caption = caption_map.get(normalized)
        print(f"  Retrying: {url}" + (" [caption]" if caption else " [no caption]"))

        try:
            raw_post = fetch_post(url, embedded_caption=caption)
        except Exception as e:
            print(f"    fetch failed: {e}")
            still_failed.append(entry)
            continue

        try:
            places = extractor.extract(raw_post, transcript=None)
        except Exception as e:
            print(f"    extraction failed: {e}")
            still_failed.append({"url": url, "error": str(e)})
            continue

        for extracted_place in places:
            try:
                lat, lng = (
                    geocoder.geocode(extracted_place.location_name, country=extracted_place.country)
                    if extracted_place.is_place else (None, None)
                )
                deduplicator.find_or_merge_place(
                    extracted_place, raw_post, lat, lng, job.id, session,
                    transcript=None, transcript_missing=False,
                )
                session.commit()
            except Exception as e:
                print(f"    save failed: {e}")

        existing_urls.add(normalized)
        retried += 1

    # Update job failure list
    job.failed_urls = still_failed
    if not still_failed and not (job.pending_review or []):
        job.status = "complete"
    session.commit()

    print(f"\nRetry results: {retried} succeeded, {skipped} skipped, {len(still_failed)} still failed")


def resolve_handles(session):
    """
    Re-fetch one source URL per unique author (by primary_author_id) to get
    their actual @handle and profile URL via yt-dlp.

    Instagram's yt-dlp response: uploader=display_name, channel=@handle.
    """
    import subprocess
    import json as _json
    import re as _re

    cookies_file = os.getenv("INSTAGRAM_COOKIES_FILE")

    def _fetch_info(url: str) -> dict:
        cmd = ["yt-dlp", "--dump-json", "--no-playlist", "-q", "--no-warnings"]
        if cookies_file and os.path.exists(cookies_file):
            cmd += ["--cookies", cookies_file]
        cmd.append(url)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            # Multi-image posts output NDJSON — take the first valid line
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    try:
                        return _json.loads(line)
                    except _json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return {}

    # Group places by primary_author_id (stable numeric ID, survives handle changes)
    places_by_pid: dict[str, list] = {}
    for place in session.query(Place).filter(
        Place.primary_author_id.isnot(None),
        Place.primary_author_id != "",
        Place.primary_author_profile_url.is_(None),
    ).all():
        places_by_pid.setdefault(place.primary_author_id, []).append(place)

    print(f"Resolving handles for {len(places_by_pid)} authors")

    resolved = 0
    failed = 0
    for pid, pid_places in places_by_pid.items():
        # Try source URLs until one succeeds
        handle = None
        profile_url = None
        tried_urls = []
        for place in pid_places:
            for url in (place.source_urls or []):
                if url in tried_urls:
                    continue
                tried_urls.append(url)
                info = _fetch_info(url)
                if not info:
                    continue
                # channel = @handle on Instagram
                ch = info.get("channel") or ""
                if ch and _re.match(r'^[\w.]+$', ch) and not ch.isdigit():
                    handle = ch
                    profile_url = f"https://www.instagram.com/{handle}/"
                    break
            if handle:
                break

        if not handle:
            print(f"  {pid} — no handle found (tried {len(tried_urls)} URL(s))")
            failed += 1
            continue

        for place in pid_places:
            place.primary_author = handle
            place.primary_author_profile_url = profile_url
            updated_authors = []
            for a in (place.all_authors or []):
                if a.get("platform_id") == pid:
                    a = {**a, "username": handle, "profile_url": profile_url}
                updated_authors.append(a)
            place.all_authors = updated_authors
        session.commit()
        print(f"  {pid} → @{handle} ({profile_url})")
        resolved += 1

    print(f"\nHandle resolution: {resolved} resolved, {failed} failed")


def regeocde_all(session, country: str = "South Korea"):
    """Re-geocode ALL places for a country with Kakao; also backfills city."""
    from services.geocoder import geocode_with_city, city_from_coords

    places = session.query(Place).filter(
        Place.country == country,
        Place.is_place == True,
    ).all()
    print(f"Re-geocoding {len(places)} places in {country}")

    updated = 0
    city_only = 0
    failed = 0
    for place in places:
        lat, lng, city = geocode_with_city(place.location_name, country=country)
        if lat is not None:
            place.lat = lat
            place.lng = lng
            if city:
                place.city = city
            session.commit()
            city_str = f" [{city}]" if city else ""
            print(f"  ✓ {place.location_name}{city_str} → {lat:.4f}, {lng:.4f}")
            updated += 1
        elif place.lat is not None and place.city is None:
            # Keyword search failed but we have existing coords — reverse geocode for city only
            city = city_from_coords(place.lat, place.lng)
            if city:
                place.city = city
                session.commit()
                print(f"  ~ {place.location_name} [{city}] (city from coords)")
                city_only += 1
            else:
                print(f"  ✗ {place.location_name} — no result")
                failed += 1
        else:
            print(f"  ✗ {place.location_name} — no result")
            failed += 1

    print(f"\nRe-geocode: {updated} updated, {city_only} city-only, {failed} failed")


def canonicalize_labels_all(session, *, apply=True):
    """Add canonical tags to fragmented labels across all rows (see
    services.label_canonicalizer). Additive + idempotent. apply=False = dry run.

    Returns (rows_changed, additions) where additions is [(name, added_tags), ...].
    """
    from services.label_canonicalizer import canonicalize_labels

    changed = 0
    additions = []
    for place in session.query(Place).filter(Place.labels.isnot(None)).all():
        current = place.labels or []
        new = canonicalize_labels(current)
        if new != current:
            added = [t for t in new if t not in current]
            additions.append((place.location_name, added))
            if apply:
                place.labels = new
            changed += 1
    if apply:
        session.commit()
    return changed, additions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post-job cleanup")
    parser.add_argument("--canonicalize-labels", action="store_true", help="Add canonical tags to fragmented labels (additive, idempotent)")
    parser.add_argument("--dry-run", action="store_true", help="With --canonicalize-labels: show changes without writing")
    parser.add_argument("--fix-geocoding", action="store_true", help="Re-geocode places with wrong coordinates")
    parser.add_argument("--regeocde-all", action="store_true", help="Re-geocode ALL places for a country with Kakao and backfill city")
    parser.add_argument("--country", default="South Korea", help="Expected country for geocoding validation (default: South Korea)")
    parser.add_argument("--retry-failures", action="store_true", help="Retry failed URLs from the latest job")
    parser.add_argument("--job-id", help="Job ID to retry failures for (defaults to latest)")
    parser.add_argument("--collections-file", help="Path to saved_collections.json for caption lookup")
    parser.add_argument("--collection", help="Collection name to load captions from")
    parser.add_argument("--resolve-handles", action="store_true", help="Re-fetch one post per author to get @handles and profile URLs")
    args = parser.parse_args()

    if not any([args.fix_geocoding, args.retry_failures, args.resolve_handles,
                getattr(args, 'regeocde_all', False), args.canonicalize_labels]):
        parser.print_help()
        sys.exit(1)

    session = SessionLocal()
    try:
        if args.canonicalize_labels:
            mode = "DRY RUN" if args.dry_run else "APPLYING"
            print(f"=== Canonicalizing labels ({mode}) ===")
            changed, additions = canonicalize_labels_all(session, apply=not args.dry_run)
            for name, added in additions:
                print(f"  {name}: +{added}")
            print(f"\nLabel canonicalization: {changed} rows {'would change' if args.dry_run else 'changed'}")

        if args.fix_geocoding:
            print(f"=== Re-geocoding places outside {args.country} ===")
            fix_geocoding(session, country=args.country)

        if getattr(args, 'regeocde_all', False):
            print(f"=== Re-geocoding ALL {args.country} places with Kakao ===")
            regeocde_all(session, country=args.country)

        if args.retry_failures:
            job_id = args.job_id
            if not job_id:
                latest = session.query(Job).order_by(Job.created_at.desc()).first()
                if not latest:
                    print("No jobs found")
                    sys.exit(1)
                job_id = latest.id
                print(f"Using latest job: {job_id}")
            print(f"\n=== Retrying failures for job {job_id} ===")
            retry_failures(session, job_id, args.collections_file, args.collection)

        if args.resolve_handles:
            print("\n=== Resolving @handles from Instagram ===")
            resolve_handles(session)
    finally:
        session.close()
