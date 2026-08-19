"""The address column, from the geocoder to the row.

`GeoResult.address` was populated by `_kakao_full` and consumed by nothing — every geocoded row
fetched an address and dropped it. Nothing in the suite could see that, because a value no code
reads is invisible to tests written against behaviour. These are the channel that can see it.
"""
from tests.conftest import make_raw_post
from schemas import ExtractedPlace
from services.deduplicator import find_or_merge_place
from models import Place

SEOUL = (37.579, 126.977)
KAKAO_ADDR = "서울 종로구 사직로 161"
ROAD_ADDR = "서울 종로구 세종로 1-1"


def _extracted(name="Gyeongbokgung Palace", **kw) -> ExtractedPlace:
    return ExtractedPlace(
        location_name=name,
        category=kw.pop("category", "see_visit"),
        subcategory=kw.pop("subcategory", "palace"),
        is_place=True,
        summary="Historic palace in Seoul.",
        labels=["must-see"],
        insider_tips="Go early.",
        **kw,
    )


def test_address_is_stored_on_a_new_place(db_session):
    raw = make_raw_post()
    pid, is_new = find_or_merge_place(
        _extracted(), raw, *SEOUL, "job1", db_session, address=KAKAO_ADDR
    )
    assert is_new is True
    assert db_session.get(Place, pid).address == KAKAO_ADDR


def test_address_defaults_to_none_when_the_geocoder_gave_none(db_session):
    # Nominatim results and the no-match path carry no address. Absent must stay absent rather
    # than becoming an empty string, so `address IS NULL` remains the backfill's selector.
    raw = make_raw_post()
    pid, _ = find_or_merge_place(_extracted(), raw, *SEOUL, "job1", db_session)
    assert db_session.get(Place, pid).address is None


def test_a_merge_fills_a_missing_address(db_session):
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="b", author_platform_id="2")
    id1, _ = find_or_merge_place(_extracted(), raw1, *SEOUL, "job1", db_session)
    assert db_session.get(Place, id1).address is None

    id2, is_new = find_or_merge_place(
        _extracted(), raw2, 37.5791, 126.977, "job1", db_session, address=KAKAO_ADDR
    )
    assert is_new is False and id1 == id2
    assert db_session.get(Place, id1).address == KAKAO_ADDR


def test_a_merge_never_overwrites_an_address_already_present(db_session):
    # ⛔ The one that matters. An address may have been curated by hand or recovered from the
    # POI the row actually resolved to; a later post re-geocoding the same venue must not be
    # able to revert it. Fill-only, exactly like geocoder_place_id and native_name above it.
    raw1 = make_raw_post(url="https://www.instagram.com/p/A1/", author="a", author_platform_id="1")
    raw2 = make_raw_post(url="https://www.instagram.com/p/B2/", author="b", author_platform_id="2")
    id1, _ = find_or_merge_place(
        _extracted(), raw1, *SEOUL, "job1", db_session, address=ROAD_ADDR
    )
    id2, is_new = find_or_merge_place(
        _extracted(), raw2, 37.5791, 126.977, "job1", db_session, address=KAKAO_ADDR
    )
    assert is_new is False and id1 == id2
    assert db_session.get(Place, id1).address == ROAD_ADDR


def test_address_is_absent_on_non_place_items(db_session):
    # A dish is not geocoded, so it has no address to carry. Guards against a future caller
    # threading the parent venue's address onto its children, which would make `address` mean
    # two different things depending on `is_place`.
    raw = make_raw_post()
    dish = ExtractedPlace(
        location_name="Ganjang gejang",
        category="eat",
        subcategory="dish",
        is_place=False,
        venue="Gwangjang Market",
        summary="Soy-marinated raw crab.",
        labels=[],
        insider_tips="",
    )
    pid, _ = find_or_merge_place(dish, raw, None, None, "job1", db_session)
    assert db_session.get(Place, pid).address is None


def test_address_reaches_the_api_response(db_session):
    # The column exists so downstream can read it — KFP's promotion export and Taste Stew's
    # googlePlaceUrl both go through PlaceResponse. A column the API drops is still discarded,
    # just one layer later.
    from schemas import PlaceResponse

    raw = make_raw_post()
    pid, _ = find_or_merge_place(
        _extracted(), raw, *SEOUL, "job1", db_session, address=KAKAO_ADDR
    )
    resp = PlaceResponse.model_validate(db_session.get(Place, pid))
    assert resp.address == KAKAO_ADDR
