#!/usr/bin/env python3
"""Check every recovered address against the neighbourhood the extraction claimed. No API calls.

⛔ WHY THIS IS INDEPENDENT, stated carefully because that is the whole value.

Joayo's provenance chain is: Claude writes `location_name` / `native_name` / `city` /
`neighborhood`, then Kakao keyword-search on the name returns a POI carrying a coordinate, an
address, a canonical name and a city. Everything Kakao returned is DOWNSTREAM of Kakao's choice —
so checking the coordinate against the address, or the city against the address, is Kakao
grading its own homework. `_kakao_full` overwrites `city`; that field is not usable here.

`neighborhood` is the exception. It is written by the extraction step and `_kakao_full` never
writes it back (it sets lat/lng/city/place_id/canonical_name/address, and nothing else). So
"does the address Kakao returned actually lie in the neighbourhood the post described?" is a
genuine two-source question — and it is free.

Coverage where it matters: 632 of 908 Korean geocoded rows carry a neighbourhood, INCLUDING 164
of the 286 that have no `native_name` — the population the Google name-probe is structurally
unable to judge and `review_confidence()` cannot see either.

Usage:  python3 neighborhood_gate.py backend/places.db
"""
import argparse
import json
import pathlib
import re
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gate_triage import romanize                                    # noqa: E402

# Administrative suffixes that ride along with a locality name. Stripped from BOTH sides so
# "Insadong" matches 인사동 and "Seongsu-dong" matches 성수동.
_SUFFIX_RE = re.compile(r"(dong|gu|ro|gil|myeon|eup|ri|si|gun)$", re.I)
_HANGUL_SUFFIX = re.compile(r"[동구로길면읍리시군가]$")


def keys(s: str) -> set[str]:
    """Comparable forms of a locality name: romanized, suffix-stripped, space-free."""
    out = set()
    for form in (s or "", romanize(s or "")):
        f = re.sub(r"[^0-9A-Za-z가-힣]", "", form).lower()
        if len(f) < 2:
            continue
        out.add(f)
        stripped = _SUFFIX_RE.sub("", f)
        if len(stripped) >= 2:
            out.add(stripped)
        h = _HANGUL_SUFFIX.sub("", f)
        if len(h) >= 2:
            out.add(h)
    return out


def address_forms(addr: str) -> str:
    """One searchable blob: the address plus its romanization, punctuation removed."""
    a = addr or ""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", (a + romanize(a))).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--out", default="neighborhood_gate_results.json")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    cols = {c[1] for c in con.execute("PRAGMA table_info(places)")}
    if "address" not in cols:
        sys.exit("places.address does not exist — run migrate_add_address.py and the backfill first")

    rows = [dict(r) for r in con.execute("""
        SELECT id, location_name, native_name, city, neighborhood, address, category,
               subcategory, lat, lng
        FROM places
        WHERE is_place = 1 AND lat IS NOT NULL AND country = 'South Korea'
          AND address IS NOT NULL AND address != ''
          AND neighborhood IS NOT NULL AND neighborhood != ''
    """)]
    if not rows:
        sys.exit("no rows carry both an address and a neighbourhood — has the backfill run?")

    agree, disagree = [], []
    for r in rows:
        blob = address_forms(r["address"])
        if any(k in blob for k in keys(r["neighborhood"])):
            agree.append(r)
        else:
            disagree.append(r)

    n = len(rows)
    print(f"rows with BOTH an address and a neighbourhood: {n}")
    print(f"  address contains the claimed neighbourhood: {len(agree)}  ({100*len(agree)/n:.1f}%)")
    print(f"  it does not:                                {len(disagree)}  ({100*len(disagree)/n:.1f}%)")

    # The subset the Google name-probe cannot judge at all.
    blind = [r for r in rows if not (r["native_name"] or "").strip()]
    blind_bad = [r for r in disagree if not (r["native_name"] or "").strip()]
    if blind:
        print(f"\n  of the {len(blind)} rows with NO native_name (unprovable by the name gate):")
        print(f"    disagree here: {len(blind_bad)}  ({100*len(blind_bad)/len(blind):.1f}%)")
        print("    ⚠️ this is the only instrument that can see that population at all")

    print(f"\n  disagreements by category: {dict(Counter(r['category'] for r in disagree))}")

    print(f"\n── mismatches (up to 25)")
    for r in disagree[:25]:
        print(f"   {str(r['location_name'])[:30]!r:34} claimed={r['neighborhood']!r:18} "
              f"city={str(r['city'])[:10]!r}")
        print(f"      {str(r['address'])[:86]}")

    json.dump({"checked": n, "agree": len(agree),
               "disagree": [{k: r[k] for k in ("id", "location_name", "native_name", "city",
                                               "neighborhood", "address", "category",
                                               "lat", "lng")} for r in disagree]},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwrote {a.out}")

    print("\n⚠️ What a mismatch does and does not mean. It means the post's locality and the "
          "\n   POI's address disagree — which can be a mislocation, OR the extraction guessing a "
          "\n   neighbourhood, OR a legitimate address that names a different administrative unit "
          "\n   than the colloquial area (Hannam-dong addresses often read Yongsan-gu). Treat this "
          "\n   as a queue for ReviewPanel, not a verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
