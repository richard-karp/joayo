#!/usr/bin/env python3
"""
C-gate: is Joayo's coordinate corroborated by an INDEPENDENT provider?

Joayo derives every coordinate from one Kakao keyword search on the venue's name
(`geocode_full` -> `_kakao_full`). KFP's `write_kakao_hours.py` measured that exact method
returning "a dental clinic at 6.8 km, a bar at 166 km", which is why its own passes anchor on
a coordinate the workbook already independently held. A Joayo row has no such anchor, so any
check that re-queries Kakao reports dist=0 on 100% of rows and proves nothing.

This asks Google — a different index, different matching — the same question, and measures how
far apart the two answers land.

⛔ PROVENANCE. The Google query is deliberately built from ONLY the fields that predate the
Kakao lookup: `native_name` / `location_name`, both written by the LLM extraction step. It
carries NO location bias and does not use `city`, which `_kakao_full` overwrites from the Kakao
result — biasing the query by anything Kakao produced is what would make this circular.

Buckets use KFP's own 150 m anchor threshold as the top band.
"""
import argparse
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
# Restricting the mask is load-bearing for cost: these four keep the call in the cheaper tier.
FIELD_MASK = "places.id,places.displayName,places.formattedAddress,places.location"
AGREE_M, CLOSE_M, MARGINAL_M = 150, 500, 2000


def load_key(env_path: str, name: str) -> str:
    """Read one key from a .env without printing it."""
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"{name} not found in {env_path}")


def haversine_m(a, b, c, d) -> float:
    R, r = 6_371_000.0, math.pi / 180
    x = (math.sin((c - a) * r / 2) ** 2
         + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(x))


def norm(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    return " ".join(re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).split())


def name_sim(a: str, b: str) -> float:
    import difflib
    if not a or not b:
        return 0.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    sa, sb = set(a.split()), set(b.split())
    if sa and sb and (sa <= sb or sb <= sa):
        ratio = max(ratio, 0.85)
    return ratio


def query_google(key: str, text: str) -> dict | None:
    payload = {"textQuery": text, "languageCode": "ko", "regionCode": "KR", "maxResultCount": 1}
    for attempt in range(3):
        try:
            r = requests.post(
                ENDPOINT,
                headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK,
                         "Content-Type": "application/json"},
                json=payload, timeout=20,
            )
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            if not r.ok:
                return {"_error": f"{r.status_code} {r.text[:120]}"}
            places = r.json().get("places") or []
            return places[0] if places else None
        except Exception as e:                      # noqa: BLE001
            if attempt == 2:
                return {"_error": repr(e)[:120]}
            time.sleep(1.0 * (attempt + 1))
    return {"_error": "retries exhausted"}


def check(key: str, row: dict) -> dict:
    # Name only — the one field that predates the Kakao lookup. No bias, no city.
    probe = (row["native_name"] or row["location_name"] or "").strip()
    out = dict(row, probe=probe, g_name=None, g_addr=None, g_lat=None, g_lng=None,
               dist_m=None, sim=None, bucket=None, error=None)
    if not probe:
        out["bucket"] = "NO_QUERY"
        return out
    g = query_google(key, f"{probe}, South Korea")
    if g is None:
        out["bucket"] = "NO_RESULT"
        return out
    if "_error" in g:
        out["bucket"], out["error"] = "ERROR", g["_error"]
        return out

    loc = g.get("location") or {}
    glat, glng = loc.get("latitude"), loc.get("longitude")
    if glat is None or glng is None:
        out["bucket"] = "NO_RESULT"
        return out

    gname = (g.get("displayName") or {}).get("text")
    d = haversine_m(row["lat"], row["lng"], glat, glng)
    sim = max(name_sim(norm(probe), norm(gname)),
              name_sim(norm(row["location_name"]), norm(gname)))
    out.update(g_name=gname, g_addr=g.get("formattedAddress"), g_lat=glat, g_lng=glng,
               dist_m=round(d), sim=round(sim, 2))

    # A far-away hit whose NAME doesn't match is Google failing to find the venue, which is not
    # evidence against Joayo. Separated out rather than counted as a disagreement.
    if d > MARGINAL_M and sim < 0.6:
        out["bucket"] = "GOOGLE_MISS"
    elif d <= AGREE_M:
        out["bucket"] = "AGREE"
    elif d <= CLOSE_M:
        out["bucket"] = "CLOSE"
    elif d <= MARGINAL_M:
        out["bucket"] = "MARGINAL"
    else:
        out["bucket"] = "DISAGREE"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--env", required=True, help=".env holding GOOGLE_PLACES_API_KEY")
    ap.add_argument("--sample", type=int, default=0, help="0 = all rows")
    ap.add_argument("--seed", type=int, default=20260819)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="gate_results.json")
    a = ap.parse_args()

    key = load_key(a.env, "GOOGLE_PLACES_API_KEY")
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("""
        SELECT id, location_name, native_name, city, neighborhood, lat, lng,
               category, subcategory, geocoder_place_id, needs_review, is_context
        FROM places
        WHERE is_place = 1 AND lat IS NOT NULL AND country = 'South Korea'
    """)]
    population = len(rows)

    if a.sample and a.sample < population:
        # Stratified by category so the `eat` figure — the promotion candidates — has its own
        # precision, rather than being whatever the overall draw happened to contain.
        rnd = random.Random(a.seed)
        eat = [r for r in rows if r["category"] == "eat"]
        rest = [r for r in rows if r["category"] != "eat"]
        half = a.sample // 2
        rows = (rnd.sample(eat, min(half, len(eat)))
                + rnd.sample(rest, min(a.sample - half, len(rest))))
        print(f"stratified sample: {len(rows)} of {population} "
              f"({sum(1 for r in rows if r['category'] == 'eat')} eat / "
              f"{sum(1 for r in rows if r['category'] != 'eat')} other)")
    else:
        print(f"full run: {len(rows)} rows")

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(lambda r: check(key, r), rows))

    json.dump(results, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    def report(subset, label):
        n = len(subset)
        if not n:
            return
        c = Counter(r["bucket"] for r in subset)
        print(f"\n── {label}  (n={n})")
        for b in ["AGREE", "CLOSE", "MARGINAL", "DISAGREE", "GOOGLE_MISS", "NO_RESULT",
                  "NO_QUERY", "ERROR"]:
            if c.get(b):
                print(f"    {b:12} {c[b]:4}  {100*c[b]/n:5.1f}%")
        judged = [r for r in subset if r["bucket"] in ("AGREE", "CLOSE", "MARGINAL", "DISAGREE")]
        if judged:
            ok = sum(1 for r in judged if r["bucket"] in ("AGREE", "CLOSE"))
            print(f"    -> of the {len(judged)} Google could judge, {ok} ({100*ok/len(judged):.1f}%) "
                  f"corroborate within {CLOSE_M} m")
            ds = sorted(r["dist_m"] for r in judged)
            print(f"    -> distance median {ds[len(ds)//2]} m, p90 {ds[int(len(ds)*0.9)]} m, "
                  f"max {ds[-1]} m")

    report(results, "ALL")
    report([r for r in results if r["category"] == "eat"], "eat only (promotion candidates)")
    report([r for r in results if r["category"] != "eat"], "non-eat")

    bad = sorted([r for r in results if r["bucket"] in ("MARGINAL", "DISAGREE")],
                 key=lambda r: -r["dist_m"])
    if bad:
        print(f"\n── worst disagreements ({len(bad)} total, showing up to 15)")
        for r in bad[:15]:
            print(f"    {r['dist_m']:>7} m  sim={r['sim']}  {r['category']:<10} "
                  f"joayo={r['location_name'][:34]!r} google={str(r['g_name'])[:34]!r}")
    print(f"\nwrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
