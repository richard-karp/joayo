from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


# ── Claude extraction ────────────────────────────────────────────────────────

class ExtractedPlace(BaseModel):
    location_name: str
    category: Literal["eat", "see_visit", "do", "shop", "service", "guide"]
    subcategory: str
    is_place: bool          # True = has a specific physical address; False = dish, product, tip, etc.
    venue: Optional[str] = None  # if is_place=False, the place where you find/do this
    country: Optional[str] = None   # e.g. "South Korea" — used to bias geocoding
    city: Optional[str] = None      # e.g. "Seoul"
    summary: str
    labels: list[str]       # freeform descriptors only
    insider_tips: str


class ExtractionResult(BaseModel):
    places: list[ExtractedPlace]


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
    venue: Optional[str] = None
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
