"""Tests for services.label_canonicalizer."""
from services.label_canonicalizer import canonicalize_labels


def test_variant_gets_canonical_tag_added():
    out = canonicalize_labels(["media art", "immersive"])
    assert "media art" in out          # original kept
    assert "art" in out                # canonical added


def test_multiple_variants_add_canonical_once():
    out = canonicalize_labels(["contemporary art", "art gallery", "digital art"])
    assert out.count("art") == 1       # single canonical despite 3 variants


def test_idempotent():
    once = canonicalize_labels(["public art"])
    twice = canonicalize_labels(once)
    assert once == twice               # re-running adds nothing


def test_existing_canonical_not_duplicated():
    out = canonicalize_labels(["art", "media art"])
    assert out.count("art") == 1


def test_unrelated_and_lookalike_labels_untouched():
    # "makeup artist" / "nail art" contain "art" but must NOT map (exact-match only).
    out = canonicalize_labels(["makeup artist", "custom nail art", "cheap"])
    assert "art" not in out
    assert out == ["makeup artist", "custom nail art", "cheap"]


def test_empty_and_none():
    assert canonicalize_labels([]) == []
    assert canonicalize_labels(None) == []


def test_case_insensitive_match_and_dedup():
    out = canonicalize_labels(["Media Art", "ART"])   # variant + already-present canonical (diff case)
    assert sum(1 for x in out if x.lower() == "art") == 1  # "ART" already satisfies canonical
