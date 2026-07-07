import re
import time
from typing import Optional
from uuid import uuid4

from rapidfuzz import fuzz as _fuzz

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import get_db, SessionLocal
from models import Job, CdnUrlCache
from schemas import ExtractResponse
from services import url_parser, transcriber, extractor, geocoder, deduplicator, noise_filter
from services.fetchers import fetch_post
from services.geocoder import GeoResult
from services.text_utils import _transcript_matches_caption, normalize_name
from services.transcriber import RateLimitError

router = APIRouter()

_THIN_CAPTION_RE = re.compile(r'#\S+|@\S+|[\U00010000-\U0010ffff]', re.UNICODE)

PAUSE_THRESHOLD_FETCH = 3
PAUSE_THRESHOLD_TRANSCRIPTION = 3
PAUSE_THRESHOLD_EXTRACTION = 2


def _is_thin_caption(caption: str) -> bool:
    stripped = _THIN_CAPTION_RE.sub("", caption).strip()
    return len(stripped) < 50


def _is_empty_of_signal(raw_post) -> bool:
    """True when a post has no usable extraction signal — thin caption AND no
    tagged accounts, geotag, or comments. Such posts go to pending_review."""
    return (
        _is_thin_caption(raw_post.caption)
        and not raw_post.tagged_accounts
        and not raw_post.location_string
        and not raw_post.top_comments
    )


def _is_language_mismatch(detected_language: str | None, caption: str | None) -> bool:
    """Return True if AssemblyAI detected English for a clearly non-Latin-script caption."""
    if not detected_language or not caption:
        return False
    if detected_language == "en":
        non_ascii = sum(1 for c in caption if ord(c) > 127)
        return non_ascii / max(len(caption), 1) > 0.3
    return False


def _pause_job(job, session, remaining_posts, reason_code, reason_message,
               warnings, failed_urls, pending_review, updated_place_ids):
    warnings.append({"code": reason_code, "message": reason_message})
    job.status = "paused"
    job.paused_reason = reason_code
    job.remaining_posts = remaining_posts
    job.warnings = warnings
    job.failed_urls = failed_urls
    job.pending_review = pending_review
    job.updated_place_ids = updated_place_ids
    job.current_url = None
    session.commit()


def _upsert_cdn_cache(session, cdn_url: str, job_id: str):
    from datetime import datetime, timezone
    entry = session.get(CdnUrlCache, cdn_url)
    if entry:
        entry.hit_count += 1
        entry.last_seen_at = datetime.now(timezone.utc)
    else:
        entry = CdnUrlCache(cdn_url=cdn_url, hit_count=1, first_seen_job_id=job_id)
        session.add(entry)
    session.commit()


# CDN URLs seen this many times across all jobs are flagged as suspicious
_CROSS_JOB_COLLISION_THRESHOLD = 5


def process_job(job_id: str, posts: list[dict]):
    """posts is a list of {url, caption?} dicts."""
    session = SessionLocal()
    try:
        job = session.get(Job, job_id)
        job.status = "processing"
        session.commit()

        pending_review = list(job.pending_review or [])
        failed_urls = list(job.failed_urls or [])
        updated_place_ids = list(job.updated_place_ids or [])

        from models import Place as PlaceModel
        existing_place_rows = session.query(PlaceModel.source_urls, PlaceModel.transcript_missing).all()
        seen_urls: set[str] = set()
        retranscribe_urls: set[str] = set()
        for (urls, tm) in existing_place_rows:
            for u in (urls or []):
                norm = u.rstrip("/")
                seen_urls.add(norm)
                if tm:
                    retranscribe_urls.add(norm)

        cdn_url_hits: dict[str, int] = {}
        seen_cdn_urls: set[str] = set()
        warnings = list(job.warnings or [])
        geocode_cache: dict[tuple[str, str | None, str | None], GeoResult] = {}

        # On resume after CDN collision, start with transcription already disabled
        transcription_disabled = any(w["code"] == "cdn_collision" for w in warnings)

        consecutive_fetch_failures = 0
        consecutive_transcription_failures = 0
        consecutive_extraction_failures = 0

        for i, post in enumerate(posts):
            current_status = session.query(Job.status).filter(Job.id == job_id).scalar()
            if current_status == "cancelled":
                job.current_url = None
                session.commit()
                return

            url = post["url"]
            normalized = url.rstrip("/")
            is_retranscribe = normalized in retranscribe_urls
            if normalized in seen_urls and not is_retranscribe:
                job.processed = (job.processed or 0) + 1
                session.commit()
                continue

            embedded_caption = post.get("caption")
            job.current_url = url
            session.commit()

            try:
                raw_post = fetch_post(url, embedded_caption=embedded_caption)
                consecutive_fetch_failures = 0
            except Exception as e:
                consecutive_fetch_failures += 1
                failed_urls.append({"url": url, "error": str(e)})
                job.failed_urls = failed_urls
                session.commit()
                if consecutive_fetch_failures >= PAUSE_THRESHOLD_FETCH:
                    _pause_job(
                        job, session, posts[i:],
                        "fetch_error",
                        f"{consecutive_fetch_failures} consecutive posts failed to fetch. "
                        f"Apify may be rate-limited or blocked. Resume when the issue clears.",
                        warnings, failed_urls, pending_review, updated_place_ids,
                    )
                    return
                continue

            transcript = None
            transcript_missing = False
            detected_lang: str | None = None

            if raw_post.video_cdn_url and not transcription_disabled:
                cdn_url = raw_post.video_cdn_url

                cdn_url_hits[cdn_url] = cdn_url_hits.get(cdn_url, 0) + 1
                if cdn_url_hits[cdn_url] >= 3:
                    _pause_job(
                        job, session, posts[i:],
                        "cdn_collision",
                        f"CDN URL collision detected ({cdn_url_hits[cdn_url]} posts share the same "
                        f"video URL). Instagram's CDN is returning cached content for unrelated posts. "
                        f"Resuming will continue without transcription (caption-only) for remaining posts.",
                        warnings, failed_urls, pending_review, updated_place_ids,
                    )
                    return

                # Cross-job check: warn if this URL has been collision-prone in previous jobs
                cached = session.get(CdnUrlCache, cdn_url)
                if cached and cached.hit_count >= _CROSS_JOB_COLLISION_THRESHOLD:
                    warn_msg = (
                        f"CDN URL seen {cached.hit_count} times across previous jobs — "
                        f"skipping transcription to avoid stale cached transcript."
                    )
                    if not any(w["message"] == warn_msg for w in warnings):
                        warnings.append({"code": "cdn_collision_cross_job", "message": warn_msg})
                        job.warnings = warnings
                        session.commit()
                    transcript_missing = True

                if cdn_url in seen_cdn_urls:
                    transcript_missing = True
                elif native := raw_post.raw_json.get("transcript"):
                    transcript = native
                    seen_cdn_urls.add(cdn_url)
                    consecutive_transcription_failures = 0
                    _upsert_cdn_cache(session, cdn_url, job_id)
                else:
                    try:
                        result = transcriber.transcribe(cdn_url)
                        transcript = result.text
                        detected_lang = result.detected_language
                        seen_cdn_urls.add(cdn_url)
                        consecutive_transcription_failures = 0
                        _upsert_cdn_cache(session, cdn_url, job_id)
                    except RateLimitError as e:
                        consecutive_transcription_failures += 1
                        if consecutive_transcription_failures >= PAUSE_THRESHOLD_TRANSCRIPTION:
                            _pause_job(
                                job, session, posts[i:],
                                "assemblyai_rate_limit",
                                f"AssemblyAI rate limit hit {consecutive_transcription_failures} times in a row "
                                f"(Retry-After: {e.retry_after}s). Resume after the rate limit window clears.",
                                warnings, failed_urls, pending_review, updated_place_ids,
                            )
                            return
                        transcript_missing = True
                    except Exception:
                        consecutive_transcription_failures += 1
                        if consecutive_transcription_failures >= PAUSE_THRESHOLD_TRANSCRIPTION:
                            _pause_job(
                                job, session, posts[i:],
                                "assemblyai_error",
                                f"{consecutive_transcription_failures} consecutive transcription failures. "
                                f"AssemblyAI may be down or unavailable. Resume when the service recovers.",
                                warnings, failed_urls, pending_review, updated_place_ids,
                            )
                            return
                        if not is_retranscribe and _is_empty_of_signal(raw_post):
                            pending_review.append({
                                "url": url,
                                "reason": "no_transcript_thin_caption",
                            })
                            job.pending_review = pending_review
                            job.processed = (job.processed or 0) + 1
                            session.commit()
                            continue
                        transcript_missing = True

            elif raw_post.video_cdn_url and transcription_disabled:
                transcript_missing = True
                if not is_retranscribe and _is_empty_of_signal(raw_post):
                    pending_review.append({"url": url, "reason": "no_transcript_thin_caption"})
                    job.pending_review = pending_review
                    job.processed = (job.processed or 0) + 1
                    session.commit()
                    continue

            if transcript and (
                _is_language_mismatch(detected_lang, raw_post.caption)
                or not _transcript_matches_caption(transcript, raw_post.caption)
            ):
                transcript = None
                transcript_missing = True

            if is_retranscribe:
                if transcript:
                    to_update = [
                        p for p in
                        session.query(PlaceModel).filter(PlaceModel.transcript_missing == True).all()
                        if normalized in [u.rstrip("/") for u in (p.source_urls or [])]
                    ]
                    for place in to_update:
                        place.transcript = transcript
                        place.transcript_missing = False
                job.processed = (job.processed or 0) + 1
                session.commit()
                continue

            try:
                places = extractor.extract(raw_post, transcript)
                consecutive_extraction_failures = 0
            except extractor.ExtractionTruncated as e:
                # Per-post truncation: skip with a warning, do NOT count toward the pause threshold
                warnings.append({"code": "extraction_truncated", "message": str(e)})
                job.warnings = warnings
                job.processed = (job.processed or 0) + 1
                session.commit()
                continue
            except Exception as e:
                consecutive_extraction_failures += 1
                failed_urls.append({"url": url, "error": f"extraction failed: {e}"})
                job.failed_urls = failed_urls
                session.commit()
                if consecutive_extraction_failures >= PAUSE_THRESHOLD_EXTRACTION:
                    _pause_job(
                        job, session, posts[i:],
                        "extraction_error",
                        f"{consecutive_extraction_failures} consecutive extraction failures. "
                        f"The Anthropic API may be down or rate-limited. Resume when the service recovers.",
                        warnings, failed_urls, pending_review, updated_place_ids,
                    )
                    return
                continue

            # normalized name → place_id for is_place=True items in this URL (used to resolve venue FKs)
            url_place_map: dict[str, str] = {}
            venue_pending: list[tuple[str, str, str | None]] = []  # (non_place_id, venue_name, city)

            for extracted_place in places:
                try:
                    place_geocoder = None
                    place_geocoder_id = None
                    if extracted_place.is_place:
                        cache_key = (extracted_place.location_name,
                                     extracted_place.country, extracted_place.city)
                        if cache_key in geocode_cache:
                            geo = geocode_cache[cache_key]
                        else:
                            geo = geocoder.geocode_full(
                                extracted_place.location_name,
                                country=extracted_place.country,
                                expected_city=extracted_place.city,
                            )
                            geocode_cache[cache_key] = geo
                        lat, lng = geo.lat, geo.lng
                        place_geocoder = geo.provider
                        place_geocoder_id = geo.place_id
                        if extracted_place.city is None and geo.city:
                            extracted_place.city = geo.city
                    else:
                        lat, lng = None, None

                    # Keep (not drop) incidental/passing mentions, but surface them for review
                    if extracted_place.mention_type == "incidental":
                        msg = (f"'{extracted_place.location_name}' flagged as an incidental "
                               f"mention in {url} — kept but may not be a real recommendation.")
                        if not any(w.get("message") == msg for w in warnings):
                            warnings.append({"code": "incidental_mention", "message": msg})
                            job.warnings = warnings

                    place_id, _ = deduplicator.find_or_merge_place(
                        extracted_place, raw_post, lat, lng, job_id, session,
                        transcript=transcript,
                        transcript_missing=transcript_missing,
                        geocoder=place_geocoder,
                        geocoder_place_id=place_geocoder_id,
                    )
                    if extracted_place.is_place:
                        url_place_map[normalize_name(extracted_place.location_name)] = place_id
                    elif extracted_place.venue:
                        venue_pending.append((place_id, extracted_place.venue, extracted_place.city))
                    if place_id not in updated_place_ids:
                        updated_place_ids.append(place_id)
                    job.updated_place_ids = updated_place_ids
                    session.commit()
                except Exception as e:
                    failed_urls.append({"url": url, "error": f"place save failed: {e}"})
                    job.failed_urls = failed_urls
                    session.commit()

            # Resolve venue FKs: link dishes/products back to their parent Place
            for non_place_id, venue_name, venue_city in venue_pending:
                vnorm = normalize_name(venue_name)
                matched_id = url_place_map.get(vnorm)
                if not matched_id:
                    # Fuzzy fallback within this post: Korean-parenthetical variants, minor spelling
                    for place_norm, pid in url_place_map.items():
                        if (_fuzz.token_set_ratio(vnorm, place_norm) >= 85
                                and _fuzz.ratio(vnorm, place_norm) >= 70):
                            matched_id = pid
                            break
                if not matched_id and vnorm:
                    # Cross-post fallback: an existing Place matches the venue by normalized name + city
                    try:
                        q = session.query(PlaceModel).filter(
                            PlaceModel.is_place == True,  # noqa: E712
                            PlaceModel.normalized_name == vnorm,
                        )
                        if venue_city:
                            q = q.filter(or_(PlaceModel.city == venue_city, PlaceModel.city.is_(None)))
                        cand = q.first()
                        if cand:
                            matched_id = cand.id
                    except Exception:
                        pass
                if matched_id:
                    try:
                        non_place_row = session.get(PlaceModel, non_place_id)
                        if non_place_row and non_place_row.venue_place_id is None:
                            non_place_row.venue_place_id = matched_id
                            session.commit()
                    except Exception:
                        pass

            job.processed = (job.processed or 0) + 1
            session.commit()

        job.status = "complete" if not failed_urls and not pending_review else "complete_with_errors"
        job.failed_urls = failed_urls
        job.pending_review = pending_review
        job.updated_place_ids = updated_place_ids
        job.paused_reason = None
        job.remaining_posts = []
        job.current_url = None
        session.commit()

        # Recompute ambient-noise flags (dominant home country/city + media) over the
        # whole table. Best-effort: never let this fail a completed job.
        try:
            noise_filter.flag_ambient_places(session)
        except Exception:
            session.rollback()

    except Exception as e:
        try:
            session.rollback()
        except Exception:
            pass
        job = session.get(Job, job_id)
        if job:
            job.status = "complete_with_errors"
            job.failed_urls = (job.failed_urls or []) + [{"url": "pipeline", "error": str(e)}]
            session.commit()
    finally:
        session.close()


@router.post("/api/jobs/{job_id}/resume")
async def resume_job(job_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "paused":
        raise HTTPException(status_code=400, detail=f"Job is not paused (status: {job.status})")

    remaining = list(job.remaining_posts or [])
    if not remaining:
        raise HTTPException(status_code=400, detail="No remaining posts to process")

    job.status = "processing"
    job.remaining_posts = []
    # paused_reason stays set so process_job can derive state (e.g. cdn_collision → transcription disabled)
    db.commit()

    background_tasks.add_task(process_job, job_id, remaining)
    return {"status": "processing"}


@router.post("/api/collections")
async def collections_endpoint(file: UploadFile = File(...)):
    content = await file.read()
    try:
        collections = url_parser.list_collections(content)
    except Exception:
        raise HTTPException(status_code=422, detail="Could not parse JSON file")
    return collections


@router.post("/api/extract", response_model=ExtractResponse)
async def extract_endpoint(
    background_tasks: BackgroundTasks,
    file: Optional[UploadFile] = File(None),
    urls: Optional[str] = Form(None),
    collection: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    if file is None and not urls:
        raise HTTPException(status_code=422, detail="Provide either a file or urls")

    if file:
        content = await file.read()
        try:
            if collection:
                post_list = url_parser.parse_posts_from_collection(content, collection)
            else:
                urls_only = url_parser.parse_urls_from_json(content)
                post_list = [{"url": u} for u in urls_only]
        except Exception:
            raise HTTPException(status_code=422, detail="Could not parse JSON file — check format")
    else:
        urls_only = url_parser.parse_urls_from_text(urls)
        post_list = [{"url": u} for u in urls_only]

    if not post_list:
        raise HTTPException(status_code=422, detail="No supported URLs found (Instagram posts/reels or YouTube videos/playlists)")

    expanded = url_parser.expand_playlists([p["url"] for p in post_list])
    url_to_caption = {p["url"]: p.get("caption") for p in post_list}
    post_list = [{"url": u, "caption": url_to_caption.get(u)} for u in expanded]

    job_id = str(uuid4())
    job = Job(
        id=job_id,
        status="pending",
        total_urls=len(post_list),
        processed=0,
        pending_review=[],
        failed_urls=[],
        updated_place_ids=[],
    )
    db.add(job)
    db.commit()

    background_tasks.add_task(process_job, job_id, post_list)
    return ExtractResponse(job_id=job_id)
