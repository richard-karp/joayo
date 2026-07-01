import os
import time
from typing import Optional

import anthropic

from schemas import ExtractedPlace, ExtractionResult
from services.raw_post import RawPost

_client: anthropic.Anthropic | None = None


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
- tagged_accounts are Instagram handles of featured venues. If a place is tagged, use that handle as the primary name signal (e.g. tagged_accounts includes "cafebora" → location_name is "Cafe Bora"). Tagged accounts are more reliable than vague caption references.
- A single post can yield multiple items (e.g. a dish AND the restaurant that serves it).
- For "eat" items: a restaurant/cafe is is_place=true; a specific dish or drink is is_place=false with venue=<restaurant name> if known.
- For "shop" items: a store/market is is_place=true; a product to buy is is_place=false.
- For "service" items: a clinic/spa is is_place=true.
- Never output placeholder names like "<UNKNOWN>", "unnamed restaurant", or category descriptions as names.
- If a post mentions a neighborhood or area (e.g. Insadong, Hongdae) without naming a specific venue inside it, do NOT extract the unnamed venue — only extract the neighborhood itself if it is the subject of the post."""


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


def extract(raw_post: RawPost, transcript: Optional[str]) -> list[ExtractedPlace]:
    content_parts = []
    if raw_post.caption:
        content_parts.append(f"Caption:\n{raw_post.caption}")
    if transcript:
        content_parts.append(f"Audio transcript:\n{transcript}")
    if raw_post.tagged_accounts:
        content_parts.append(f"Tagged accounts: {', '.join(raw_post.tagged_accounts)}")
    if not content_parts:
        return []

    user_content = "\n\n".join(content_parts)

    if not os.getenv("ANTHROPIC_API_KEY"):
        return _mock_extract()

    tool_schema = ExtractionResult.model_json_schema()
    client = _get_client()

    response = None
    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                tools=[{
                    "name": "extract_places",
                    "description": "Extract all named places from the social media post content.",
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

    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_places":
            result = ExtractionResult.model_validate(block.input)
            return result.places

    return []
