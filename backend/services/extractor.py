import os
import time
from typing import Optional

import anthropic

from schemas import ExtractedPlace, ExtractionResult
from services.raw_post import RawPost

_client: anthropic.Anthropic | None = None

_DEFAULT_MODEL = "claude-sonnet-4-6"
_HARD_MODEL = "claude-opus-4-8"   # for thin/ambiguous posts needing harder judgment
_MAX_TOKENS = 8192
_MAX_COMMENTS = 12                # cap comments fed to the model


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


def extract(raw_post: RawPost, transcript: Optional[str]) -> list[ExtractedPlace]:
    user_content = _build_user_content(raw_post, transcript)
    if not user_content:
        return []

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
