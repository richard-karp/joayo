#!/usr/bin/env python3
"""Split `poi_changed` into POI-id churn and genuine relocation. No API calls.

`backfill_addresses.py` separates `poi_gone` from `poi_changed` because conflating them "would
have put a closed pub in the same bucket as a wrong pin". The same logic divides `poi_changed`
again: *same venue, new id* is Kakao reissuing an identifier and says nothing about the
coordinate, while *different venue* is the mislocation candidate.

⛔ This does NOT reimplement "are these the same venue". It imports the matcher from
`gate_triage.py`, which has a fixture suite pinning three separate generous-direction bugs —
an end-anchored branch regex, an EXTENT check ordered ahead of the name test, and a
romanized-vs-Hangul difflib comparison that was structurally incapable of discriminating.
A second hand-rolled name comparison would relearn all three.

That makes this the THIRD caller of one question — after `services/deduplicator.py` and KFP's
`normalize_v3.py` union-find. Three implementations is the argument for extracting a shared
`venue_match` module; two is the warning.

Usage:  python3 triage_backfill_flags.py address_backfill_flagged.json [--db backend/places.db]
"""
import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from gate_triage import names_match, transliterates, BRANCH_RE          # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "backend"))
try:
    # Reuse Kakao's own address -> English city mapping rather than writing a second one. It
    # already handles the 2026 Gwangju/Jeonnam merge, which a fresh implementation would miss.
    from services.geocoder import _city_from_address
except Exception:                                                        # noqa: BLE001
    _city_from_address = lambda a: None                                  # noqa: E731


def classify(row, stored_city):
    """CHURN | RELOCATED | CROSS_CITY — most severe last."""
    stored = row.get("name") or ""
    now = row.get("now_resolves_to") or ""
    if not now:
        return "CHURN"                       # nothing to compare; poi_gone is handled upstream

    # Strip the branch marker before comparing: "구오 한남점" against "Guo" is the same business
    # naming an outlet, not a different venue.
    same = names_match(stored, now) or names_match(stored, BRANCH_RE.sub("", now))
    if not same and row.get("native_name"):
        same = names_match(row["native_name"], now)
    if not same:
        same = transliterates(stored, now)

    now_city = _city_from_address(row.get("now_address") or "")
    # ⛔ A city conflict is decisive even when the names agree — a chain resolving to another
    # province is exactly the signature of a name-only lookup landing in the wrong place, and it
    # is the one class the name test cannot see.
    if stored_city and now_city and stored_city.strip().lower() != now_city.strip().lower():
        return "CROSS_CITY"
    return "CHURN" if same else "RELOCATED"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("flagged")
    ap.add_argument("--db", default="backend/places.db")
    ap.add_argument("--out", default="address_backfill_triaged.json")
    a = ap.parse_args()

    rows = json.load(open(a.flagged, encoding="utf-8"))

    cities, natives = {}, {}
    try:
        import sqlite3
        con = sqlite3.connect(a.db)
        for pid, city, nat in con.execute("SELECT id, city, native_name FROM places"):
            cities[pid], natives[pid] = city, nat
    except Exception as e:                                               # noqa: BLE001
        print(f"(no db read: {e!r} — city comparison disabled)")

    changed = [r for r in rows if r.get("reason") == "poi_changed"]
    gone = [r for r in rows if r.get("reason") == "poi_gone"]
    for r in changed:
        r["native_name"] = natives.get(r.get("id"))
        r["_class"] = classify(r, cities.get(r.get("id")))

    counts = Counter(r["_class"] for r in changed)
    print(f"poi_gone     {len(gone):3}   delisted — coordinate may be fine, venue may be closed")
    print(f"poi_changed  {len(changed):3}")
    for k in ("CHURN", "RELOCATED", "CROSS_CITY"):
        if counts[k]:
            print(f"    {k:11} {counts[k]:3}")
    real = counts["RELOCATED"] + counts["CROSS_CITY"]
    print(f"\n  POI-id churn (not evidence about the coordinate): {counts['CHURN']}")
    print(f"  genuine mislocation candidates:                   {real}")

    for k in ("CROSS_CITY", "RELOCATED"):
        sub = [r for r in changed if r["_class"] == k]
        if not sub:
            continue
        print(f"\n── {k} ({len(sub)})")
        for r in sub:
            print(f"   {str(r.get('name'))[:30]!r:34} city={cities.get(r.get('id'))!r:12} "
                  f"now={str(r.get('now_resolves_to'))[:28]!r}")
            print(f"      {str(r.get('now_address'))[:88]}")

    json.dump(changed, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
