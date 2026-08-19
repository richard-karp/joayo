#!/usr/bin/env python3
"""Validate a place-record payload, and convert either existing corpus into one.

Two jobs, deliberately in one file so the adapters and the rules cannot drift apart:

  --from-tastestew <restaurants.json>   adapt Taste Stew's array
  --from-joayo <places.db>              adapt Joayo's SQLite
  <payload.json>                        validate an already-adapted payload

The JSON Schema checks shape. The rules below check the things a schema cannot express — and those
are the ones with scar tissue behind them, so they are the point of this file rather than an extra.

Usage:
    python3 validate_places.py --from-tastestew ../korean-food-map/public/data/restaurants.json
    python3 validate_places.py --from-joayo backend/places.db --out places.json
    python3 validate_places.py places.json
"""
import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Resolved from this file, not the working directory: the schema lives beside the contract
# in docs/, and the script is normally run from the repo root.
SCHEMA = Path(__file__).resolve().parent.parent / "docs" / "place-record.schema.json"
KNOWN_CATEGORIES = {"eat", "see", "do", "shop", "service"}

# Taste Stew's Country union -> ISO 3166-1 alpha-2, and the IANA zone its hours are expressed in.
# ⛔ The zone is the whole reason this mapping is not just a two-entry dict: `kstNow()` hardcodes
# UTC+9, so today the timezone is an unstated assumption. Here it becomes data.
COUNTRY = {"Korea": ("KR", "Asia/Seoul"), "Japan": ("JP", "Asia/Tokyo"),
           "South Korea": ("KR", "Asia/Seoul")}
JOAYO_CATEGORY = {"eat": "eat", "see_visit": "see", "do": "do", "shop": "shop",
                  "service": "service"}   # `guide` deliberately absent — a person is not a place


# ── adapters ──────────────────────────────────────────────────────────────────────────

def from_tastestew(path):
    out = []
    for r in json.load(open(path, encoding="utf-8")):
        iso, tz = COUNTRY.get(r.get("country"), (None, None))
        p = {
            "id": f"ts_{r['id']}",
            "name": r["name"],
            "lat": r["lat"], "lng": r["lng"],
            "category": "eat",
            "source": {"kind": "curated", "name": r["group"]},
            "location_confidence": "poi_id" if r.get("place_id") else "address",
        }
        if iso:
            p["country"] = iso
        for src, dst in (("name_kr", "name_local"), ("address", "address"), ("city", "city"),
                         ("cuisine", "cuisine"), ("sub", "subcategory"), ("notes", "summary")):
            v = r.get(src)
            if v:
                p[dst] = str(v)
        if r.get("disp_val") is not None:
            p["rating"] = {"value": r["disp_val"], "scale": r.get("disp_scale") or "none"}
            if r.get("disp_src"):
                p["rating"]["source"] = r["disp_src"]
        if r.get("price_tier"):
            p["price_tier"] = r["price_tier"]
        if r.get("stars"):
            p["awards"] = [{"body": "michelin", "kind": "star", "value": r["stars"]}]
        elif r.get("cluster_star_label") in ("Bib Gourmand", "Selected"):
            p["awards"] = [{"body": "michelin", "kind": r["cluster_star_label"].lower()}]

        hours = {}
        if r.get("open_days"):
            hours["days"] = r["open_days"]
        if r.get("hours_by_day"):
            hours["by_day"] = r["hours_by_day"]
        if r.get("hours_text"):
            hours["text"] = r["hours_text"]
        if r.get("irregular"):
            hours["irregular"] = True
        if hours:
            p["hours"] = hours
            if tz:
                p["tz"] = tz                    # the rule that makes open-now portable
        if r.get("place_id"):
            p["refs"] = {"google_place_id": r["place_id"]}
        out.append(p)
    return out


def from_joayo(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    has_addr = "address" in {c[1] for c in con.execute("PRAGMA table_info(places)")}
    rows = con.execute("SELECT * FROM places WHERE is_place = 1").fetchall()
    out = []
    for r in rows:
        cat = JOAYO_CATEGORY.get(r["category"])
        if cat is None:
            continue                            # `guide` and anything unmapped are not places
        iso, tz = COUNTRY.get(r["country"], (None, None))
        addr = r["address"] if has_addr else None
        p = {
            "id": f"jy_{r['id']}",
            "name": r["location_name"],
            "category": cat,
            "source": {
                "kind": "social",
                "urls": json.loads(r["source_urls"] or "[]"),
            },
            # ⛔ The weaker of the two channels, deliberately. The coordinate came from a Kakao
            # KEYWORD search on the name; the POI id is what that search returned, not an
            # independent confirmation of it. It may be raised to `address` only once the address
            # has been geocoded separately and agreed.
            "location_confidence": "name_search" if r["geocoder_place_id"] else "none",
        }
        if r["primary_author"]:
            p["source"]["name"] = r["primary_author"]
            p["source"]["contributors"] = [
                {"handle": r["primary_author"], "platform": r["platform"] or "unknown"}
            ]
        if r["lat"] is not None and r["lng"] is not None:
            p["lat"], p["lng"] = r["lat"], r["lng"]
        if iso:
            p["country"] = iso
        if tz:
            p["tz"] = tz
        for src, dst in (("native_name", "name_local"), ("city", "city"),
                         ("neighborhood", "neighborhood"), ("subcategory", "subcategory"),
                         ("summary", "summary"), ("insider_tips", "tips")):
            if r[src]:
                p[dst] = r[src]
        if addr:
            p["address"] = addr
        labels = json.loads(r["labels"] or "[]")
        if labels:
            p["tags"] = labels
        if r["geocoder_place_id"]:
            p["refs"] = {"kakao_poi_id": r["geocoder_place_id"]}
        if r["needs_review"]:
            p["needs_review"] = True
        if r["is_context"]:
            p["is_context"] = True
        out.append(p)
    return out


# ── the rules a schema cannot express ─────────────────────────────────────────────────

def extra_rules(payload):
    errs, warns = [], []
    places = payload["places"]

    seen = {}
    for i, p in enumerate(places):
        where = f"[{i}] {p.get('id')} {str(p.get('name'))[:28]!r}"

        if p["id"] in seen:
            errs.append(f"{where}: duplicate id (also at [{seen[p['id']]}])")
        seen[p["id"]] = i

        # lat/lng are paired or absent — a half-located record silently plots at the equator
        if ("lat" in p) != ("lng" in p):
            errs.append(f"{where}: lat and lng must both be present or both absent")

        # hours without a timezone is the kstNow bug, promoted to a contract violation
        if "hours" in p and "tz" not in p:
            errs.append(f"{where}: has hours but no tz — 'open now' cannot be answered")

        # absent != empty: an empty days array is a CLAIM (shut every day), not a shrug
        h = p.get("hours") or {}
        if "days" in h and not h["days"]:
            warns.append(f"{where}: hours.days is [] — that asserts 'closed every day'. "
                         f"Omit `hours` entirely if the schedule is unknown.")
        if h and not any(k in h for k in ("days", "by_day", "text")):
            errs.append(f"{where}: hours present but carries no schedule")

        # by_day intervals must be forward; overnight ends run past 1440, never backwards
        for d, ivs in enumerate(h.get("by_day") or []):
            for iv in ivs:
                if iv[1] <= iv[0]:
                    errs.append(f"{where}: hours.by_day[{d}] interval {iv} does not run forward")

        # flags are present-only; an explicit false is a different (and wrong) statement
        for flag in ("needs_review", "is_context"):
            if flag in p and p[flag] is not True:
                errs.append(f"{where}: {flag} must be omitted or true, never {p[flag]!r}")

        # a coordinate must say how it was obtained, or it cannot be audited later
        if "lat" in p and p.get("location_confidence") in (None, "none"):
            errs.append(f"{where}: has coordinates but location_confidence is {p.get('location_confidence')!r}")

        # cuisine on a non-restaurant is a category error, not a harmless extra field
        if p.get("cuisine") and p.get("category") != "eat":
            warns.append(f"{where}: cuisine on category={p.get('category')!r}")

        if p.get("category") not in KNOWN_CATEGORIES:
            warns.append(f"{where}: unknown category {p.get('category')!r} — consumers must pass it through")

        # address is authoritative; a coordinate claiming address-grade confidence needs one
        if p.get("location_confidence") == "address" and not p.get("address"):
            warns.append(f"{where}: location_confidence='address' but no address is carried")

    if payload.get("count") is not None and payload["count"] != len(places):
        errs.append(f"count says {payload['count']}, payload has {len(places)}")
    return errs, warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", nargs="?")
    ap.add_argument("--from-tastestew")
    ap.add_argument("--from-joayo")
    ap.add_argument("--out")
    ap.add_argument("--schema", default=SCHEMA)
    a = ap.parse_args()

    places = []
    if a.from_tastestew:
        places += from_tastestew(a.from_tastestew)
    if a.from_joayo:
        places += from_joayo(a.from_joayo)
    if places:
        payload = {"contract": "1.0", "count": len(places), "places": places}
    elif a.payload:
        payload = json.load(open(a.payload, encoding="utf-8"))
    else:
        sys.exit("nothing to validate")

    if a.out:
        json.dump(payload, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
        print(f"wrote {a.out} ({len(payload['places'])} places)")

    print(f"\nvalidating {len(payload['places'])} places")

    # ⛔ Both failures are non-fatal by design: the rules below are the half of this file with
    # scar tissue behind them, and losing them because a schema file moved would be the worse
    # outcome. Each says plainly that shape went unchecked rather than passing silently.
    try:
        import jsonschema
    except ImportError:
        print("  (jsonschema not installed — shape unchecked; pip install jsonschema)")
    else:
        try:
            schema = json.load(open(a.schema, encoding="utf-8"))
        except FileNotFoundError:
            print(f"  (schema not found at {a.schema} — shape unchecked)")
        else:
            v = jsonschema.Draft202012Validator(schema)
            shape = sorted(v.iter_errors(payload), key=lambda e: list(e.path))[:20]
            print(f"  schema errors: {len(shape)}" + (" (first 20)" if len(shape) == 20 else ""))
            for e in shape:
                print(f"    {'/'.join(str(x) for x in e.path)}: {e.message[:110]}")

    errs, warns = extra_rules(payload)
    print(f"  rule errors:   {len(errs)}")
    for e in errs[:20]:
        print(f"    {e}")
    print(f"  warnings:      {len(warns)}")
    for w in warns[:12]:
        print(f"    {w}")
    if len(warns) > 12:
        print(f"    … and {len(warns)-12} more")

    total = len(payload["places"])
    if not total:
        print("\n  coverage: (payload is empty)")
        return 1 if errs else 0

    print("\n  coverage:")
    for f in ("address", "hours", "tz", "rating", "price_tier", "awards", "cuisine",
              "name_local", "neighborhood", "lat"):
        n = sum(1 for p in payload["places"] if f in p)
        print(f"    {f:14} {n:5} / {total}  {100*n/total:5.1f}%")
    print(f"    {'by source':14} {dict(Counter(p['source']['kind'] for p in payload['places']))}")
    print(f"    {'by category':14} {dict(Counter(p['category'] for p in payload['places']))}")
    print(f"    {'by confidence':14} {dict(Counter(p.get('location_confidence') for p in payload['places']))}")

    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
