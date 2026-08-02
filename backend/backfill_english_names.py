#!/usr/bin/env python3
"""Give every place an English `location_name`, keeping the Korean in `native_name`.

joayo is an English app, but extraction sometimes returns the storefront name in
Korean ("한이식당") or bolts a romanization onto it ("풍물시장 (Pungmul Market)"), so
the list and the map end up mixing scripts. This script normalizes both cases:

  phase 1 — SPLIT (no API): the name already carries its English form inline, in a
    parenthetical or appended after the Korean. Split it: English becomes
    `location_name`, Korean moves to `native_name`. Free and deterministic.
  phase 2 — TRANSLATE (LLM): the name is pure Korean with nothing to salvage, so
    ask a fast model for the romanization or official English name. Two providers:
    Anthropic (default, one forced tool call per row) or Groq (--provider groq),
    the same free-tier escape hatch services.extractor offers.

`native_name` is only ever filled, never overwritten — it feeds Kakao geocoding and
the /review flow, so an existing value is authoritative. `normalized_name` is
recomputed from the new English name so dedup keeps matching.

Defaults to a DRY RUN (phase 2 still makes the real LLM calls so the preview is
accurate). Pass --apply to commit — a `places.db.pre-english-<ts>` backup is
written first.

    python backend/backfill_english_names.py --split-only   # free, no API key
    python backend/backfill_english_names.py --limit 10     # sample the first 10 rows
    python backend/backfill_english_names.py                # full dry run
    python backend/backfill_english_names.py --apply        # commit
    python backend/backfill_english_names.py --provider groq --apply   # free tier

Phase 2 needs ANTHROPIC_API_KEY (or GROQ_API_KEY with --provider groq);
--split-only needs no keys at all.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

import httpx
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(raise_error_if_not_found=False))
# Resolve the DB relative to this file (not the CWD) unless DB_PATH is already set,
# so the script hits backend/places.db no matter where it's launched from, while
# production's absolute DB_PATH (the Fly volume) still takes precedence.
os.environ.setdefault("DB_PATH", os.path.join(os.path.dirname(__file__), "places.db"))

from database import SessionLocal  # noqa: E402  (import after DB_PATH is resolved)
from models import Place  # noqa: E402
from routes.admin import _dedupe_places  # noqa: E402
from services import extractor  # noqa: E402
from services.text_utils import normalize_name  # noqa: E402

# Plan-authorized: a fast model is plenty for a single-field local-script lookup.
_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 256
_CAPTION_SNIPPET = 500      # chars of raw_caption fed to the model for context
_SLEEP_BETWEEN = 0.1        # gentle throttle for the LLM API
_COMMIT_EVERY = 25          # persist progress every N writes (mid-run crash-safety)

# ── Groq provider (free tier) ────────────────────────────────────────────────
# Mirrors services.extractor's Groq path: OpenAI-compatible chat API in JSON mode
# on an open model, so a bulk backfill can run without Anthropic credits.
_GROQ_MODEL = os.getenv("GROQ_NAME_MODEL", "openai/gpt-oss-120b")
_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
# Names are BATCHED here, unlike extraction. Groq's free tier bills input + the
# RESERVED max_tokens against a per-minute token cap, so one request per name would
# spend ~800 tokens to translate ~10 tokens of text — 400 names would crawl and
# could exhaust the daily allowance outright. Twenty names share one reservation.
_GROQ_BATCH = int(os.getenv("GROQ_NAME_BATCH", "20"))
_GROQ_MAX_TOKENS = int(os.getenv("GROQ_NAME_MAX_TOKENS", "1200"))
# Keeps a batch (~650 input + 1200 reserved) inside the free-tier per-minute cap.
_GROQ_MIN_INTERVAL = float(os.getenv("GROQ_NAME_MIN_INTERVAL", "14.0"))

# Hangul, kana, and Han. Matches what a reader would call "not the Latin alphabet".
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
# Cyrillic/Greek letters that look like Latin ones. A model romanizing 코다차야 once
# emitted "Kodachaя" — visually almost right, but it breaks search and sorting. These
# scripts never belong in a romanized Korean name; accented Latin (Café, Rosé) does,
# and lives in other blocks, so this stays narrow.
_CONFUSABLE_SCRIPT = re.compile(r"[Ͱ-ϿЀ-ӿ]")
# A single Latin word appended to a Korean name (no parentheses) must be at least this
# long to count as the name rather than a tacked-on category word ("BBQ", "Cafe").
_MIN_APPENDED_NAME_CHARS = 5
_PAREN = re.compile(r"^(?P<head>.*?)\s*[（(](?P<inner>[^)）]*)[)）]\s*(?P<tail>.*)$")


def has_cjk(s: str | None) -> bool:
    return bool(s and _CJK.search(s))


def _is_usable_english(s: str) -> bool:
    """A candidate is a real English name, not a stray fragment.

    Requires a 3+ letter word and no CJK. Guards against splitting a name whose only
    Latin characters are embedded in a Korean token ("제주옥탑 블랙BBQ"), where the
    Latin run is part of the Korean word rather than a translation of it.
    """
    s = s.strip()
    return bool(s) and not has_cjk(s) and bool(_LATIN_WORD.search(s))


def _is_clean_english(s) -> bool:
    """A model-returned name fit to write: non-empty, no CJK, no Cyrillic/Greek."""
    return (isinstance(s, str) and bool(s.strip())
            and not has_cjk(s) and not _CONFUSABLE_SCRIPT.search(s))


def _has_standalone_latin(s: str) -> bool:
    """True when some whitespace-separated token is a Latin word on its own.

    Latin fused into a Korean token ("BBQ연구소") is part of that word; a separate token
    ("… Saengjeonpo Mandu") is English text in its own right. Only the latter means a
    parenthetical is glossing just one piece of a longer name.
    """
    return any(
        _LATIN_WORD.search(tok) and not has_cjk(tok)
        for tok in s.split()
    )


def split_inline_english(name: str) -> tuple[str, str] | None:
    """Split a mixed-script name into (english, korean), or None if not splittable.

    Handles the three shapes extraction produces:
      "풍물시장 (Pungmul Market)"      -> ("Pungmul Market", "풍물시장")
      "Kodachaya (코다차야)"           -> ("Kodachaya", "코다차야")
      "신사형통정형외과 Sinsa Clinic"   -> ("Sinsa Clinic", "신사형통정형외과")
    """
    name = (name or "").strip()
    if not name or not has_cjk(name):
        return None

    # Parenthetical: whichever side is Latin is the English name.
    m = _PAREN.match(name)
    if m:
        inner = m.group("inner").strip()
        outer = f"{m.group('head')} {m.group('tail')}".strip()
        # One side must be wholly English and the other wholly Korean. A side carrying
        # BOTH scripts means the parenthetical annotates part of the name rather than
        # translating all of it ("구오 (Guo) Saengjeonpo Mandu"), and picking either
        # side would silently drop the rest — leave those to phase 2.
        if _is_usable_english(inner) and has_cjk(outer) and not _has_standalone_latin(outer):
            return inner, outer
        if _is_usable_english(outer) and has_cjk(inner) and not _has_standalone_latin(inner):
            return outer, inner
        return None

    # Appended: one whitespace-separated run of Korean and one of Latin, either order.
    # Splitting only at a token boundary is what keeps "블랙BBQ" intact.
    tokens = name.split()
    if len(tokens) < 2:
        return None
    korean = [t for t in tokens if has_cjk(t)]
    latin = [t for t in tokens if not has_cjk(t)]
    if len(korean) + len(latin) != len(tokens) or not korean or not latin:
        return None
    # Require the two scripts to be contiguous — "A 한 B" is interleaved, not a name
    # followed by its translation, and splitting it would scramble the word order.
    cjk_flags = [has_cjk(t) for t in tokens]
    switches = sum(1 for a, b in zip(cjk_flags, cjk_flags[1:]) if a != b)
    if switches > 1:  # e.g. "서울 Grand 호텔" — interleaved, not name-then-translation
        return None
    english, korean_part = " ".join(latin), " ".join(korean)
    if not _is_usable_english(english):
        return None
    # Parentheses explicitly mark a translation; a bare appended run does not, so it has
    # to look like a name in its own right. "솥뚜껑 BBQ" is a Korean name with an English
    # category word stuck on — promoting "BBQ" alone would delete the identifying part.
    # Measured on letters only, so hyphens and digits can't pad a category word past
    # the bar ("k-bbq" is 4 letters, not 5).
    if len(latin) < 2 and sum(c.isalpha() for c in english) < _MIN_APPENDED_NAME_CHARS:
        return None
    return english, korean_part


# Covers dishes and products as well as venues: roughly 40% of the rows needing a
# rename are is_place=false items (냉면, 유부초밥), and a venue-only prompt makes the
# model correctly answer "not a place name" and return null for every one of them.
_SYSTEM_PROMPT = (
    "You convert the Korean (한글) or Japanese name of a South Korean venue, dish, "
    "drink, or product into the name an English speaker would use. Prefer the "
    "established English form if one exists (e.g. '스타벅스' → 'Starbucks', "
    "'비빔밥' → 'Bibimbap'); otherwise return the standard Revised Romanization "
    "(e.g. '한이식당' → 'Hani Sikdang', '먹고 또 먹고' → 'Meokgo Tto Meokgo', "
    "'유부초밥' → 'Yubu Chobap'). Romanize the name — do NOT translate its meaning "
    "('냉면' is 'Naengmyeon', not 'Cold Noodles'). But when a word is a Korean "
    "transcription of a foreign word, restore that word's real spelling rather than "
    "romanizing the sound: '블랙' is 'Black' (not 'Beullaek'), '하이볼' is 'Highball', "
    "'팝핑캔디' is 'Popping Candy', '붓카케 우동' is 'Bukkake Udon'. Return the name only, with no extra "
    "words and no parentheses. Every input is a real menu item, product, or venue, so "
    "always romanize it; return null only if the input is unintelligible."
)

_TOOL = {
    "name": "report_english_name",
    "description": "Report the place's English or romanized name.",
    "input_schema": {
        "type": "object",
        "properties": {
            "english_name": {
                "type": ["string", "null"],
                "description": "Official English name or Revised Romanization; null if unknown.",
            },
        },
        "required": ["english_name"],
    },
}


def _llm_english_name(client, location_name, city, neighborhood, caption) -> str | None:
    """One LLM call → the place's English/romanized name, or None."""
    parts = [f"Place name (Korean): {location_name}"]
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
        tool_choice={"type": "tool", "name": "report_english_name"},
        messages=[{"role": "user", "content": "\n".join(parts)}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "report_english_name":
            en = block.input.get("english_name")
            if _is_clean_english(en):
                return en.strip()
    return None


_GROQ_SYSTEM_PROMPT = (
    _SYSTEM_PROMPT
    + " You will receive several places at once as JSON. Respond with JSON only, in "
      'exactly this shape: {"names": [{"i": <the item\'s i>, "english": "<name or null>"}]}. '
      "Include exactly one entry for every input item, echoing its `i` unchanged."
)


def _groq_batch(items: list[tuple[int, Place]]) -> dict[int, str | None]:
    """Translate one batch of places. Returns {index: english_or_None}.

    Raises on sustained HTTP failure so the caller can count the whole batch as errored.
    """
    api_key = os.getenv("GROQ_API_KEY")
    payload = {
        "model": _GROQ_MODEL,
        "max_tokens": _GROQ_MAX_TOKENS,
        # gpt-oss is a reasoning model; at default effort it can spend the whole
        # budget thinking and emit no JSON. Matches services.extractor's setting.
        "reasoning_effort": "low",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _GROQ_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({"items": [
                {"i": i, "name": p.location_name,
                 **({"city": p.city} if p.city else {}),
                 **({"neighborhood": p.neighborhood} if p.neighborhood else {})}
                for i, p in items
            ]}, ensure_ascii=False)},
        ],
    }

    data = None
    with httpx.Client(timeout=120) as client:
        for attempt in range(3):
            resp = client.post(_GROQ_CHAT_URL,
                               headers={"Authorization": f"Bearer {api_key}"}, json=payload)
            if resp.is_success:
                data = resp.json()
                break
            if resp.status_code in (413, 429, 500, 502, 503) and attempt < 2:
                ra = resp.headers.get("retry-after")
                time.sleep(min(int(ra), 60) if (ra and ra.isdigit()) else 15)
                continue
            raise RuntimeError(f"Groq HTTP {resp.status_code}: {resp.text[:200]}")
    if data is None:
        raise RuntimeError("Groq failed after retries")

    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        # The reply didn't fit. Halving costs one extra reservation but rescues the
        # batch; a single item that still won't fit is genuinely stuck, so let it raise.
        if len(items) > 1:
            mid = len(items) // 2
            out = _groq_batch(items[:mid])
            time.sleep(_GROQ_MIN_INTERVAL)
            out.update(_groq_batch(items[mid:]))
            return out
        raise RuntimeError(f"Groq truncated at {_GROQ_MAX_TOKENS} tokens")

    parsed = json.loads((choice.get("message") or {}).get("content") or "{}")
    wanted = {i for i, _ in items}
    out: dict[int, str | None] = {}
    for entry in parsed.get("names") or []:
        i = entry.get("i")
        # Index-matched, not order-matched: a model that reorders or invents an index
        # must not shift names onto the wrong rows. Unknown indices are dropped and
        # missing ones stay absent, which the caller counts as "no name".
        if not isinstance(i, int) or i not in wanted:
            continue
        en = entry.get("english")
        out[i] = en.strip() if _is_clean_english(en) else None
    return out


def _resolve_anthropic(places: list[Place], counts: dict):
    """Yield (place, english_or_None) — one forced tool call per row."""
    for p in places:
        try:
            english = _llm_english_name(
                _client(), p.location_name, p.city, p.neighborhood, p.raw_caption
            )
        except Exception as e:
            # The SDK exhausts its own retries, so a raise here means sustained
            # failure. Don't lose everything committed so far — log and move on,
            # matching backfill_native_names.py.
            counts["errors"] += 1
            print(f"  ! LLM error        {p.location_name[:40]:42} {str(e)[:70]}")
            time.sleep(_SLEEP_BETWEEN)
            continue
        yield p, english
        time.sleep(_SLEEP_BETWEEN)


def _resolve_groq(places: list[Place], counts: dict):
    """Yield (place, english_or_None) — batched, throttled for the free tier."""
    batches = [places[i:i + _GROQ_BATCH] for i in range(0, len(places), _GROQ_BATCH)]
    for n, batch in enumerate(batches):
        if n:
            time.sleep(_GROQ_MIN_INTERVAL)
        items = list(enumerate(batch))
        print(f"  … batch {n + 1}/{len(batches)} ({len(batch)} name(s))")
        try:
            got = _groq_batch(items)
        except Exception as e:
            counts["errors"] += len(batch)
            print(f"  ! Groq error       batch {n + 1:<3} {str(e)[:70]}")
            continue
        # A short reply is indistinguishable from "no name for these" downstream, so
        # say it out loud — otherwise a model that quietly drops half a batch looks
        # like a run where half the names were untranslatable.
        if len(got) < len(items):
            print(f"  ! partial batch    {n + 1:<3} model returned {len(got)}/{len(items)} entries")
        for i, p in items:
            yield p, got.get(i)


def _apply_rename(place: Place, english: str, korean: str | None) -> None:
    """Point location_name at the English form, preserving the Korean as native_name."""
    if korean and not place.native_name:
        place.native_name = korean
    place.location_name = english
    place.normalized_name = normalize_name(english)


def _backup_db() -> str:
    db_path = os.environ["DB_PATH"]
    ts = time.strftime("%Y%m%d-%H%M%S")
    dst = f"{db_path}.pre-english-{ts}"
    shutil.copy2(db_path, dst)
    return dst


def run(apply: bool, limit: int | None, split_only: bool, provider: str = "anthropic") -> None:
    if not split_only:
        key = "GROQ_API_KEY" if provider == "groq" else "ANTHROPIC_API_KEY"
        if not os.getenv(key):
            sys.exit(f"ERROR: {key} is not set — pass --split-only to skip the LLM phase.")

    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== backfill_english_names.py [{mode}] ===\n")

    if apply:
        print(f"Backup written: {_backup_db()}\n")

    db = SessionLocal()
    try:
        candidates = [
            p for p in db.query(Place).order_by(Place.created_at).all()
            if has_cjk(p.location_name)
        ]
        found = len(candidates)
        # --limit caps the candidate list, so it bounds EVERY write the run makes —
        # both phases and (below) the dedup pass. Slicing inside phase 2 instead would
        # make "--limit 10 --apply" still rewrite every splittable name in the table.
        if limit:
            candidates = candidates[:limit]
        print(f"{found} row(s) with a non-Latin location_name."
              + (f" Sampling the first {len(candidates)}." if limit else "") + "\n")

        # ── Phase 1: split names that already carry their English form ──────────
        print("Phase 1 — split inline English (no API)")
        split_done = 0
        remaining: list[Place] = []
        for p in candidates:
            parts = split_inline_english(p.location_name)
            if not parts:
                remaining.append(p)
                continue
            english, korean = parts
            print(f"  ✓ {p.location_name[:44]:46} → {english!r}  (native={korean!r})")
            _apply_rename(p, english, korean)
            split_done += 1
        print(f"  {split_done} split, {len(remaining)} left for translation.\n")

        if apply and split_done:
            db.commit()

        # ── Phase 2: translate what's left ──────────────────────────────────────
        counts = {"translated": 0, "no_name": 0, "errors": 0}
        if split_only:
            print(f"Phase 2 — skipped (--split-only); {len(remaining)} row(s) left untouched.\n")
        else:
            model = _GROQ_MODEL if provider == "groq" else _MODEL
            print(f"Phase 2 — translate {len(remaining)} pure-Korean name(s) via {model}")
            resolve = _resolve_groq if provider == "groq" else _resolve_anthropic
            for p, english in resolve(remaining, counts):
                if not english:
                    counts["no_name"] += 1
                    print(f"  · no english name  {p.location_name[:40]:42}")
                    continue

                print(f"  ✓ {p.location_name[:44]:46} → {english!r}")
                _apply_rename(p, english, p.location_name)
                counts["translated"] += 1
                # Persist incrementally so an interruption keeps progress; candidates are
                # selected by "has CJK", so a re-run resumes where this one stopped.
                if apply and counts["translated"] % _COMMIT_EVERY == 0:
                    db.commit()

        if apply:
            db.commit()

        err_note = f", {counts['errors']} errored" if counts["errors"] else ""
        print(
            f"\n{split_done} split + {counts['translated']} translated = "
            f"{split_done + counts['translated']} renamed; "
            f"{counts['no_name']} had no English name{err_note}."
        )

        # Renaming is precisely what surfaces name collisions: two rows for one venue
        # that differed only in script ("쇼부" vs "쇼부 (Shobu)") now share a name.
        pairs: list[dict] = []
        if limit:
            # The pass compares every row against every other, so it would merge pairs
            # this run never touched — not what a sampling run should commit.
            print("\nDedup pass — skipped (--limit); it runs over the whole table.")
        else:
            print("\nDedup pass" + ("" if apply else " (previewing)"))
            pairs = _dedupe_places(db, commit=apply)
            for pr in pairs:
                print(f"  - {pr['merged_name'][:34]:36} merged into {pr['kept_name'][:34]!r}")
            if not pairs:
                print("  (no duplicate records to merge)")

        if not apply:
            db.rollback()
            print("\nDone (dry run — no writes). Re-run with --apply to commit.")
        elif limit:
            print("\nDone. Committed the renames; re-run without --limit to dedup.")
        else:
            print(f"\nDone. Committed the renames and {len(pairs)} merge(s).")
    finally:
        db.close()


_CLIENT = None


def _client():
    """Lazily built so --split-only never needs an API key."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = extractor._get_client()
    return _CLIENT


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Rename Korean-named places to English, keeping the Korean as native_name."
    )
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry run).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N non-Latin rows, and skip the "
                         "whole-table dedup pass (sampling).")
    ap.add_argument("--split-only", action="store_true",
                    help="Run only the free mechanical split; skip the LLM phase.")
    ap.add_argument("--provider", choices=["anthropic", "groq"], default="anthropic",
                    help="Phase-2 backend: anthropic (default) or groq (free tier).")
    args = ap.parse_args()
    run(args.apply, args.limit, args.split_only, args.provider)
