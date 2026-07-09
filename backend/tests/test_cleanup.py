from uuid import uuid4

import cleanup
from models import Place


def _add(session, name, *, lat, lng, country=None, is_place=True):
    pid = str(uuid4())
    session.add(Place(
        id=pid, location_name=name, category="eat", subcategory="restaurant",
        is_place=is_place, country=country, lat=lat, lng=lng,
        source_urls=["https://x/" + pid], platform="instagram",
    ))
    session.commit()
    return pid


def test_fix_geocoding_leaves_explicitly_foreign_places_untouched(db_session, mocker):
    """fix_geocoding must not clobber a genuinely-foreign venue back into Korea.

    A Tokyo restaurant (country='Japan') has correct Japanese coordinates that fall
    outside the Korea bounding box — it should be skipped, not re-geocoded."""
    # Would-be re-geocode target: any call returns a Seoul coordinate.
    geo = mocker.patch.object(cleanup.geocoder, "geocode", return_value=(37.5665, 126.9780))

    foreign_id = _add(db_session, "Tokyo Sushi Bar", lat=35.6762, lng=139.6503, country="Japan")
    kr_bad_id = _add(db_session, "Seoul Cafe", lat=40.7580, lng=-73.9720, country="South Korea")

    cleanup.fix_geocoding(db_session)

    db_session.expire_all()
    foreign = db_session.get(Place, foreign_id)
    kr_bad = db_session.get(Place, kr_bad_id)

    # Foreign place untouched, and never passed to the geocoder.
    assert (foreign.lat, foreign.lng) == (35.6762, 139.6503)
    assert all(c.args[0] != "Tokyo Sushi Bar" for c in geo.call_args_list)

    # The Korea-expected place that was mis-geocoded gets re-geocoded into Korea.
    assert (round(kr_bad.lat, 4), round(kr_bad.lng, 4)) == (37.5665, 126.9780)


def test_fix_geocoding_nulls_unresolvable_korea_place(db_session, mocker):
    """A blank-country place mis-geocoded abroad that can't be resolved to Korea
    has its coordinates nulled (the Four Seasons / Deborah Beauty case)."""
    mocker.patch.object(cleanup.geocoder, "geocode", return_value=(None, None))

    pid = _add(db_session, "Four Seasons", lat=40.7580, lng=-73.9720, country=None)

    cleanup.fix_geocoding(db_session)

    db_session.expire_all()
    p = db_session.get(Place, pid)
    assert p.lat is None and p.lng is None
