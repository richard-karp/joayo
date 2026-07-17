#!/usr/bin/env python3
"""Backfill coordinates for the is_place rows that never geocoded (lat IS NULL).

Kakao keyword search indexes **Korean** names, but these rows stored romanized
English names ("3dae Samgyejangin", "Sooa Clinic") that match nothing — even with
city context. This script asks a fast LLM for each place's Korean (한글) name,
re-geocodes it through Kakao, and writes the coordinates back.

Per the "accept all, flag guesses" decision it writes **every** pin that the
existing region-conflict guard passes (a wrong *region* is worse than no pin, so
those stay NULL), and stamps low-confidence matches `needs_review=1` — the UI
shows a ⚠ "best guess" badge on those instead of silently dropping them.

After the pass it runs the retroactive dedup so a newly-geocoded row that now
shares a Kakao `geocoder_place_id` with an existing place merges correctly.

Defaults to a DRY RUN (no writes; still makes the real LLM + Kakao calls so the
preview is accurate). Pass --apply to commit — a `places.db.pre-native-<ts>`
backup is written first. --limit N processes only the first N candidates
(sample cheaply before the full run).

    python backend/backfill_native_names.py --limit 10   # sample preview
    python backend/backfill_native_names.py              # full dry run
    python backend/backfill_native_names.py --apply      # commit

Needs ANTHROPIC_API_KEY (the Korean-name lookup) and KAKAO_REST_API_KEY (geocoding).
"""
import argparse
import os
import shutil
import sys
import time

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(raise_error_if_not_found=False))
# Resolve the DB relative to this file (not the CWD) unless DB_PATH is already set,
# so the script hits backend/places.db no matter where it's launched from, while
# production's absolute DB_PATH (the Fly volume) still takes precedence.
os.environ.setdefault("DB_PATH", os.path.join(os.path.dirname(__file__), "places.db"))

from database import SessionLocal  # noqa: E402  (import after DB_PATH is resolved)
from models import Place  # noqa: E402
from routes.admin import _dedupe_places  # noqa: E402
from services import extractor, geocoder  # noqa: E402

# Plan-authorized: a fast model is plenty for a single-field local-script lookup.
_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 256
_CAPTION_SNIPPET = 500            # chars of raw_caption fed to the model for context
_CONF_THRESHOLD = geocoder.REVIEW_CONF_THRESHOLD  # native/kakao match below this → flag
_SLEEP_BETWEEN = 0.1             # gentle throttle for the Kakao/LLM APIs
_COMMIT_EVERY = 25              # persist progress every N writes (mid-run crash-safety)

_SYSTEM_PROMPT = (
    "You identify the official local-script name of a place from its romanized English "
    "name and location context. For South Korea, return the exact Korean (한글) name the "
    "place uses on maps and storefronts (e.g. 'Gyeongbokgung Palace' → '경복궁', "
    "'3dae Samgyejangin' → '3대삼계장인'). Return the name only, with no extra words. "
    "If you are not confident of this specific place's Korean name, or it is not a real "
    "physical place, return null — do NOT guess a plausible-looking name."
)

_TOOL = {
    "name": "report_native_name",
    "description": "Report the place's name in its local script (Korean 한글 for South Korea).",
    "input_schema": {
        "type": "object",
        "properties": {
            "native_name": {
                "type": ["string", "null"],
                "description": "The place's Korean (한글) name, or null if unknown / not a real place.",
            },
        },
        "required": ["native_name"],
    },
}


def _llm_native_name(client, location_name, city, neighborhood, caption) -> str | None:
    """One LLM call → the place's Korean name, or None."""
    parts = [f"Place name (romanized): {location_name}"]
    if city:
        parts.append(f"City: {city}")
    if neighborhood:
        parts.append(f"Neighborhood: {neighborhood}")
    if caption:
        parts.append(f"Context from the original post:\n{caption[:_CAPTION_SNIPPET]}")
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "report_native_name"},
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "report_native_name":
            nn = block.input.get("native_name")
            if isinstance(nn, str) and nn.strip():
                return nn.strip()
    return None


def _confidence(native_name, geo) -> tuple[int, bool]:
    """Return (fuzzy_score, needs_review).

    Confidence is how well the LLM's Korean name matches the name Kakao returned
    (token_set_ratio); below the threshold, the pin is flagged for review. Delegates
    to geocoder.review_confidence so this and the /review endpoint share one rule.

    (A neighborhood-in-address corroboration was considered but dropped: our stored
    neighborhoods are romanized — "Seocho" — while Kakao addresses are Korean —
    "서초구" — so a substring check never matches for the real KR data.)
    """
    return geocoder.review_confidence(native_name, geo.canonical_name)


def _backup_db() -> str:
    db_path = os.environ["DB_PATH"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{db_path}.pre-native-{ts}"
    shutil.copy2(db_path, dst)
    return dst


def run(apply: bool, limit: int | None) -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ERROR: ANTHROPIC_API_KEY is not set — the Korean-name lookup needs it.")
    if not os.getenv("KAKAO_REST_API_KEY"):
        sys.exit("ERROR: KAKAO_REST_API_KEY is not set — nothing can be geocoded without it.")

    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== backfill_native_names.py [{mode}] ===\n")

    if apply:
        backup = _backup_db()
        print(f"Backup written: {backup}\n")

    client = extractor._get_client()
    db = SessionLocal()
    try:
        q = db.query(Place).filter(
            Place.is_place == True,  # noqa: E712
            Place.lat.is_(None),
        ).order_by(Place.created_at)
        candidates = q.limit(limit).all() if limit else q.all()
        print(f"{len(candidates)} candidate row(s) with no coordinates.\n")

        counts = {"accepted": 0, "flagged": 0, "no_native": 0, "no_geocode": 0, "errors": 0}
        for p in candidates:
            try:
                native = _llm_native_name(
                    client, p.location_name, p.city, p.neighborhood, p.raw_caption
                )
            except Exception as e:
                # The LLM call is the only step that can raise (the SDK exhausts its
                # retries on sustained rate limiting / server errors). Don't let one
                # transient failure abort the run and lose everything committed so far
                # — log it and move on, like backfill_dates.py does per URL.
                counts["errors"] += 1
                print(f"  ! LLM error        {(p.location_name or '')[:40]:42} {str(e)[:80]}")
                time.sleep(_SLEEP_BETWEEN)
                continue

            if not native:
                counts["no_native"] += 1
                print(f"  · no native name   {(p.location_name or '')[:40]:42}")
                time.sleep(_SLEEP_BETWEEN)
                continue

            geo = geocoder.geocode_full(
                p.location_name, country="South Korea",
                expected_city=p.city, native_name=native,
            )
            if geo.lat is None:
                # No Kakao match, or the region-conflict guard rejected a wrong-region hit.
                counts["no_geocode"] += 1
                print(f"  · no geocode       {(p.location_name or '')[:40]:42} "
                      f"native={native!r}")
                time.sleep(_SLEEP_BETWEEN)
                continue

            score, needs_review = _confidence(native, geo)
            counts["flagged" if needs_review else "accepted"] += 1
            flag = " ⚠ best-guess" if needs_review else ""
            print(f"  {'⚠' if needs_review else '✓'} {(p.location_name or '')[:40]:42} "
                  f"native={native!r} → {geo.canonical_name!r} "
                  f"({geo.lat:.4f},{geo.lng:.4f}) score={score}{flag}")

            p.lat = geo.lat
            p.lng = geo.lng
            p.geocoder = geo.provider
            p.geocoder_place_id = geo.place_id
            p.native_name = native
            p.needs_review = needs_review
            if p.city is None and geo.city:
                p.city = geocoder.canonicalize_city(geo.city)
            # Persist incrementally so an interruption keeps progress; because
            # candidates are selected by lat IS NULL, a re-run resumes where we stopped.
            if apply and (counts["accepted"] + counts["flagged"]) % _COMMIT_EVERY == 0:
                db.commit()
            time.sleep(_SLEEP_BETWEEN)

        if apply:
            db.commit()
        err_note = f", {counts['errors']} errored" if counts["errors"] else ""
        print(
            f"\nRecovered {counts['accepted'] + counts['flagged']} pin(s) "
            f"({counts['accepted']} confident, {counts['flagged']} flagged needs_review); "
            f"{counts['no_native']} no native name, {counts['no_geocode']} did not geocode"
            f"{err_note}.\n"
        )

        # Merge any newly-geocoded row that now collides with an existing place.
        print("Dedup pass" + ("" if apply else " (previewing)"))
        pairs = _dedupe_places(db, commit=apply)
        for pr in pairs:
            print(f"  - {pr['merged_name'][:34]:36} merged into {pr['kept_name'][:34]!r}")
        if not pairs:
            print("  (no duplicate records to merge)")

        if not apply:
            db.rollback()
            print("\nDone (dry run — no writes). Re-run with --apply to commit.")
        else:
            print(f"\nDone. Committed the recovered pins and {len(pairs)} merge(s).")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Recover missing map pins via LLM Korean-name lookup + Kakao re-geocode."
    )
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry run).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N candidate rows (sampling).")
    args = ap.parse_args()
    run(args.apply, args.limit)
