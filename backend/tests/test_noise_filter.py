"""Tests for the dominance-based ambient-noise filter (services/noise_filter.py)."""
from uuid import uuid4

from models import Place
from services import noise_filter


def _place(session, name, *, city=None, country=None, neighborhood=None):
    p = Place(
        id=str(uuid4()),
        location_name=name,
        city=city,
        country=country,
        neighborhood=neighborhood,
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
    assert res["flagged"] == {"home_country": 0, "home_city": 0, "media": 0}
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
