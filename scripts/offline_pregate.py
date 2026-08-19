#!/usr/bin/env python3
"""
Offline pre-gate: how coherent are Joayo's coordinates, judged ONLY by fields the geocoder
never produced?

The live gate (geocode_gate.py) asks Google. This asks a question that needs no network, using
three channels whose provenance is upstream of the Kakao lookup:

  A. NEIGHBOURHOOD COHERENCE. `neighborhood` is written by the LLM extraction step and is never
     touched by the geocoder (`_kakao_full` writes lat/lng/city/place_id/canonical_name/address
     — not neighborhood). So for any neighbourhood used by >= 3 pins, the spread of those pins
     is an independent statement about whether the coordinates are landing where the text said.

  B. SAME-POST COHERENCE. Places sharing a source post are, in the overwhelming majority, in one
     area — that is what a single reel is. Post membership comes from the fetch step. A pin far
     from its siblings is a mislocation candidate that no geocoder self-check can surface.

  C. TASTE STEW CROSS-CHECK. For the handful of venues both corpora hold, Taste Stew's
     coordinate is independently sourced (workbook + address anchoring). Small n, but it is the
     only channel here that is a true second opinion on the same venue.

⚠️ None of these prove a coordinate right. They find coordinates that are inconsistent with
what the post said — which is the cheap half of the answer, available today.
"""
import argparse
import difflib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict


def haversine_m(a, b, c, d) -> float:
    R, r = 6_371_000.0, math.pi / 180
    x = (math.sin((c - a) * r / 2) ** 2
         + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(x))


def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    return " ".join(re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE).split())


def geo_median(pts, iters=64):
    """Weiszfeld. The median point resists a single far outlier the way a mean cannot —
    which matters here because the outlier is exactly what we are looking for.

    Points are (lng, lat) in degrees, so the longitude axis is scaled by cos(lat) before
    distances are taken: at 37.5 N a degree of longitude is 88.8 km against 111.2 km for a
    degree of latitude, and treating them as equal over-weights east-west separation by ~26%,
    pulling the centre along that axis. Small in practice — over this corpus it moves the
    centre a median of 11 m and changes one flag in 419 — but the whole point of this file is
    being an instrument worth trusting.
    """
    x = sum(p[0] for p in pts) / len(pts)
    y = sum(p[1] for p in pts) / len(pts)
    kx = math.cos(y * math.pi / 180) or 1e-9
    for _ in range(iters):
        num_x = num_y = den = 0.0
        for px, py in pts:
            d = math.hypot((px - x) * kx, py - y) or 1e-9
            num_x += px / d
            num_y += py / d
            den += 1 / d
        nx, ny = num_x / den, num_y / den
        if math.hypot(nx - x, ny - y) < 1e-9:
            break
        x, y = nx, ny
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--tastestew", required=True)
    ap.add_argument("--out", default="pregate_results.json")
    # A pin this far from its neighbourhood's median is flagged. Seoul's largest 동 are ~2 km
    # across, so 3 km is generous enough that a correct pin is never flagged for being at the
    # edge of a big district.
    ap.add_argument("--nbhd-radius-m", type=float, default=3000)
    # Posts routinely cover a whole city ("10 spots in Seoul"), so this is deliberately loose:
    # it is looking for the Busan pin in a Seoul reel, not for cross-district spread.
    ap.add_argument("--post-radius-m", type=float, default=25000)
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("""
        SELECT id, location_name, native_name, city, neighborhood, lat, lng, category,
               subcategory, source_urls, needs_review, is_context, geocoder_place_id
        FROM places WHERE is_place = 1 AND lat IS NOT NULL AND country = 'South Korea'
    """)]
    for r in rows:
        try:
            r["urls"] = json.loads(r["source_urls"] or "[]")
        except Exception:
            r["urls"] = []
    by_id = {r["id"]: r for r in rows}
    n = len(rows)
    print(f"Joayo Korean geocoded venues: {n}")
    flags = defaultdict(set)

    # ── A. neighbourhood coherence ────────────────────────────────────────────────────
    nb = defaultdict(list)
    for r in rows:
        if r["neighborhood"]:
            nb[(norm(r["neighborhood"]), norm(r["city"]))].append(r)
    tested_nb = {k: v for k, v in nb.items() if len(v) >= 3}
    covered_a = sum(len(v) for v in tested_nb.values())
    a_out = []
    for key, members in tested_nb.items():
        cx, cy = geo_median([(m["lng"], m["lat"]) for m in members])
        for m in members:
            d = haversine_m(m["lat"], m["lng"], cy, cx)
            if d > a.nbhd_radius_m:
                flags[m["id"]].add("neighborhood")
                a_out.append((round(d), key[0], m["location_name"], m["category"]))
    print("\n── A. neighbourhood coherence")
    print(f"    neighbourhoods with >=3 pins: {len(tested_nb)}, covering {covered_a} pins "
          f"({100*covered_a/n:.1f}% of the corpus)")
    print(f"    pins further than {a.nbhd_radius_m:.0f} m from their neighbourhood's median: "
          f"{len(a_out)}  ({100*len(a_out)/max(covered_a,1):.1f}% of those tested)")
    for d, k, name, cat in sorted(a_out, key=lambda x: -x[0])[:10]:
        print(f"       {d:>8} m  {k[:18]:<18} {cat:<10} {name[:40]!r}")

    # ── B. same-post coherence ────────────────────────────────────────────────────────
    post = defaultdict(list)
    for r in rows:
        for u in r["urls"]:
            post[u].append(r)
    tested_p = {k: v for k, v in post.items() if len(v) >= 3}
    covered_b = len({m["id"] for v in tested_p.values() for m in v})
    b_out = []
    for url, members in tested_p.items():
        cx, cy = geo_median([(m["lng"], m["lat"]) for m in members])
        for m in members:
            d = haversine_m(m["lat"], m["lng"], cy, cx)
            if d > a.post_radius_m:
                flags[m["id"]].add("post")
                b_out.append((round(d), len(members), m["location_name"], m["category"], url))
    print("\n── B. same-post coherence")
    print(f"    posts yielding >=3 pins: {len(tested_p)}, covering {covered_b} pins "
          f"({100*covered_b/n:.1f}%)")
    uniq_b = {x[2] for x in b_out}
    print(f"    pins further than {a.post_radius_m/1000:.0f} km from their post's median: "
          f"{len(uniq_b)} distinct venues")
    for d, k, name, cat, url in sorted(b_out, key=lambda x: -x[0])[:10]:
        print(f"       {d/1000:>7.0f} km  (post had {k:>2} pins)  {cat:<10} {name[:40]!r}")

    # ── C. Taste Stew cross-check ─────────────────────────────────────────────────────
    ts = [r for r in json.load(open(a.tastestew, encoding="utf-8")) if r.get("country") == "Korea"]

    def same(x, y):
        if not x or not y:
            return False
        if difflib.SequenceMatcher(None, x, y).ratio() >= 0.80:
            return True
        sx, sy = set(x.split()), set(y.split())
        if sx and sy and (sx <= sy or sy <= sx):
            return True
        cx, cy_ = x.replace(" ", ""), y.replace(" ", "")
        return (cx in cy_ or cy_ in cx) if min(len(cx), len(cy_)) >= 5 else False

    pairs = []
    for r in rows:
        for t in ts:
            if abs(t["lat"] - r["lat"]) > 0.05 or abs(t["lng"] - r["lng"]) > 0.05:
                continue
            if same(norm(r["location_name"]), norm(t.get("name"))) or (
                r["native_name"] and t.get("name_kr")
                and same(norm(r["native_name"]), norm(t["name_kr"]))
            ):
                pairs.append((haversine_m(r["lat"], r["lng"], t["lat"], t["lng"]),
                              r["location_name"], t.get("name")))
                break
    print("\n── C. Taste Stew cross-check (independent coordinate, same venue)")
    print(f"    matched venue pairs within 5 km: {len(pairs)}")
    if pairs:
        ds = sorted(p[0] for p in pairs)
        within = sum(1 for d in ds if d <= 150)
        print(f"    agree within 150 m: {within}/{len(ds)} ({100*within/len(ds):.0f}%)")
        print(f"    median {ds[len(ds)//2]:.0f} m, p90 {ds[int(len(ds)*0.9)]:.0f} m, max {ds[-1]:.0f} m")
        for d, jn, tn in sorted(pairs, key=lambda x: -x[0])[:8]:
            print(f"       {d:>8.0f} m  joayo={jn[:32]!r} ts={str(tn)[:32]!r}")

    # ── summary ───────────────────────────────────────────────────────────────────────
    print("\n── summary")
    print(f"    pins flagged by >=1 channel: {len(flags)} of {n} ({100*len(flags)/n:.1f}%)")
    print(f"    by channel: {dict(Counter(c for s in flags.values() for c in s))}")
    print(f"    flagged AND already self-flagged needs_review: "
          f"{sum(1 for i in flags if by_id[i]['needs_review'])}")
    print(f"    Joayo's own needs_review total: {sum(1 for r in rows if r['needs_review'])}")

    json.dump({"flagged": {k: sorted(v) for k, v in flags.items()},
               "n": n, "pairs": [[round(p[0]), p[1], p[2]] for p in pairs]},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
