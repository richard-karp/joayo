"""Collapse fragmented freeform labels onto canonical tags.

Freeform `labels` drift into many near-synonyms for the same concept
("media art", "contemporary art", "art gallery", …). That makes a concept
un-filterable: an exact-label filter for "art" finds 2 rows when 40+ are
art-related. This maps a curated allowlist of variant strings onto a canonical
tag, which is ADDED to the label list (originals are kept).

Design rules:
  - EXACT match on a curated allowlist (casefolded), never substring — substring
    would wrongly map negations like "not accessible to foreigners" or unrelated
    strings like "makeup artist".
  - Additive: the canonical tag is appended; the descriptive original stays.
  - Idempotent + order-preserving: re-running never duplicates a canonical.

Extend `_CANONICAL_GROUPS` to add concepts. Keep each variant set hand-curated.
"""

# canonical tag -> exact variant strings (compared casefolded)
_CANONICAL_GROUPS: dict[str, set[str]] = {
    "art": {
        "art", "arts", "artsy", "artistic",
        "public art", "media art", "media art show", "media art venue",
        "media art dining", "contemporary art", "immersive art", "digital art",
        "kinetic art", "light art", "character art", "traditional korean art",
        "tech and art fusion", "performing arts",
        "art gallery", "art museum", "art exhibition", "art installation",
        "art book cafe", "art café", "art & design", "arts complex", "arts village",
    },
}

# reverse index: variant -> canonical, built once at import
_VARIANT_TO_CANONICAL: dict[str, str] = {
    variant: canon
    for canon, variants in _CANONICAL_GROUPS.items()
    for variant in variants
}


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def canonicalize_labels(labels: list[str] | None) -> list[str]:
    """Return `labels` with a canonical tag appended for any recognized variant.

    Non-destructive (keeps originals), order-preserving, and idempotent — a
    canonical already present (in any case) is not added again.
    """
    if not labels:
        return list(labels or [])
    out = list(labels)
    present = {_norm(x) for x in out}
    for label in labels:
        canon = _VARIANT_TO_CANONICAL.get(_norm(label))
        if canon and _norm(canon) not in present:
            out.append(canon)
            present.add(_norm(canon))
    return out
