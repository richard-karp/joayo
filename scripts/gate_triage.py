#!/usr/bin/env python3
"""Triage `gate_results.json` — separate real disagreements from artifacts of the gate's own design.

The headline number (82% corroborate within 500 m) is a floor, not a verdict, because the gate is
deliberately handicapped: it sends the venue name with NO location bias, since the only fields that
predate the Kakao lookup are the LLM-written names. For a CHAIN that is close to unanswerable —
Google gets "CU" and returns whichever of ~18,000 branches its ranking likes. Joayo, by contrast,
runs `_kakao_full` with `expected_city` and actively prefers a branch in the right city. On chains
the gate is the weaker instrument, and its disagreements are not evidence.

Five classes, and only two of them are ever folded into the adjusted rate:

  CHAIN        The provider named a different BRANCH of the same business. Requires the base names
               to agree once the branch marker is stripped. Folded in — the gate could not pick a
               branch, Joayo could (`_kakao_full` prefers a hit in `expected_city`).

  EXTENT       The venue is a mountain, forest, strait or district — a feature with no single
               point. Two coordinates 9 km apart on Gyejoksan are both on Gyejoksan. Requires the
               names to agree. Folded in.

  NAME_CHECK   The provider agrees with `native_name`, but `location_name` does not plausibly
               transliterate it. May be a genuine mismatch (Tachibana / 바위파스타바 are different
               restaurants) or an innocent loanword (컬러 오브 유 for "Color of You"). A list for
               human eyes. Folded into NOTHING.

  NO_ANCHOR    No `native_name`, and the provider answered in Hangul with no romanization that
               anchors it. Identity is not merely unproven, it is UNPROVABLE by this instrument.
               Excluded from the denominator rather than counted either way. This is the same
               blindness `review_confidence()` has, and it covers 286 of the corpus's 908 rows.

  REVIEW       Everything left — names agree, no excuse applies, kilometres apart. The population
               the C-gate exists to size.

⛔ THE GOVERNING RULE: an excusing class may excuse the DISTANCE, never the IDENTITY. Every error
this classifier makes in the generous direction moves a row out of REVIEW and flatters the
conclusion, so each fold requires the names to match first.

Usage:  python3 gate_triage.py gate_results.json [--recheck --env ../korean-food-map/.env]

`--recheck` re-probes the still-open classes with the venue's `neighborhood` appended. That stays
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


def names_match(a, b, threshold=0.75):
    """Do two venue names refer to the same venue?

    Edit distance alone is not enough: a provider routinely APPENDS a qualifier that Joayo omits —
    성수 against 성수동2가, 마티나 라운지 against 마티나 라운지 인천공항1터미널서편. Those score
    0.57 and 0.64 and are plainly the same place.

    ⛔ PREFIX containment, not substring. A qualifier is appended in Korean POI naming, never
    prepended, so requiring a prefix keeps the tolerance tight. Substring would let any short name
    match anywhere inside a long unrelated one — and every loosening here moves rows OUT of REVIEW,
    which is the direction that flatters the conclusion.
    """
    fa, fb = norm(a).replace(" ", ""), norm(b).replace(" ", "")
    if not fa or not fb:
        return False
    if len(min(fa, fb, key=len)) >= 2 and (fa.startswith(fb) or fb.startswith(fa)):
        return True
    return difflib.SequenceMatcher(None, fa, fb).ratio() >= threshold


HANGUL_RE = re.compile(r"[가-힣]")
NAME_AGREE = 0.75

# ── Revised Romanization, approximate ─────────────────────────────────────────────────
# Enough to answer one question: does `location_name` plausibly transliterate `native_name`?
# No assimilation rules, no vowel-harmony exceptions — those change a letter or two and this is
# compared with a fuzzy matcher, not an equality test.
#
# ⛔ Needed because comparing a romanized string to a Hangul one with difflib is not a weak test,
# it is a MEANINGLESS one: the character sets are disjoint, so the ratio is ~0 for a correct pair
# and ~0 for a wrong one. v3 used exactly that comparison as its NAME_SPLIT trigger and therefore
# flagged every row whose native name matched the provider — the majority of correct rows.
_INI = ["g","kk","n","d","tt","r","m","b","pp","s","ss","","j","jj","ch","k","t","p","h"]
_MED = ["a","ae","ya","yae","eo","e","yeo","ye","o","wa","wae","oe","yo","u","wo","we","wi",
        "yu","eu","ui","i"]
_FIN = ["","k","k","k","n","n","n","t","l","k","m","p","t","t","p","l","m","p","p","t","t",
        "ng","t","t","k","t","p","t"]


def romanize(s: str) -> str:
    """Hangul -> approximate Revised Romanization. Non-Hangul passes through."""
    out = []
    for ch in s or "":
        c = ord(ch) - 0xAC00
        if 0 <= c < 11172:
            out.append(_INI[c // 588] + _MED[(c % 588) // 28] + _FIN[c % 28])
        else:
            out.append(ch)
    return "".join(out)


def transliterates(latin: str, hangul: str) -> bool:
    """Is `latin` a plausible transliteration of `hangul`?

    Containment-tolerant in both directions, because one side routinely carries a qualifier the
    other omits — "Bijarim Forest" against 비자림 ("bijarim"), "Sihyunhada Photo Studio" against
    시현하다 ("sihyeonhada").

    ⚠️ Returns False for LOANWORD names: 컬러 오브 유 romanizes to "keolreoobeuyu", which is
    correct Korean for "Color of You" and matches nothing. That is why the class this feeds is
    named NAME_CHECK — a row for human eyes — and is never counted as a proven defect.
    """
    a = re.sub(r"[^a-z]", "", (latin or "").lower())
    b = re.sub(r"[^a-z]", "", romanize(hangul or "").lower())
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    short = min(len(a), len(b))
    return difflib.SequenceMatcher(None, a[:short], b[:short]).ratio() >= 0.6


def classify(r):
    """Classify a disagreement.

    ⛔ THE RULE THAT GOVERNS THIS FUNCTION, learned the hard way in v2:
    an excusing class may excuse the DISTANCE, never the IDENTITY.

    v2 checked EXTENT first, so `Yongmasan` (용마산) resolving to 아차산 — a different mountain on
    the adjacent ridge — was filed as "the feature has no single point" and folded into the
    adjusted rate. That is exactly the generous-direction error the 센터 exclusion below refuses
    to make, reintroduced through the order of checks. A wrong mountain is a wrong answer no
    matter how large mountains are.

    So CHAIN and EXTENT now both require the names to AGREE first. Only then is the distance
    excused.
    """
    g = r.get("g_name") or ""
    jn = r.get("location_name") or ""
    nat = (r.get("native_name") or "").strip()
    sub = r.get("subcategory") or ""

    # ⚠️ Compare with whitespace COLLAPSED. Korean POI names differ freely on spacing —
    # "컬러 오브 유" vs "컬러오브유" — and v1 scored those as different venues, manufacturing
    # three NAME_SPLITs out of pure typography.
    flat = lambda s: norm(s).replace(" ", "")
    # Strip the branch marker before comparing, so "화덕고깃간 방이점" is judged against
    # "화덕고깃간" rather than being penalised for carrying the very token that identifies it.
    g_base = BRANCH_RE.sub("", g)

    nat_matches = bool(nat) and (names_match(nat, g) or names_match(nat, g_base))
    rom_matches = names_match(jn, g) or names_match(jn, g_base)

    # ── NO_ANCHOR ────────────────────────────────────────────────────────────────────────
    # No native name, and the provider answered in Hangul. A romanized string never matches a
    # Korean one by edit distance, so identity here is not merely unproven — it is UNPROVABLE by
    # this instrument. Reported on its own and NEVER folded into the adjusted rate.
    #
    # This is the same blindness `review_confidence()` has: it compares native_name to the
    # provider's canonical name, so with native_name NULL it has nothing to compare — which is
    # why 0 of the 286 no-native rows in the corpus carry a needs_review flag.
    if not nat and HANGUL_RE.search(g):
        # …unless the romanized name transliterates the provider's Hangul one, which establishes
        # identity without a stored native name and rescues rows like "Hwadeok Gogitgan"
        # against 화덕고깃간.
        if not transliterates(jn, g):
            return "NO_ANCHOR"
        return "CHAIN" if BRANCH_RE.search(g) else "REVIEW"

    names_agree = nat_matches or rom_matches

    # ── NAME_CHECK ───────────────────────────────────────────────────────────────────────
    # The provider agrees with `native_name`, but `location_name` does not plausibly transliterate
    # it — so Joayo's own two name fields may be naming different venues, with the coordinate
    # following the native one. No recheck can fix this; the question itself is malformed.
    #
    # ⚠️ Named CHECK, not SPLIT, and never counted as a proven defect: a loanword name
    # (컬러 오브 유 for "Color of You") romanizes to nothing like its Latin form and lands here
    # innocently. This class is a list for human eyes, and it is folded into nothing.
    if nat_matches and not transliterates(jn, nat):
        return "NAME_CHECK"

    if not names_agree:
        # Identity is in doubt. No class may excuse it.
        return "REVIEW"

    if sub in EXTENDED_SUBCATEGORIES:
        return "EXTENT"
    if BRANCH_RE.search(g) or LATIN_QUAL_RE.search(jn):
        return "CHAIN"
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
    if not judged:
        # Every row is NO_RESULT / GOOGLE_MISS / ERROR — the gate returned nothing to triage, and
        # every rate below divides by len(judged). This is not a hypothetical: an expired key, a
        # quota block or an outage puts every row in ERROR, and that is precisely when the tool
        # must say so rather than raise. `bad` is a subset of `judged`, so there is also nothing
        # to classify.
        print("  nothing to triage: Google could judge no rows in this run")
        return 0
    groups = Counter()
    for r in bad:
        r["_class"] = classify(r)
        groups[r["_class"]] += 1
    for k in ("CHAIN", "EXTENT", "NAME_CHECK", "NO_ANCHOR", "REVIEW"):
        print(f"    {k:11} {groups[k]:3}")

    agree = sum(1 for r in judged if r["bucket"] in ("AGREE", "CLOSE"))
    # ⛔ Only CHAIN and EXTENT are folded in, and both now require the NAMES to agree first, so
    # neither can absorb a wrong venue. NO_ANCHOR is not folded either way — its identity is
    # unprovable by this instrument, and counting an unprovable row as corroborated is how a
    # measurement turns into a wish.
    adj = agree + groups["CHAIN"] + groups["EXTENT"]
    unprov = groups["NO_ANCHOR"]
    denom_prov = len(judged) - unprov

    def ci95(k, n):
        """Wilson interval — the normal approximation is nonsense at k=3, which is where this
        number actually lives."""
        if n == 0:
            return (0.0, 0.0)
        z, p = 1.96, k / n
        d = 1 + z * z / n
        c = (p + z * z / (2 * n)) / d
        h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
        return (max(0.0, c - h) * 100, min(1.0, c + h) * 100)

    print(f"\n  raw corroboration      {agree}/{len(judged)} = {100*agree/len(judged):.1f}%")
    print(f"  adjusted               {adj}/{denom_prov} = {100*adj/max(denom_prov,1):.1f}%"
          f"   (of rows whose identity is provable)")
    print("    (chain = branch not pickable; extent = no single point — BOTH require name agreement)")
    print(f"  identity unprovable    {unprov}/{len(judged)} = {100*unprov/len(judged):.1f}%"
          f"   (no native_name vs a Hangul result)")
    nchk = groups["NAME_CHECK"]
    print(f"  name-field checks      {nchk}/{len(judged)} = {100*nchk/len(judged):.1f}%"
          f"   (romanized name may not match native — human check, folded into nothing)")
    resid = groups["REVIEW"]
    lo, hi = ci95(resid, denom_prov)
    print(f"  genuinely unexplained  {resid}/{denom_prov} = {100*resid/max(denom_prov,1):.1f}%"
          f"   95% CI {lo:.1f}–{hi:.1f}%")
    if resid <= 5:
        print(f"    ⚠ that rests on {resid} rows. The interval is what the sample supports; the "
              f"point estimate is not.")

    for k in ("EXTENT", "NAME_CHECK", "NO_ANCHOR", "REVIEW"):
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

    # ⚠️ The denominator is the CLASS-ELIGIBLE set, not every disagreement. v2 printed
    # "N of {len(bad)} have one", which read as "only 4 of 25 disagreements carry a
    # neighbourhood" when 16 of 25 do — what excluded the rest was this class filter. Reporting a
    # filter's effect as a coverage gap understates a field that is actually well populated.
    eligible = [r for r in bad if r["_class"] in ("REVIEW", "NAME_CHECK", "NO_ANCHOR")]
    targets = [r for r in eligible if r.get("neighborhood")]
    with_nb = sum(1 for r in bad if r.get("neighborhood"))
    print(f"\n── recheck with neighbourhood")
    print(f"   eligible classes: {len(eligible)} of {len(bad)} disagreements; "
          f"{len(targets)} of those carry a neighbourhood "
          f"({with_nb} of all {len(bad)} do)")
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
