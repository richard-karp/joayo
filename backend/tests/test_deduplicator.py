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


def test_coord_proximity_unrelated_names_no_merge(db_session):
    """Two genuinely different venues within 150m must not merge when names share no tokens."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted("Gyeongbokgung Palace"), raw1, 37.579, 126.977, "job1", db_session)
    # A cafe 50m away — completely unrelated name
    cafe = ExtractedPlace(
        location_name="Cafe Bora",
        category="eat", subcategory="cafe", is_place=True,
        summary="Purple latte cafe.", labels=[], insider_tips="",
    )
    id2, is_new2 = find_or_merge_place(cafe, raw2, 37.5791, 126.977, "job1", db_session)
    assert is_new2 is True
    assert id1 != id2


def test_coord_proximity_different_names_merges(db_session):
    """Same location geocoded slightly differently by two posts with different names."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted("Gwangjang Market"), raw1, 37.5697, 127.0094, "job1", db_session)
    # Different name, but coords are only ~10m apart
    id2, is_new2 = find_or_merge_place(_extracted("Gwangjang Traditional Market"), raw2, 37.5698, 127.0094, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2


def test_fuzzy_name_nearby_coords_merges(db_session):
    """Fuzzy name match at same location — e.g. 'Gwangjang Market' vs 'Gwangjang Traditional Market' with minor coord difference."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted("Gwangjang Market"), raw1, 37.5697, 127.0094, "job1", db_session)
    # Coords 300m apart (within fuzzy 500m radius), names fuzzy-match
    id2, is_new2 = find_or_merge_place(_extracted("Gwangjang Traditional Market"), raw2, 37.572, 127.0094, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2


def test_fuzzy_name_distant_coords_creates_new(db_session):
    """Same fuzzy name but far apart — two different franchise locations."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted("Gwangjang Market"), raw1, 37.5697, 127.0094, "job1", db_session)
    # 111km apart — clearly a different location
    id2, is_new2 = find_or_merge_place(_extracted("Gwangjang Traditional Market"), raw2, 38.5697, 127.0094, "job1", db_session)
    assert is_new2 is True
    assert id1 != id2


def test_fuzzy_name_no_coords_merges(db_session):
    """Fuzzy name match when neither record has geocoords."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted("Gyeongbokgung Palace"), raw1, None, None, "job1", db_session)
    id2, is_new2 = find_or_merge_place(_extracted("Gyeongbokgung"), raw2, None, None, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2


def _extracted_with_country(name: str, country: str, city: str | None = None) -> ExtractedPlace:
    return ExtractedPlace(
        location_name=name,
        category="eat", subcategory="restaurant", is_place=True,
        country=country, city=city,
        summary="A place.", labels=[], insider_tips="",
    )


def test_fuzzy_name_different_country_no_merge(db_session):
    """Same fuzzy name but different countries → should not merge even without coords."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    find_or_merge_place(_extracted_with_country("Gwangjang Market", "South Korea"), raw1, None, None, "job1", db_session)
    # Country-filtered query won't include the Korean place, so no fuzzy match
    id2, is_new2 = find_or_merge_place(_extracted_with_country("Gwangjang Traditional Market", "Japan"), raw2, None, None, "job1", db_session)
    assert is_new2 is True


# ── Dish/venue dedup (#1) ─────────────────────────────────────────────────────

def _dish(name: str, venue: str, city: str | None = "Seoul") -> ExtractedPlace:
    return ExtractedPlace(
        location_name=name, category="eat", subcategory="dish",
        is_place=False, venue=venue, country="South Korea", city=city,
        summary="A dish.", labels=[], insider_tips="",
    )


def test_same_dish_different_venue_stays_separate(db_session):
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_dish("Bibimbap", "Restaurant A"), raw1, None, None, "job1", db_session)
    id2, is_new2 = find_or_merge_place(_dish("Bibimbap", "Restaurant B"), raw2, None, None, "job1", db_session)
    assert is_new2 is True
    assert id1 != id2


def test_same_dish_same_venue_merges(db_session):
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(_dish("Bibimbap", "Restaurant A"), raw1, None, None, "job1", db_session)
    id2, is_new2 = find_or_merge_place(_dish("Bibimbap", "Restaurant A"), raw2, None, None, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2


# ── Provider-id-first dedup (#9) ──────────────────────────────────────────────

def test_provider_id_merges_romanization_variants(db_session):
    """Two romanization variants at the same Kakao POI id merge, despite name divergence."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(
        _extracted("Gyeongbokgung Palace"), raw1, 37.5796, 126.9770, "job1", db_session,
        geocoder="kakao", geocoder_place_id="POI-123",
    )
    # Different romanization + slightly different coords, but same provider place id
    id2, is_new2 = find_or_merge_place(
        _extracted("Kyungbok Palace"), raw2, 37.5798, 126.9772, "job1", db_session,
        geocoder="kakao", geocoder_place_id="POI-123",
    )
    assert is_new2 is False
    assert id1 == id2


def test_provider_id_scoped_to_places_not_dishes(db_session):
    """A dish and its venue may share a POI id — they must NOT merge on provider id."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    venue_id, _ = find_or_merge_place(
        _extracted("Gwangjang Market"), raw1, 37.5697, 127.0094, "job1", db_session,
        geocoder="kakao", geocoder_place_id="POI-999",
    )
    dish = _dish("Bindaetteok", "Gwangjang Market")
    dish_id, is_new = find_or_merge_place(
        dish, raw2, None, None, "job1", db_session,
        geocoder="kakao", geocoder_place_id="POI-999",
    )
    assert is_new is True
    assert dish_id != venue_id


def test_normalize_collapses_suffix_variants(db_session):
    """normalize_name collapses type-suffix variants even without coords."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    id1, _ = find_or_merge_place(
        _extracted_with_country("Insadong neighborhood", "South Korea", "Seoul"),
        raw1, None, None, "job1", db_session)
    id2, is_new2 = find_or_merge_place(
        _extracted_with_country("Insadong", "South Korea", "Seoul"),
        raw2, None, None, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2


# ── Coord gate over-merge guard (#11) ─────────────────────────────────────────

def test_adjacent_different_category_near_coords_stays_separate(db_session):
    """Two distinct businesses at near-identical coords with different categories don't merge."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    palace = _extracted("Aaa Bbb")  # see_visit / palace
    id1, _ = find_or_merge_place(palace, raw1, 37.5700, 127.0000, "job1", db_session)
    cafe = ExtractedPlace(
        location_name="Ccc Ddd", category="eat", subcategory="cafe", is_place=True,
        summary="A cafe.", labels=[], insider_tips="",
    )
    # ~10m away, unrelated name, different category → must not merge
    id2, is_new2 = find_or_merge_place(cafe, raw2, 37.57001, 127.0000, "job1", db_session)
    assert is_new2 is True
    assert id1 != id2


def test_null_country_candidate_still_found_by_fuzzy(db_session):
    """#4: an existing duplicate with null country/city is still reachable by fuzzy match."""
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="user_a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="user_b", author_platform_id="2")
    # First record has no country/city inferred
    id1, _ = find_or_merge_place(_extracted("Gwangjang Market"), raw1, None, None, "job1", db_session)
    # Second record has country/city; the null-country candidate must still be found
    id2, is_new2 = find_or_merge_place(
        _extracted_with_country("Gwangjang Traditional Market", "South Korea", "Seoul"),
        raw2, None, None, "job1", db_session)
    assert is_new2 is False
    assert id1 == id2
