from datetime import datetime, timezone

import pytest
from tests.conftest import make_raw_post
from schemas import ExtractedPlace
from services.deduplicator import find_or_merge_place
from models import Place


def _extracted(name="Gyeongbokgung Palace") -> ExtractedPlace:
    return ExtractedPlace(
        location_name=name,
        category="see_visit",
        subcategory="palace",
        is_place=True,
        summary="Historic palace in Seoul.",
        labels=["must-see"],
        insider_tips="Go early to avoid crowds.",
    )


def test_new_place_created(db_session):
    raw = make_raw_post()
    place_id, is_new = find_or_merge_place(_extracted(), raw, 37.579, 126.977, "job1", db_session)
    assert is_new is True
    place = db_session.get(Place, place_id)
    assert place.location_name == "Gyeongbokgung Palace"
    assert place.primary_author == "travel_user"
    assert place.source_urls == [raw.url]


def test_duplicate_by_name_and_nearby_coords(db_session):
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted(), raw1, 37.579, 126.977, "job1", db_session)
    id2, is_new2 = find_or_merge_place(_extracted(), raw2, 37.5791, 126.977, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2
    place = db_session.get(Place, id1)
    assert len(place.source_urls) == 2
    assert len(place.all_authors) == 2


def test_same_name_distant_coords_creates_new(db_session):
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted(), raw1, 37.579, 126.977, "job1", db_session)
    # 1 degree lat ≈ 111km — clearly outside 150m radius
    id2, is_new2 = find_or_merge_place(_extracted(), raw2, 38.579, 126.977, "job1", db_session)
    assert is_new2 is True
    assert id1 != id2


def test_name_match_no_coords_merges(db_session):
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted(), raw1, None, None, "job1", db_session)
    id2, is_new2 = find_or_merge_place(_extracted(), raw2, None, None, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2


def test_primary_author_updated_on_earlier_date(db_session):
    newer = make_raw_post(
        author="later_user", author_platform_id="2",
        url="https://www.instagram.com/p/B2/",
        date_posted=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    older = make_raw_post(
        author="early_user", author_platform_id="3",
        url="https://www.instagram.com/p/C3/",
        date_posted=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    id1, _ = find_or_merge_place(_extracted(), newer, None, None, "job1", db_session)
    find_or_merge_place(_extracted(), older, None, None, "job1", db_session)

    place = db_session.get(Place, id1)
    assert place.primary_author == "early_user"
    # SQLite stores naive datetimes; compare without tzinfo
    assert place.earliest_date_posted == datetime(2023, 1, 1, 0, 0)


def test_newer_post_does_not_change_primary_author(db_session):
    older = make_raw_post(
        author="early_user", author_platform_id="1",
        url="https://www.instagram.com/p/A1/",
        date_posted=datetime(2023, 1, 1, tzinfo=timezone.utc),
    )
    newer = make_raw_post(
        author="later_user", author_platform_id="2",
        url="https://www.instagram.com/p/B2/",
        date_posted=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )
    id1, _ = find_or_merge_place(_extracted(), older, None, None, "job1", db_session)
    find_or_merge_place(_extracted(), newer, None, None, "job1", db_session)

    place = db_session.get(Place, id1)
    assert place.primary_author == "early_user"


def test_handle_change_updates_username_in_place(db_session):
    raw1 = make_raw_post(author="old_handle", author_platform_id="99",
                         url="https://www.instagram.com/p/A1/")
    raw2 = make_raw_post(
        author="new_handle", author_platform_id="99",  # same platform_id
        url="https://www.instagram.com/p/B2/",
        date_posted=datetime(2024, 7, 1, tzinfo=timezone.utc),
    )
    id1, _ = find_or_merge_place(_extracted(), raw1, None, None, "job1", db_session)
    find_or_merge_place(_extracted(), raw2, None, None, "job1", db_session)

    place = db_session.get(Place, id1)
    # Only one author entry (same platform_id), username updated
    assert len(place.all_authors) == 1
    assert place.all_authors[0]["username"] == "new_handle"


def test_source_urls_no_duplicates(db_session):
    raw = make_raw_post()
    id1, _ = find_or_merge_place(_extracted(), raw, None, None, "job1", db_session)
    find_or_merge_place(_extracted(), raw, None, None, "job1", db_session)
    place = db_session.get(Place, id1)
    assert len(place.source_urls) == 1
