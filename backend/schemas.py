from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, model_validator


# ── Claude extraction ────────────────────────────────────────────────────────

_VALID_SUBCATEGORIES: dict[str, frozenset[str]] = {
    "eat":       frozenset({"restaurant", "cafe", "bar", "street_food_stall", "korean_bbq", "fine_dining", "bakery", "dish", "drink", "snack"}),
    "see_visit": frozenset({"temple", "palace", "market_traditional", "neighborhood", "viewpoint", "nature", "museum", "landmark", "park", "island"}),
    "do":        frozenset({"experience", "class", "day_trip", "show", "outdoor", "festival", "nightlife"}),
    "shop":      frozenset({"traditional_market", "shopping_district", "boutique", "product", "clothing", "cosmetics", "souvenir"}),
    "service":   frozenset({"medical", "dental", "beauty_clinic", "wellness", "pharmacy", "spa", "fitness"}),
    "guide":     frozenset({"licensed_guide", "guide_service", "tour"}),
}


class ExtractedPlace(BaseModel):
    location_name: str
    category: Literal["eat", "see_visit", "do", "shop", "service", "guide"]
    subcategory: Optional[str] = None
    is_place: bool          # True = has a specific physical address; False = dish, product, tip, etc.
    venue: Optional[str] = None  # if is_place=False, the place where you find/do this
    country: Optional[str] = None   # e.g. "South Korea" — used to bias geocoding
    city: Optional[str] = None      # e.g. "Seoul"
    neighborhood: Optional[str] = None  # sub-city locality, e.g. "Insadong" — do NOT fold into the name
    mention_type: Optional[Literal["primary", "incidental"]] = None  # "incidental" = passing/background mention
    summary: str
    labels: list[str]       # freeform descriptors only
    insider_tips: str

    # extra="forbid" emits additionalProperties:false, required for strict tool use.
    model_config = {"extra": "forbid"}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        # Strict tool use requires every property listed in "required" and no
        # per-field "default" keyword. Optional fields stay nullable via anyOf.
        props = schema.get("properties", {})
        schema["required"] = list(props.keys())
        for prop in props.values():
            prop.pop("default", None)
        return schema

    @model_validator(mode="after")
    def _validate_subcategory(self) -> "ExtractedPlace":
        valid = _VALID_SUBCATEGORIES.get(self.category, frozenset())
        if self.subcategory and self.subcategory not in valid:
            self.subcategory = None
        return self


class ExtractionResult(BaseModel):
    places: list[ExtractedPlace]

    model_config = {"extra": "forbid"}


# ── Author ───────────────────────────────────────────────────────────────────

class Author(BaseModel):
    username: str
    platform_id: Optional[str] = None
    platform: str
    profile_url: Optional[str] = None

    model_config = {"extra": "ignore"}


# ── Place response ───────────────────────────────────────────────────────────

class PlaceResponse(BaseModel):
    id: str
    created_by_job_id: Optional[str]
    source_urls: list[str]
    platform: Optional[str]
    primary_author: Optional[str]
    primary_author_id: Optional[str]
    primary_author_profile_url: Optional[str] = None
    all_authors: list[Author]
    earliest_date_posted: Optional[datetime]
    location_name: Optional[str]
    category: Optional[str]
    subcategory: Optional[str]
    is_place: bool = True
    is_context: bool = False  # ambient home-base (dominant country/city) or media — demoted
    venue: Optional[str] = None
    venue_place_id: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    summary: Optional[str]
    labels: Optional[list[str]]
    insider_tips: Optional[str]
    lat: Optional[float]
    lng: Optional[float]
    raw_caption: Optional[str]
    tagged_accounts: Optional[list[str]]
    transcript_missing: bool
    created_at: datetime
    # Computed from Vote table
    vote_score: int = 0
    current_vote: Optional[Literal["up", "down"]] = None

    model_config = {"from_attributes": True}


# ── Job response ─────────────────────────────────────────────────────────────

class JobResponse(BaseModel):
    id: str
    status: str
    total_urls: int
    processed: int
    current_url: Optional[str] = None
    pending_review: list[dict]
    failed_urls: list[dict]
    warnings: list[dict] = []
    paused_reason: Optional[str] = None
    remaining_posts: list[dict] = []
    places: list[PlaceResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Extract request ──────────────────────────────────────────────────────────

class ExtractResponse(BaseModel):
    job_id: str


# ── Vote request ─────────────────────────────────────────────────────────────

class VoteRequest(BaseModel):
    vote: Optional[Literal["up", "down"]] = None


# ── Leaderboard ──────────────────────────────────────────────────────────────

class LeaderboardEntry(BaseModel):
    username: str
    platform_id: Optional[str]
    profile_url: Optional[str] = None
    total_score: int
    attributed_count: int
    mentioned_count: int
