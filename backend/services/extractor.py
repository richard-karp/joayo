import json
import logging
import os
import time
from typing import Optional

import anthropic
import httpx

from schemas import ExtractedPlace, ExtractionResult
from services.raw_post import RawPost

logger = logging.getLogger("extractor")

_client: anthropic.Anthropic | None = None

_DEFAULT_MODEL = "claude-sonnet-4-6"
_HARD_MODEL = "claude-opus-4-8"   # for thin/ambiguous posts needing harder judgment
_MAX_TOKENS = 8192
_MAX_COMMENTS = 12                # cap comments fed to the model

# Extraction provider: "anthropic" (default, Claude) or "groq" (free-tier open
# model via Groq's OpenAI-compatible API). Groq lets a bulk backfill run without
# Anthropic credits; prod stays on Claude unless this is explicitly set.
_PROVIDER = os.getenv("EXTRACTOR_PROVIDER", "anthropic").lower()
_GROQ_EXTRACT_MODEL = os.getenv("GROQ_EXTRACT_MODEL", "openai/gpt-oss-120b")
# Groq's free tier bills input + reserved max_tokens against the per-minute TPM
# cap, so keep the output reservation modest (extraction output is small — a few
# hundred tokens per place). 2000 keeps a typical request (~2K input + 2K
# reserved) under the 8K free-tier TPM cap for gpt-oss-120b.
_GROQ_MAX_TOKENS = int(os.getenv("GROQ_EXTRACT_MAX_TOKENS", "2000"))
_GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"


class ExtractionTruncated(Exception):
    """Raised when the model hit max_tokens before completing the tool output.

    Signals a per-post problem (skip with a warning) — distinct from an API
    outage, so it must NOT count toward the extraction pause threshold.
    """


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


_SYSTEM_PROMPT = """You are a travel content extraction assistant. Given a social media post, extract all notable items mentioned — including places, dishes, products, experiences, and services.

For each item, output:
- location_name: the item's name (specific and exact — the actual name, not a generic description)
- category: exactly one of: eat | see_visit | do | shop | service | guide
- subcategory: one of the valid subcategories for that category (see taxonomy below)
- is_place: true if the item has a specific physical address (restaurant, palace, market, clinic, spa, store); false if it is a dish, drink, product, general tip, or non-location recommendation
- venue: if is_place is false AND the item is associated with a specific place (e.g. a dish served at a particular restaurant or market), provide that place's name here; otherwise null
- summary: 1-2 sentence description of what makes this item notable
- labels: freeform descriptors (e.g. ["cash only", "hidden gem", "great for groups"]) — NOT categories
- country: the country where this item is located (e.g. "South Korea", "Japan", "United States") — infer from context; null if unclear
- city: the city or region (e.g. "Seoul", "Busan", "Jeju Island") — infer from context; null if unclear
- neighborhood: the sub-city locality or district if known (e.g. "Insadong", "Hongdae", "Seongsu") — null if unknown. Put the locality HERE; never fold it into location_name.
- native_name: the place's name in its local script when you know it (Korean 한글 for South Korea, e.g. "경복궁" for Gyeongbokgung Palace, "카페 보라" for Café Bora) — null if the item is not a physical place or you don't know the local-script name. This is used to geocode the place; romanized-only names often fail to match, so provide the native name whenever you can.
- mention_type: "primary" if this item is a genuine recommendation/subject of the post; "incidental" if it is only a passing or background mention (e.g. named in a comment aside, a place the creator merely walked past, a brand shown but not recommended).
- insider_tips: practical advice

Category taxonomy:
  see_visit → temple | palace | market_traditional | neighborhood | viewpoint | nature | museum | landmark | park | island
  eat       → restaurant | cafe | bar | street_food_stall | korean_bbq | fine_dining | bakery | dish | drink | snack
  do        → experience | class | day_trip | show | outdoor | festival | nightlife
  shop      → traditional_market | shopping_district | boutique | product | clothing | cosmetics | souvenir
  service   → medical | dental | beauty_clinic | wellness | pharmacy | spa | fitness
  guide     → licensed_guide | guide_service | tour (ONLY if explicitly an accredited/professional guide — NOT influencers)

Key rules:
- location_name MUST be a specific proper name (e.g. "Gyeongbokgung Palace", "Café Bora", "Olive Young Seongsu"). Generic descriptions like "Korean BBQ restaurant", "unnamed cafe near Insadong", "local market" are NOT valid — if you cannot identify a specific proper name, skip the item entirely.
- NEVER form a location_name by combining an area or neighborhood with a category type. All of the following are invalid and must be skipped: "Insadong Korean BBQ", "Hongdae cafe", "Myeongdong restaurant", "Insadong neighborhood Korean BBQ restaurant", "Korean BBQ restaurant (Insadong)", "Insadong neighborhood Korean BBQ". These describe a type of place in an area — not a specific named venue. If you only know the area and the type but not the actual business name, skip the item.
- Do NOT extract a country or province/region as its own item (e.g. "South Korea", "Gyeonggi Province") — that is context, not a recommendation; put it in the `country` field and skip it. For a whole CITY: if it is only the ambient setting (the creator merely happens to be there, e.g. "here in Seoul" while recommending a specific venue), do NOT extract the city as its own item — put it in `city`. BUT if the city or town is itself what's being recommended (the post is telling you to go there, e.g. a day trip to Gangneung, "Tongyeong is worth the trip"), you MAY extract it as a see_visit item (subcategory "neighborhood" if nothing fits better); set mention_type="primary" for a genuine recommendation, "incidental" for a passing/contextual mention. Never emit a bare city that is just where the creator happens to be.
- Do NOT extract TV shows, films, dramas, or other media titles as items (e.g. "Culinary Class Wars", "Squid Game") — they are not places or experiences you visit.
- Canonicalize each name to its official/romanized form. Drop area, type, and locality suffixes from the name itself (put locality in `neighborhood`): prefer "Gyeongbokgung Palace" over "Gyeongbokgung Palace (Jongno)", "Insadong" over "Insadong neighborhood". If a place is tagged, use the tagged account's real venue name.
- Emit each distinct item EXACTLY ONCE, even if it appears in both the caption and the audio transcript. Do not create duplicate entries for the same place under romanization or spelling variants — pick one canonical name.
- When extracting a neighborhood or district as a see_visit item, prefer the plain proper name ("Insadong") over adding the word "neighborhood". If you do include "neighbourhood", ensure the subcategory is "neighborhood".
- tagged_accounts are Instagram handles of featured venues. If a place is tagged, use that handle as the primary name signal (e.g. tagged_accounts includes "cafebora" → location_name is "Cafe Bora"). Tagged accounts are more reliable than vague caption references. BUT ignore personal/creator handles (the poster's own account, friends, generic influencers) — only treat handles that denote a venue/business as name signals.
- A single post can yield multiple items (e.g. a dish AND the restaurant that serves it).
- For "eat" items: a restaurant/cafe is is_place=true; a specific dish or drink is is_place=false with venue=<restaurant name> if known.
- For "shop" items: a store/market is is_place=true; a product to buy is is_place=false.
- For "service" items: a clinic/spa is is_place=true.
- Never output placeholder names like "<UNKNOWN>", "unnamed restaurant", or category descriptions as names.
- If a post mentions a neighborhood or area (e.g. Insadong, Hongdae) without naming a specific venue inside it, do NOT extract the unnamed venue — only extract the neighborhood itself if it is the subject of the post.
- Do NOT skip items that are only mentioned in a geotag or a comment — extract them, but set mention_type appropriately."""


def _mock_extract() -> list[ExtractedPlace]:
    return [
        ExtractedPlace(
            location_name="Gyeongbokgung Palace",
            category="see_visit",
            subcategory="palace",
            is_place=True,
            venue=None,
            summary="Iconic 14th-century royal palace at the heart of Seoul, built during the Joseon Dynasty.",
            labels=["historic", "must-see", "free on public holidays"],
            insider_tips="Arrive early for the changing of the guard ceremony and fewer crowds.",
        ),
        ExtractedPlace(
            location_name="Gwangjang Market",
            category="eat",
            subcategory="market_traditional",
            is_place=True,
            venue=None,
            summary="One of Seoul's oldest markets, famous for bindaetteok and mung bean porridge.",
            labels=["cash only", "open late", "local favorite"],
            insider_tips="Head upstairs to the fabric section for a quieter experience.",
        ),
        ExtractedPlace(
            location_name="Bindaetteok",
            category="eat",
            subcategory="snack",
            is_place=False,
            venue="Gwangjang Market",
            summary="Crispy mung bean pancakes, one of Gwangjang Market's most iconic street foods.",
            labels=["must-try", "cheap eats"],
            insider_tips="Get them fresh off the griddle from the stalls on the main floor.",
        ),
    ]


def _format_comments(top_comments: list[dict]) -> str | None:
    lines = []
    for c in (top_comments or [])[:_MAX_COMMENTS]:
        if isinstance(c, dict):
            text = c.get("text") or c.get("comment") or ""
        else:
            text = str(c)
        text = text.strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines) if lines else None


def _build_user_content(raw_post: RawPost, transcript: Optional[str]) -> str:
    content_parts = []
    if raw_post.caption:
        content_parts.append(f"Caption:\n{raw_post.caption}")
    if transcript:
        content_parts.append(f"Audio transcript:\n{transcript}")
    if raw_post.location_string:
        content_parts.append(f"Geotag / location: {raw_post.location_string}")
    if raw_post.tagged_accounts:
        content_parts.append(f"Tagged accounts: {', '.join(raw_post.tagged_accounts)}")
    if raw_post.hashtags:
        content_parts.append(f"Hashtags: {', '.join(raw_post.hashtags)}")
    comments = _format_comments(raw_post.top_comments)
    if comments:
        content_parts.append(f"Top comments:\n{comments}")
    return "\n\n".join(content_parts)


def _pick_model(raw_post: RawPost, transcript: Optional[str]) -> str:
    """Route thin/ambiguous posts to the stronger model for harder judgment calls."""
    caption = (raw_post.caption or "").strip()
    thin = len(caption) < 80
    side_signal = bool(raw_post.location_string or raw_post.top_comments or raw_post.tagged_accounts)
    if thin and not transcript and side_signal:
        return _HARD_MODEL
    return _DEFAULT_MODEL


_GROQ_JSON_INSTRUCTION = (
    "\n\nRespond with ONLY a single JSON object of the form "
    '{"places": [ ... ]}, where each element has exactly these fields: '
    "location_name, category, subcategory, is_place, venue, summary, labels, "
    "country, city, neighborhood, native_name, mention_type, insider_tips. "
    "Use null for unknown optional fields (venue, country, city, neighborhood, "
    'native_name), "" (not null) for summary and insider_tips when there is nothing '
    "to add, and [] for empty labels. Always include "
    "native_name in the local script (Korean 한글 for Korea) whenever you know it. "
    "Output valid JSON only — no markdown fences, no commentary."
)


def _extract_groq(user_content: str) -> list[ExtractedPlace]:
    """Extract via Groq's OpenAI-compatible chat API in JSON mode — the same
    ExtractionResult shape, run on a free-tier open model (default gpt-oss-120b).
    Used for the bulk backfill when Anthropic credits are out. JSON mode (rather
    than forced tool-calling) keeps the request small enough for the free-tier
    per-minute token cap and works with models that emit a bare JSON payload."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return _mock_extract()

    payload = {
        "model": _GROQ_EXTRACT_MODEL,
        "max_tokens": _GROQ_MAX_TOKENS,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT + _GROQ_JSON_INSTRUCTION},
            {"role": "user", "content": user_content},
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    data = None
    with httpx.Client(timeout=120) as client:
        for attempt in range(3):
            resp = client.post(_GROQ_CHAT_URL, headers=headers, json=payload)
            if resp.is_success:
                data = resp.json()
                break
            if resp.status_code in (413, 429, 500, 502, 503) and attempt < 2:
                ra = resp.headers.get("retry-after")
                time.sleep(min(int(ra), 60) if (ra and ra.isdigit()) else 15)
                continue
            logger.warning("Groq extraction HTTP %s: %s", resp.status_code, resp.text[:400])
            raise RuntimeError(f"Groq extraction failed: {resp.status_code} {resp.text[:300]}")
    if data is None:
        raise RuntimeError("Groq extraction failed after retries")

    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise ExtractionTruncated(f"Extraction output truncated at {_GROQ_MAX_TOKENS} tokens")

    content = (choice.get("message") or {}).get("content") or "{}"
    try:
        parsed = json.loads(content)
    except Exception as e:
        logger.warning("Groq extraction JSON parse failed: %s | content[:300]=%s", e, content[:300])
        raise

    if isinstance(parsed, list):
        raw_places = parsed
    elif isinstance(parsed, dict):
        raw_places = parsed.get("places")
        if not isinstance(raw_places, list):
            # Some models wrap the array under a different key — take the first list.
            raw_places = next((v for v in parsed.values() if isinstance(v, list)), [])
    else:
        raw_places = []

    # Validate each place individually so one malformed item doesn't drop the whole
    # reel, and coerce the common "null for an absent required string" case (open
    # models return null for insider_tips / summary when there's nothing to say).
    places: list[ExtractedPlace] = []
    for pd in raw_places:
        if not isinstance(pd, dict):
            continue
        for k in ("insider_tips", "summary"):
            if pd.get(k) is None:
                pd[k] = ""
        try:
            places.append(ExtractedPlace.model_validate(pd))
        except Exception as e:
            logger.warning("Groq extraction: skipping invalid place: %s | %s", e, str(pd)[:200])
    return places


def extract(raw_post: RawPost, transcript: Optional[str]) -> list[ExtractedPlace]:
    user_content = _build_user_content(raw_post, transcript)
    if not user_content:
        return []

    if _PROVIDER == "groq":
        return _extract_groq(user_content)

    if not os.getenv("ANTHROPIC_API_KEY"):
        return _mock_extract()

    tool_schema = ExtractionResult.model_json_schema()
    client = _get_client()
    model = _pick_model(raw_post, transcript)

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=_MAX_TOKENS,
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=[{
                    "name": "extract_places",
                    "description": "Extract all named places from the social media post content.",
                    "strict": True,
                    "input_schema": tool_schema,
                }],
                tool_choice={"type": "tool", "name": "extract_places"},
                messages=[{"role": "user", "content": user_content}],
            )
            break
        except anthropic.APIStatusError as e:
            if e.status_code not in (429, 529) or attempt >= 2:
                raise
            retry_after = 30
            if e.status_code == 429:
                try:
                    retry_after = int(e.response.headers.get("retry-after", 30))
                except Exception:
                    pass
            time.sleep(min(retry_after, 30))
    else:
        raise RuntimeError("Anthropic API failed after retries")

    if response.stop_reason == "max_tokens":
        raise ExtractionTruncated(
            f"Extraction output truncated at {_MAX_TOKENS} tokens for {raw_post.url}"
        )

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_places":
            result = ExtractionResult.model_validate(block.input)
            return result.places

    return []
