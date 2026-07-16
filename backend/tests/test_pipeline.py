from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
from models import Job, Place
from routes.extract import process_job
from schemas import ExtractedPlace
from services.raw_post import RawPost
from services.geocoder import GeoResult
from services.transcriber import TranscriptResult
from tests.conftest import make_raw_post


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _make_job(session, urls: list[str]) -> str:
    job_id = str(uuid4())
    job = Job(
        id=job_id,
        status="pending",
        total_urls=len(urls),
        processed=0,
        pending_review=[],
        failed_urls=[],
        updated_place_ids=[],
    )
    session.add(job)
    session.commit()
    return job_id


def _posts(urls: list[str]) -> list[dict]:
    return [{"url": u} for u in urls]


def _mock_extractor_result(names=("Gyeongbokgung Palace",)):
    return [
        ExtractedPlace(
            location_name=name,
            category="see_visit",
            subcategory="palace",
            is_place=True,
            summary="Historic site.",
            labels=[],
            insider_tips="Go early.",
        )
        for name in names
    ]


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full")
@patch("routes.extract.extractor.extract")
@patch("routes.extract.fetch_post")
def test_happy_path(mock_fetch, mock_extract, mock_geocode, mock_session_cls, db):
    mock_session_cls.return_value = db
    # Two distinct places with distinct coordinates so coord-proximity check doesn't merge them
    mock_geocode.side_effect = [
        GeoResult(lat=37.579, lng=126.977, city="Seoul", provider="kakao"),
        GeoResult(lat=37.5697, lng=127.0094, city="Seoul", provider="kakao"),
    ]
    mock_fetch.side_effect = [
        make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1"),
        make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2"),
    ]
    mock_extract.side_effect = [
        _mock_extractor_result(names=("Gyeongbokgung Palace",)),
        _mock_extractor_result(names=("Gwangjang Market",)),
    ]

    urls = [
        "https://www.instagram.com/p/A1/",
        "https://www.instagram.com/p/B2/",
    ]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.status == "complete"
    assert job.processed == 2
    assert len(job.updated_place_ids) == 2


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full", return_value=GeoResult())
@patch("routes.extract.extractor.extract")
@patch("routes.extract.fetch_post")
def test_failed_fetch_adds_to_failed_urls(mock_fetch, mock_extract, mock_geocode, mock_session_cls, db):
    mock_session_cls.return_value = db
    mock_fetch.side_effect = [Exception("404 not found"), make_raw_post()]
    mock_extract.return_value = _mock_extractor_result()

    urls = ["https://www.instagram.com/p/BAD/", "https://www.instagram.com/p/GOOD/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.status == "complete_with_errors"
    assert len(job.failed_urls) == 1
    assert job.failed_urls[0]["url"] == "https://www.instagram.com/p/BAD/"


@patch("routes.extract.SessionLocal")
@patch("routes.extract.transcriber.transcribe")
@patch("routes.extract.fetch_post")
def test_thin_caption_no_transcript_goes_to_pending_review(mock_fetch, mock_transcribe, mock_session_cls, db):
    mock_session_cls.return_value = db
    mock_fetch.return_value = make_raw_post(
        caption="#korea #travel",  # thin: only hashtags
        video_cdn_url="https://cdn.example.com/video.mp4",
        tagged_accounts=[],       # no tagged accounts
        location_string=None,     # no geotag
        top_comments=[],          # no comments — gate should fire
    )
    mock_transcribe.side_effect = RuntimeError("transcription failed")

    urls = ["https://www.instagram.com/reel/THIN/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.status == "complete_with_errors"
    assert len(job.pending_review) == 1
    assert job.pending_review[0]["reason"] == "no_transcript_thin_caption"


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full", return_value=GeoResult(lat=37.579, lng=126.977, city="Seoul", provider="kakao"))
@patch("routes.extract.extractor.extract")
@patch("routes.extract.transcriber.transcribe")
@patch("routes.extract.fetch_post")
def test_thin_caption_with_geotag_not_pending_review(
    mock_fetch, mock_transcribe, mock_extract, mock_geocode, mock_session_cls, db
):
    """#6: a thin caption WITH a geotag is not treated as empty — it gets extracted."""
    mock_session_cls.return_value = db
    mock_fetch.return_value = make_raw_post(
        caption="#korea #travel",  # thin
        video_cdn_url="https://cdn.example.com/video.mp4",
        tagged_accounts=[],
        location_string="Gyeongbokgung Palace, Seoul",  # geotag present
        top_comments=[],
    )
    mock_transcribe.side_effect = RuntimeError("transcription failed")
    mock_extract.return_value = _mock_extractor_result()

    urls = ["https://www.instagram.com/reel/GEOTAG/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.pending_review == []
    assert len(job.updated_place_ids) == 1


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full", return_value=GeoResult())
@patch("routes.extract.extractor.extract")
@patch("routes.extract.transcriber.transcribe")
@patch("routes.extract.fetch_post")
def test_substantive_caption_no_transcript_sets_flag(
    mock_fetch, mock_transcribe, mock_extract, mock_geocode, mock_session_cls, db
):
    mock_session_cls.return_value = db
    mock_fetch.return_value = make_raw_post(
        caption="This is a really detailed caption about visiting an amazing place in Seoul.",
        video_cdn_url="https://cdn.example.com/video.mp4",
    )
    mock_transcribe.side_effect = RuntimeError("failed")
    mock_extract.return_value = _mock_extractor_result()

    urls = ["https://www.instagram.com/reel/SUBST/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.pending_review == []
    place_id = job.updated_place_ids[0]
    place = db.get(Place, place_id)
    assert place.transcript_missing is True


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full", return_value=GeoResult())
@patch("routes.extract.extractor.extract")
@patch("routes.extract.transcriber.transcribe")
@patch("routes.extract.fetch_post")
def test_transcript_success(
    mock_fetch, mock_transcribe, mock_extract, mock_geocode, mock_session_cls, db
):
    """Successful transcription produces transcript_missing=False on the place."""
    mock_session_cls.return_value = db
    mock_fetch.return_value = make_raw_post(
        video_cdn_url="https://cdn.example.com/video.mp4",
    )
    mock_transcribe.return_value = TranscriptResult(text="Transcript text here.", detected_language="en")
    mock_extract.return_value = _mock_extractor_result()

    urls = ["https://www.instagram.com/reel/OK/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.pending_review == []
    place = db.get(Place, job.updated_place_ids[0])
    assert place.transcript_missing is False


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full", return_value=GeoResult(lat=37.579, lng=126.977, city="Seoul", provider="kakao"))
@patch("routes.extract.extractor.extract")
@patch("routes.extract.fetch_post")
def test_youtube_video_pipeline(mock_fetch, mock_extract, mock_geocode, mock_session_cls, db):
    mock_session_cls.return_value = db
    mock_fetch.return_value = RawPost(
        platform="youtube",
        url="https://www.youtube.com/watch?v=vid1",
        author="korea_travel_yt",
        author_platform_id="UCmock123",
        caption="Seoul travel guide with lots of great content about places to visit.",
        hashtags=["seoul", "travel"],
        tagged_accounts=[],
        video_cdn_url="https://www.youtube.com/watch?v=vid1",
        location_string=None,
        top_comments=[],
        date_posted=datetime(2024, 3, 10),
        raw_json={"transcript": "We visit Gyeongbokgung Palace today in Seoul."},
    )
    mock_extract.return_value = _mock_extractor_result(names=("Gyeongbokgung Palace",))

    urls = ["https://www.youtube.com/watch?v=vid1"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    job = db.get(Job, job_id)
    assert job.status == "complete"
    assert job.processed == 1
    place = db.get(Place, job.updated_place_ids[0])
    assert place.platform == "youtube"
    assert place.primary_author == "korea_travel_yt"


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full", return_value=GeoResult())
@patch("routes.extract.extractor.extract")
@patch("routes.extract.fetch_post")
def test_duplicate_place_across_calls(mock_fetch, mock_extract, mock_geocode, mock_session_cls, db):
    mock_session_cls.return_value = db
    raw_a = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw_b = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    mock_fetch.side_effect = [raw_a, raw_b]
    mock_extract.return_value = _mock_extractor_result()  # same place name both times

    urls = ["https://www.instagram.com/p/A1/", "https://www.instagram.com/p/B2/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    assert db.query(Place).count() == 1
    place = db.query(Place).first()
    assert len(place.all_authors) == 2


@patch("routes.extract.SessionLocal")
@patch("routes.extract.geocoder.geocode_full",
       return_value=GeoResult(lat=33.45, lng=126.57, city="Jeju", provider="kakao"))
@patch("routes.extract.extractor.extract")
@patch("routes.extract.fetch_post")
def test_freeform_city_label_is_canonicalized_on_store(
    mock_fetch, mock_extract, mock_geocode, mock_session_cls, db
):
    """A place Claude labels "Jeju Island" is stored as "Jeju", so it can never create
    a duplicate city alongside pins already labeled "Jeju"."""
    mock_session_cls.return_value = db
    mock_fetch.return_value = make_raw_post(url="https://www.instagram.com/p/JEJU/")
    mock_extract.return_value = [
        ExtractedPlace(
            location_name="Gamttanam Cafe",
            category="eat",
            subcategory="cafe",
            is_place=True,
            country="South Korea",
            city="Jeju Island",  # freeform variant of the canonical "Jeju"
            summary="Seaside cafe.",
            labels=[],
            insider_tips="",
        )
    ]

    urls = ["https://www.instagram.com/p/JEJU/"]
    job_id = _make_job(db, urls)
    process_job(job_id, _posts(urls))

    place = db.query(Place).one()
    assert place.city == "Jeju"
