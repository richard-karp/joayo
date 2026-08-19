#!/usr/bin/env python3
"""Recover the addresses that were fetched and discarded.

⛔ This is a RECOVERY, not a re-geocode, and the distinction is the whole design.

Each row already records the Kakao POI it resolved to (`geocoder_place_id`). This re-runs the
SAME query that produced the row — `native_name` where present, else `location_name`, exactly as
`geocode_full` chooses — and accepts the address ONLY from the document whose `id` equals the
stored POI id. If Kakao no longer returns that POI for that query, the row is left blank and
counted, never filled from whatever came back instead.

That guard is what keeps this from silently becoming a second geocoding pass with a different
answer. `Hours`-style discipline: BLANK MEANS UNKNOWN, and a drifted POI is a reported event, not
an absence.

The drift count is worth reading on its own. A row whose stored POI id no longer surfaces for its
own query is a row whose coordinate rests on a lookup that is no longer reproducible — which is
exactly the population the C-gate exists to size.

Usage:
    KAKAO_REST_API_KEY=... python3 backfill_addresses.py backend/places.db [--limit N] [--dry-run]
    # or leave the key in .env beside the repo root and pass --env path/to/.env
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import Counter

import requests

KAKAO_KEYWORD = "https://dapi.kakao.com/v2/local/search/keyword.json"
SLEEP_S = 0.12          # Kakao's published ceiling is far above this; be a good citizen anyway.
PAGE_SIZE = 10          # same as _kakao_full, so the same documents are in scope


def load_key(env_path: str | None) -> str:
    key = os.getenv("KAKAO_REST_API_KEY")
    if key:
        return key
    if env_path:
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("KAKAO_REST_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("KAKAO_REST_API_KEY not set (env or --env file)")


def lookup(key: str, query: str) -> list[dict]:
    for attempt in range(3):
        try:
            r = requests.get(
                KAKAO_KEYWORD,
                headers={"Authorization": f"KakaoAK {key}"},
                params={"query": query, "size": PAGE_SIZE},
                timeout=10,
            )
            if r.status_code == 429:
                time.sleep(1.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json().get("documents", []) or []
        except Exception:                                   # noqa: BLE001
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--env")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    key = load_key(a.env)
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row
    if "address" not in {r[1] for r in con.execute("PRAGMA table_info(places)")}:
        sys.exit("places.address does not exist — run migrate_add_address.py first")

    rows = con.execute("""
        SELECT id, location_name, native_name, geocoder_place_id
        FROM places
        WHERE geocoder = 'kakao' AND geocoder_place_id IS NOT NULL
          AND (address IS NULL OR address = '')
        ORDER BY created_at
    """).fetchall()
    if a.limit:
        rows = rows[: a.limit]
    print(f"{len(rows)} rows to recover" + (" (dry run)" if a.dry_run else ""))

    counts = Counter()
    flagged: list[dict] = []
    for i, row in enumerate(rows, 1):
        query = (row["native_name"] or row["location_name"] or "").strip()
        if not query:
            counts["no_query"] += 1
            continue
        try:
            docs = lookup(key, query)
        except Exception as e:                              # noqa: BLE001
            counts["request_failed"] += 1
            print(f"  ! {query[:36]:38} request failed: {e!r}"[:120])
            time.sleep(SLEEP_S)
            continue

        match = next((d for d in docs if d.get("id") == row["geocoder_place_id"]), None)
        if match is None:
            # ⛔ Two different failures, deliberately NOT counted together — an earlier version of
            # this script lumped them as "poi_drifted" and the distinction is the whole signal:
            #
            #   poi_gone    — Kakao returns NOTHING for this query. The listing is delisted. The
            #                 stored coordinate may be perfectly correct for a venue that has since
            #                 closed, which is a fact worth having: Joayo has no closure detection
            #                 at all, and this pass is accidentally one.
            #   poi_changed — Kakao returns results, but ours is not among them. THIS is the
            #                 mislocation candidate: the name now resolves somewhere else, so the
            #                 coordinate rests on a lookup that no longer reproduces.
            #
            # Only the second is evidence about the coordinate. Conflating them would have put a
            # closed pub in the same bucket as a wrong pin.
            if not docs:
                counts["poi_gone"] += 1
                flagged.append({"id": row["id"], "name": row["location_name"], "query": query,
                                "reason": "poi_gone", "stored_poi": row["geocoder_place_id"]})
                print(f"  · {query[:36]:38} delisted — Kakao returns nothing (venue may be closed)")
            else:
                counts["poi_changed"] += 1
                top = docs[0]
                flagged.append({"id": row["id"], "name": row["location_name"], "query": query,
                                "reason": "poi_changed", "stored_poi": row["geocoder_place_id"],
                                "now_resolves_to": top.get("place_name"),
                                "now_poi": top.get("id"),
                                "now_address": top.get("road_address_name") or top.get("address_name"),
                                "now_lat": top.get("y"), "now_lng": top.get("x")})
                print(f"  ⚠ {query[:36]:38} now resolves to {str(top.get('place_name'))[:28]!r} "
                      f"— MISLOCATION CANDIDATE")
            time.sleep(SLEEP_S)
            continue

        addr = match.get("road_address_name") or match.get("address_name")
        if not addr:
            counts["no_address"] += 1
        else:
            counts["recovered"] += 1
            if not a.dry_run:
                con.execute("UPDATE places SET address = ? WHERE id = ?", (addr, row["id"]))
        if i % 50 == 0:
            if not a.dry_run:
                con.commit()
            print(f"  … {i}/{len(rows)}  {dict(counts)}")
        time.sleep(SLEEP_S)

    if not a.dry_run:
        con.commit()

    print("\n── result")
    for k, v in counts.most_common():
        print(f"    {k:16} {v}")
    total = con.execute("SELECT COUNT(*) FROM places WHERE address IS NOT NULL AND address != ''").fetchone()[0]
    print(f"    rows now carrying an address: {total}")

    if flagged:
        with open("address_backfill_flagged.json", "w", encoding="utf-8") as f:
            json.dump(flagged, f, ensure_ascii=False, indent=1)
        gone = counts["poi_gone"]
        changed = counts["poi_changed"]
        print(f"\n  wrote address_backfill_flagged.json ({len(flagged)} rows)")
        if gone:
            print(f"  · {gone} delisted — Kakao no longer lists these at all. The coordinate may be "
                  f"correct for a venue that has CLOSED. Joayo has no closure detection; this is it.")
        if changed:
            print(f"  ⚠ {changed} now resolve elsewhere — these are the mislocation candidates. "
                  f"Each entry carries what the name resolves to today, so they can be adjudicated "
                  f"in ReviewPanel rather than guessed at.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
