from uuid import uuid4

from models import Place
from routes import admin
from routes.admin import (
    _city_centroids, _dedupe_places, _median, _places_match, reconcile_cities,
)


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_median_odd_and_even():
    assert _median([3.0, 1.0, 2.0]) == 2.0            # odd → middle
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5       # even → mean of middle two


def test_city_centroids_is_outlier_robust():
    """The median centroid ignores a single far-flung member of a label."""
    places = [
        _place("Seoul", 37.57, 126.98),
        _place("Seoul", 37.56, 126.97),
        _place("Seoul", 37.55, 126.99),
        _place("Seoul", 35.18, 129.06),  # one bad Busan-ish coord
    ]
    (clat, clng) = _city_centroids(places)["Seoul"]
    # Median, not mean, so the lone Busan outlier doesn't drag the centroid south.
    assert clat > 37.0
    assert clng > 126.5


# ── reconcile_cities ──────────────────────────────────────────────────────────

def _place(city, lat, lng, name=None, country="South Korea"):
    return Place(
        id=str(uuid4()),
        location_name=name or f"{city}-{lat}",
        category="eat",
        is_place=True,
        country=country,
        city=city,
        lat=lat,
        lng=lng,
        source_urls=["https://x/" + str(uuid4())],
        platform="instagram",
    )


def _region_by_lat(lat, lng):
    """Stub for city_from_coords: south → Busan, far south → Jeju, else Seoul."""
    if lat < 34.0:
        return "Jeju"
    if lat < 36.0:
        return "Busan"
    return "Seoul"


def _seed_clusters(session):
    """A clean Seoul cluster, a clean Busan cluster, and one Seoul-labeled outlier
    that actually plots in Busan. Returns the outlier's id."""
    session.add_all([
        _place("Seoul", 37.57, 126.98),
        _place("Seoul", 37.56, 126.97),
        _place("Seoul", 37.55, 126.99),
        _place("Busan", 35.18, 129.07),
        _place("Busan", 35.19, 129.08),
    ])
    outlier = _place("Seoul", 35.18, 129.06, name="Mislabeled Cafe")  # in Busan, labeled Seoul
    session.add(outlier)
    session.commit()
    return outlier.id


def test_reconcile_relabels_outlier_to_matching_city(db_session, mocker):
    """A place labeled Seoul but plotting in Busan is relabeled to the Busan cluster;
    dry_run reports without writing, dry_run=False commits."""
    mocker.patch.object(admin, "city_from_coords", side_effect=_region_by_lat)
    outlier_id = _seed_clusters(db_session)

    # Dry run (default): reports the change but does not write it.
    res = reconcile_cities(request=None, dry_run=True, db=db_session, _=None)
    assert res["checked"] == 6
    assert res["mismatched"] == 1
    assert res["needs_review"] == []
    assert len(res["changes"]) == 1
    change = res["changes"][0]
    assert change["id"] == outlier_id
    assert change["old_city"] == "Seoul"
    assert change["new_city"] == "Busan"
    db_session.expire_all()
    assert db_session.get(Place, outlier_id).city == "Seoul"  # unchanged

    # Apply: commits the relabel.
    res2 = reconcile_cities(request=None, dry_run=False, db=db_session, _=None)
    assert len(res2["changes"]) == 1
    db_session.expire_all()
    assert db_session.get(Place, outlier_id).city == "Busan"


def test_reconcile_needs_review_when_no_matching_label(db_session, mocker):
    """An outlier whose coordinate region has no existing city label is left unchanged
    and surfaced in needs_review."""
    mocker.patch.object(admin, "city_from_coords", side_effect=_region_by_lat)
    db_session.add_all([
        _place("Seoul", 37.57, 126.98),
        _place("Seoul", 37.56, 126.97),
    ])
    # Labeled Seoul but plots in Jeju — and there is no Jeju cluster to relabel into.
    orphan = _place("Seoul", 33.45, 126.55, name="Jeju Orphan")
    db_session.add(orphan)
    db_session.commit()

    res = reconcile_cities(request=None, dry_run=False, db=db_session, _=None)
    assert res["changes"] == []
    assert len(res["needs_review"]) == 1
    assert res["needs_review"][0]["id"] == orphan.id
    db_session.expire_all()
    assert db_session.get(Place, orphan.id).city == "Seoul"  # untouched


# ── Retroactive dish dedup (_places_match / _dedupe_places) ────────────────────

def _dish(name, venue, city="Jeju"):
    return Place(
        id=str(uuid4()),
        location_name=name,
        normalized_name=name.lower(),
        category="eat",
        is_place=False,
        venue=venue,
        city=city,
        source_urls=["https://x/" + str(uuid4())],
        platform="instagram",
    )


def test_places_match_dish_exact_name_same_venue_merges():
    a = _dish("Abalone Porridge", venue="")
    b = _dish("Abalone Porridge", venue="")
    assert _places_match(a, b) is True


def test_places_match_dish_fuzzy_name_same_venue_does_not_merge():
    """Different menu items at one venue must stay distinct — for a dish the name IS the
    identity, so fuzzy (non-exact) name matching does not apply (mirrors live dedup)."""
    a = _dish("Abalone Hot Pot Rice", venue="Gowoo Seongsu")
    b = _dish("Eel Hot Pot Rice", venue="Gowoo Seongsu")
    assert _places_match(a, b) is False


def test_places_match_dish_same_name_different_venue_does_not_merge():
    a = _dish("Abalone Porridge", venue="Restaurant A")
    b = _dish("Abalone Porridge", venue="Restaurant B")
    assert _places_match(a, b) is False


def test_dedupe_places_commit_false_previews_without_writing(db_session):
    """commit=False leaves the merge pending so the caller (reconcile script dry-run)
    can preview and roll back — nothing is persisted."""
    db_session.add_all([_dish("Jeju Black Pork", venue=""),
                        _dish("Jeju Black Pork", venue="")])
    db_session.commit()

    pairs = _dedupe_places(db_session, commit=False)
    assert len(pairs) == 1

    db_session.rollback()  # discard the preview, as the script does
    assert db_session.query(Place).count() == 2  # both rows still present


def test_dedupe_places_default_commits_exact_dish_merge(db_session):
    db_session.add_all([_dish("Jeju Black Pork", venue=""),
                        _dish("Jeju Black Pork", venue=""),
                        _dish("Abalone Hot Pot Rice", venue="Gowoo Seongsu"),
                        _dish("Eel Hot Pot Rice", venue="Gowoo Seongsu")])
    db_session.commit()

    pairs = _dedupe_places(db_session)  # commit=True by default
    assert len(pairs) == 1  # the exact pair merges; the two different dishes do not

    db_session.expire_all()
    names = sorted(p.location_name for p in db_session.query(Place).all())
    assert names == ["Abalone Hot Pot Rice", "Eel Hot Pot Rice", "Jeju Black Pork"]
