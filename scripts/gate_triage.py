#!/usr/bin/env python3
"""Triage `gate_results.json` — separate real disagreements from artifacts of the gate's own design.

The headline number (82% corroborate within 500 m) is a floor, not a verdict, because the gate is
deliberately handicapped: it sends the venue name with NO location bias, since the only fields that
predate the Kakao lookup are the LLM-written names. For a CHAIN that is close to unanswerable —
Google gets "CU" and returns whichever of ~18,000 branches its ranking likes. Joayo, by contrast,
runs `_kakao_full` with `expected_city` and actively prefers a branch in the right city. On chains
the gate is the weaker instrument, and its disagreements are not evidence.

Three classes are separated here:

  CHAIN        Google's name carries a Korean branch suffix — 점 / 본점 / 지점 — or Joayo's name
               carries a branch or neighbourhood token Google's answer does not. Both mean the two
               providers picked different doors of the same business. Not a mislocation.

  NAME_SPLIT   Joayo's own `location_name` and `native_name` point at different venues: the probe
               (native) matches Google's answer closely while the romanized name does not, or the
               reverse. ⛔ This is a defect in Joayo, not in the geocode — the extraction produced
               an inconsistent pair and the geocoder faithfully followed one of them.

  REVIEW       Everything left. Same name, no branch marker, kilometres apart. This is the
               population the C-gate actually exists to size.

Usage:  python3 gate_triage.py gate_results.json [--recheck --env ../korean-food-map/.env]

`--recheck` re-probes the REVIEW rows with the venue's `neighborhood` appended. That stays
independent: `neighborhood` is written by the extraction step and `_kakao_full` never writes it
back (it sets lat/lng/city/place_id/canonical_name/address and nothing else), so it is upstream of
the coordinate in exactly the way `city` is not.
"""
import argparse
import difflib
import json
import math
import os
import re
import sys
import time
import unicodedata
from collections import Counter

# Markers that a Korean POI name is naming a SUB-LOCATION of a larger named entity:
# 본점 head branch · 지점 branch office · 점 branch · 스페이스 brand outlet · 터미널 airport terminal.
#
# ⚠️ Not anchored to end-of-string. v1 used `(점)\s*$` and missed four chains, because Google
# returns decorated names — "⭐️ 화덕고깃간 방이점 | Hwadeok Gogitgan Bangi" carries the marker in
# the middle. Anchoring read as precision and was a blind spot.
#
# ⚠️ The `(?![가-힣])` guard applies ONLY to the single syllable 점, which occurs inside ordinary
# words. The multi-syllable markers are distinctive enough to match anywhere — and must, since
# "인천공항1터미널서편" continues in Hangul after 터미널.
#
# ⛔ 센터 is deliberately NOT here. It would sweep up "SM엔터테인먼트 스튜디오센터", a corporate
# campus that may genuinely be a different place from the address Joayo holds. Every error this
# classifier makes in the generous direction moves a row out of REVIEW and flatters the
# conclusion, so the ambiguous marker stays out and that row stays unexplained.
BRANCH_RE = re.compile(r"(본점|지점|스페이스|터미널)|점(?![가-힣])")
# Romanized branch/area qualifiers Joayo's own names carry ("… - Hannam", "… Balsan").
LATIN_QUAL_RE = re.compile(r"[-–—|]\s*([A-Z][a-z]+)\s*$")

# ⛔ Subcategories naming an EXTENT rather than a front door. Two points 9 km apart on Gyejoksan are
# both on the mountain; a strait, a forest and a crater are the same. Scoring these against a 150 m
# threshold measures nothing but the size of the feature, and it is why the non-eat disagreement
# rate looked comparable to eat's while being made of something completely different.
EXTENDED_SUBCATEGORIES = frozenset({
    "nature", "park", "island", "viewpoint", "neighborhood", "neighbourhood",
    "district", "shopping_district", "region", "city", "area", "outdoor", "day_trip",
    "market_traditional", "traditional_market",
})


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    return " ".join(re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).split())


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def classify(r):
    g = r.get("g_name") or ""
    jn = r.get("location_name") or ""
    nat = r.get("native_name") or ""

    # Order matters: EXTENT first, because a mountain whose Google name happens to contain 센터
    # is not a chain, and a distance threshold is meaningless for it either way.
    if (r.get("subcategory") or "") in EXTENDED_SUBCATEGORIES:
        return "EXTENT"
    if BRANCH_RE.search(g) or LATIN_QUAL_RE.search(jn):
        return "CHAIN"

    # ⚠️ Compare with whitespace COLLAPSED, not merely normalized. Korean POI names differ freely
    # on spacing — "컬러 오브 유" vs "컬러오브유", "카이센동우니도" vs "카이센동 우니도" — and v1
    # scored those as different names, manufacturing three NAME_SPLITs out of pure typography.
    flat = lambda s: norm(s).replace(" ", "")
    s_nat, s_rom = sim(flat(nat), flat(g)), sim(flat(jn), flat(g))
    if nat and max(s_nat, s_rom) >= 0.75 and min(s_nat, s_rom) < 0.35:
        # The probe is native_name when present, so a high native match with a low romanized one
        # means Joayo's OWN two name fields point at different venues, and the coordinate followed
        # the native one. That is a defect in the extraction, not in the geocode.
        return "NAME_SPLIT"
    return "REVIEW"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--env")
    a = ap.parse_args()

    rows = json.load(open(a.results, encoding="utf-8"))
    bad = [r for r in rows if r["bucket"] in ("MARGINAL", "DISAGREE")]
    judged = [r for r in rows if r["bucket"] in ("AGREE", "CLOSE", "MARGINAL", "DISAGREE")]

    print(f"sample {len(rows)}, Google could judge {len(judged)}, disagreed on {len(bad)}")
    groups = Counter()
    for r in bad:
        r["_class"] = classify(r)
        groups[r["_class"]] += 1
    for k in ("CHAIN", "EXTENT", "NAME_SPLIT", "REVIEW"):
        print(f"    {k:11} {groups[k]:3}")

    agree = sum(1 for r in judged if r["bucket"] in ("AGREE", "CLOSE"))
    # CHAIN and EXTENT are both the gate measuring the wrong thing — a branch it could not pick,
    # and a feature that has no single point. NAME_SPLIT is a real defect but in the EXTRACTION,
    # not the geocode, so it is reported separately rather than folded either way.
    adj = agree + groups["CHAIN"] + groups["EXTENT"]
    print(f"\n  raw corroboration      {agree}/{len(judged)} = {100*agree/len(judged):.1f}%")
    print(f"  adjusted               {adj}/{len(judged)} = {100*adj/len(judged):.1f}%")
    print("    (chain = the gate could not pick a branch; extent = the feature has no single point)")
    print(f"  extraction defects     {groups['NAME_SPLIT']}/{len(judged)} = "
          f"{100*groups['NAME_SPLIT']/len(judged):.1f}%  (name fields disagree — fix upstream)")
    resid = groups["REVIEW"]
    print(f"  genuinely unexplained  {resid}/{len(judged)} = {100*resid/len(judged):.1f}%")

    for k in ("EXTENT", "NAME_SPLIT", "REVIEW"):
        sub = [r for r in bad if r["_class"] == k]
        if not sub:
            continue
        print(f"\n── {k} ({len(sub)})")
        for r in sorted(sub, key=lambda x: -(x["dist_m"] or 0)):
            print(f"   {r['dist_m']:>8} m  {r['category']:<10} joayo={r['location_name'][:30]!r}"
                  f" native={str(r.get('native_name'))[:16]!r} google={str(r.get('g_name'))[:30]!r}")

    if not a.recheck:
        print("\n(pass --recheck --env <path> to re-probe the REVIEW rows with their neighbourhood)")
        return 0

    # ── recheck: same query plus the LLM-written neighbourhood ────────────────────────
    import requests

    key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not key and a.env:
        for line in open(a.env, encoding="utf-8"):
            if line.strip().startswith("GOOGLE_PLACES_API_KEY="):
                key = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("GOOGLE_PLACES_API_KEY not found")

    def hav(la1, lo1, la2, lo2):
        R, r = 6_371_000.0, math.pi / 180
        x = (math.sin((la2 - la1) * r / 2) ** 2
             + math.cos(la1 * r) * math.cos(la2 * r) * math.sin((lo2 - lo1) * r / 2) ** 2)
        return R * 2 * math.asin(math.sqrt(x))

    targets = [r for r in bad if r["_class"] in ("REVIEW", "NAME_SPLIT") and r.get("neighborhood")]
    print(f"\n── recheck with neighbourhood ({len(targets)} of {len(bad)} have one)")
    improved = still = 0
    for r in targets:
        probe = (r.get("native_name") or r["location_name"]).strip()
        q = f"{probe} {r['neighborhood']}, South Korea"
        resp = requests.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={"X-Goog-Api-Key": key,
                     "X-Goog-FieldMask": "places.displayName,places.location",
                     "Content-Type": "application/json"},
            json={"textQuery": q, "languageCode": "ko", "regionCode": "KR", "maxResultCount": 1},
            timeout=20,
        )
        places = resp.json().get("places") or [] if resp.ok else []
        if not places:
            print(f"   {'—':>8}    no result   {r['location_name'][:34]!r}")
            continue
        loc = places[0]["location"]
        d = hav(r["lat"], r["lng"], loc["latitude"], loc["longitude"])
        verdict = "RESOLVED" if d <= 500 else "still off"
        improved += d <= 500
        still += d > 500
        print(f"   {round(d):>8} m  {verdict:<10} {r['location_name'][:30]!r} "
              f"+{r['neighborhood']!r} -> {places[0]['displayName']['text'][:28]!r}")
        time.sleep(0.1)
    print(f"\n   resolved by neighbourhood: {improved}   still unexplained: {still}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
