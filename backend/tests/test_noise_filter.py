"""Tests for the dominance-based ambient-noise filter (services/noise_filter.py)."""
from uuid import uuid4

from models import Place
from services import noise_filter


def _place(session, name, *, city=None, country=None, neighborhood=None, subcategory=None):
    p = Place(
        id=str(uuid4()),
        location_name=name,
        city=city,
        country=country,
        neighborhood=neighborhood,
        subcategory=subcategory,
        source_urls=[f"https://example/{name}"],
        is_context=False,
    )
    session.add(p)
    return p


def _by_name(session, name):
    return session.query(Place).filter(Place.location_name == name).first()


def test_dominant_country_and_city_are_flagged(db_session):
    # 9/10 South Korea, 6/10 Seoul -> both clear the thresholds
    _place(db_session, "Seoul", city="Seoul", country="South Korea")
    _place(db_session, "South Korea", country="South Korea")
    _place(db_session, "Insadong", city="Seoul", country="South Korea", neighborhood="Insadong")
    _place(db_session, "Gyeongbokgung Palace", city="Seoul", country="South Korea")
    _place(db_session, "Gwangjang Market", city="Seoul", country="South Korea")
    _place(db_session, "Seongsu", city="Seoul", country="South Korea")
    _place(db_session, "Busan", city="Busan", country="South Korea")
    _place(db_session, "Haeundae", city="Busan", country="South Korea", neighborhood="Haeundae")
    _place(db_session, "Gangneung", city="Gangneung", country="South Korea")
    _place(db_session, "Osaka", city="Osaka", country="Japan")
    db_session.commit()

    res = noise_filter.flag_ambient_places(db_session)
    assert res["dominant_country"] == "south korea"
    assert res["dominant_city"] == "seoul"

    # ambient home base -> flagged
    assert _by_name(db_session, "Seoul").is_context is True
    assert _by_name(db_session, "South Korea").is_context is True
    # real neighborhood, lesser cities, specific venues -> kept
    assert _by_name(db_session, "Insadong").is_context is False
    assert _by_name(db_session, "Busan").is_context is False
    assert _by_name(db_session, "Gangneung").is_context is False
    assert _by_name(db_session, "Gyeongbokgung Palace").is_context is False


def test_dominant_country_canonicalized_before_matching(db_session):
    """When the stored country string is a non-canonical alias ('USA'), the derived
    dominant_country is canonicalized so a bare-country item ('United States') still
    matches and gets flagged as home_country."""
    _place(db_session, "United States", country="USA")
    _place(db_session, "Statue of Liberty", city="New York", country="USA")
    _place(db_session, "Golden Gate Bridge", city="San Francisco", country="USA")
    _place(db_session, "Griffith Observatory", city="Los Angeles", country="USA")
    _place(db_session, "Osaka", city="Osaka", country="Japan")
    db_session.commit()

    res = noise_filter.flag_ambient_places(db_session)
    assert res["dominant_country"] == "united states"   # canonicalized from "usa"
    assert _by_name(db_session, "United States").is_context is True
    assert _by_name(db_session, "Statue of Liberty").is_context is False


def test_multi_country_trip_flags_nothing(db_session):
    """The Albania case: an 'underrated countries' collection where no single country
    dominates must keep every country as a legitimate recommendation."""
    for c in ["Albania", "Bulgaria", "Georgia", "Romania"]:
        _place(db_session, c, country=c)                 # the country as its own item
        _place(db_session, f"{c} Old Town", city=f"{c} City", country=c)
    db_session.commit()

    res = noise_filter.flag_ambient_places(db_session)
    assert res["dominant_country"] is None          # nothing clears 60%
    assert res["dominant_city"] is None
    assert res["flagged"] == {"home_country": 0, "home_city": 0, "media": 0, "geography": 0}
    assert db_session.query(Place).filter(Place.is_context.is_(True)).count() == 0


def test_bare_city_guard(db_session):
    """A dominant-city name is only ambient when it's a BARE entry (no neighborhood);
    a specific place that merely sits in that city must be kept."""
    for i in range(6):
        _place(db_session, f"Seoul spot {i}", city="Seoul", country="South Korea")
    seoul = _place(db_session, "Seoul", city="Seoul", country="South Korea")          # bare -> flag
    seoul_nb = _place(db_session, "Seoul", city="Seoul", country="South Korea",
                      neighborhood="Hongdae")                                          # has nbhd -> keep
    seongsu = _place(db_session, "Seongsu", city="Seoul", country="South Korea")       # name != city -> keep
    db_session.commit()

    noise_filter.flag_ambient_places(db_session)
    db_session.refresh(seoul); db_session.refresh(seoul_nb); db_session.refresh(seongsu)
    assert seoul.is_context is True
    assert seoul_nb.is_context is False
    assert seongsu.is_context is False


def test_media_is_flagged_regardless(db_session):
    for i in range(5):
        _place(db_session, f"place {i}", city="Seoul", country="South Korea")
    _place(db_session, "Squid Game", city="Seoul", country="South Korea")
    db_session.commit()

    noise_filter.flag_ambient_places(db_session)
    assert _by_name(db_session, "Squid Game").is_context is True


def test_idempotent_self_corrects_when_dominance_shifts(db_session):
    # Start: Seoul dominates -> flagged
    for i in range(6):
        _place(db_session, f"Seoul spot {i}", city="Seoul", country="South Korea")
    _place(db_session, "Seoul", city="Seoul", country="South Korea")
    db_session.commit()
    noise_filter.flag_ambient_places(db_session)
    assert _by_name(db_session, "Seoul").is_context is True

    # Spread new places across many cities so none (incl. Seoul) clears 50%
    for city in ["Busan", "Daegu", "Incheon", "Gwangju"]:
        for i in range(5):
            _place(db_session, f"{city} spot {i}", city=city, country="South Korea")
    db_session.commit()
    res = noise_filter.flag_ambient_places(db_session)
    assert res["dominant_city"] is None                     # no city dominates now
    assert _by_name(db_session, "Seoul").is_context is False  # previously-flagged self-corrects


# --- Non-dominant geography (the "Busan" case) --------------------------------


def test_non_dominant_city_named_as_an_item_is_flagged(db_session):
    """A city that never clears the dominance threshold is still not a destination.

    "Busan" on a Seoul-heavy collection geocodes onto an area centroid and shows up
    as a pin among real venues — the dominant-city rule alone never catches it.
    """
    for i in range(8):
        _place(db_session, f"Seoul spot {i}", city="Seoul", country="South Korea")
    _place(db_session, "Busan", city="Busan", country="South Korea", subcategory="neighborhood")
    _place(db_session, "Jeju Island", city="Jeju", country="South Korea", subcategory="island")
    db_session.commit()

    res = noise_filter.flag_ambient_places(db_session)
    assert res["dominant_city"] == "seoul"                    # Busan is nowhere near dominant
    assert _by_name(db_session, "Busan").is_context is True
    # Suffix-stripped so the city label "Jeju" catches the item "Jeju Island"
    assert _by_name(db_session, "Jeju Island").is_context is True


def test_venue_sharing_a_location_name_is_kept(db_session):
    """The area-subcategory guard: a real venue may share its name with a label.

    "Seoul Forest" is a park that also leaked into one row's neighborhood column;
    without the guard it would be demoted along with the actual areas.
    """
    for i in range(5):
        _place(db_session, f"Seoul spot {i}", city="Seoul", country="South Korea")
    _place(db_session, "Seoul Forest", city="Seoul", country="South Korea",
           neighborhood="Seoul Forest", subcategory="park")
    _place(db_session, "Gwangjang Market", city="Seoul", country="South Korea",
           subcategory="market_traditional")
    db_session.commit()

    noise_filter.flag_ambient_places(db_session)
    assert _by_name(db_session, "Seoul Forest").is_context is False
    assert _by_name(db_session, "Gwangjang Market").is_context is False


def test_one_off_neighborhood_label_does_not_become_geography(db_session):
    """A neighborhood label used by a single row is extraction noise, not an area.

    Venue names leak into the neighborhood column one-off; treating them as labels
    would demote the venue of the same name.
    """
    for i in range(5):
        _place(db_session, f"Seoul spot {i}", city="Seoul", country="South Korea")
    # Only one row ever uses this as a neighborhood -> below _MIN_LABEL_USES
    _place(db_session, "Some Venue", city="Seoul", country="South Korea",
           neighborhood="Miryang Market")
    _place(db_session, "Miryang Market", city="Seoul", country="South Korea",
           subcategory="neighborhood")
    db_session.commit()

    noise_filter.flag_ambient_places(db_session)
    assert _by_name(db_session, "Miryang Market").is_context is False

    # Once a second row shares the label it is a real area and the item is demoted
    _place(db_session, "Another Venue", city="Seoul", country="South Korea",
           neighborhood="Miryang Market")
    db_session.commit()
    noise_filter.flag_ambient_places(db_session)
    assert _by_name(db_session, "Miryang Market").is_context is True


def test_label_spanning_multiple_cities_is_a_descriptor_not_an_area(db_session):
    """"Chinatown" names a kind of district many cities have, so the label describes a
    type rather than THIS collection's setting. The specific instance stays a
    destination — the city shown next to it says which one."""
    for i in range(6):
        _place(db_session, f"Seoul spot {i}", city="Seoul", country="South Korea")
    # Used as a neighborhood in two different cities
    _place(db_session, "Dumpling House", city="Incheon", country="South Korea",
           neighborhood="Chinatown")
    _place(db_session, "Bakery", city="Incheon", country="South Korea",
           neighborhood="Chinatown")
    _place(db_session, "Noodle Bar", city="New York", country="United States",
           neighborhood="Chinatown")
    _place(db_session, "Chinatown", city="Incheon", country="South Korea",
           subcategory="neighborhood")
    # A label confined to one city is still ambient geography
    _place(db_session, "Cafe A", city="Seoul", country="South Korea", neighborhood="Myeongdong")
    _place(db_session, "Cafe B", city="Seoul", country="South Korea", neighborhood="Myeongdong")
    _place(db_session, "Myeongdong", city="Seoul", country="South Korea",
           subcategory="neighborhood")
    db_session.commit()

    noise_filter.flag_ambient_places(db_session)
    assert _by_name(db_session, "Chinatown").is_context is False
    assert _by_name(db_session, "Myeongdong").is_context is True
