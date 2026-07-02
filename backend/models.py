from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, JSON, String, UniqueConstraint,
)
from database import Base


class Job(Base):
    __tablename__ = "jobs"

    id               = Column(String, primary_key=True)
    status           = Column(String, default="pending")   # pending|processing|complete|complete_with_errors
    total_urls       = Column(Integer, default=0)
    processed        = Column(Integer, default=0)
    pending_review   = Column(JSON, default=list)          # [{"url":..., "reason":"..."}]
    failed_urls      = Column(JSON, default=list)          # [{"url":..., "error":"..."}]
    updated_place_ids = Column(JSON, default=list)         # place IDs created or merged in this job
    current_url      = Column(String, nullable=True)       # URL currently being processed
    warnings         = Column(JSON, default=list)           # [{"code":..., "message":...}]
    paused_reason    = Column(String, nullable=True)         # code that triggered the pause
    remaining_posts  = Column(JSON, default=list)           # posts not yet processed when paused
    created_at       = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Place(Base):
    __tablename__ = "places"

    id                = Column(String, primary_key=True)
    created_by_job_id = Column(String, ForeignKey("jobs.id"))
    source_urls       = Column(JSON, default=list)         # all post URLs mentioning this place
    platform          = Column(String)                     # platform of primary_author's post

    # Attribution
    primary_author             = Column(String, index=True)     # display name or @handle
    primary_author_id          = Column(String, nullable=True)  # stable platform numeric ID
    primary_author_profile_url = Column(String, nullable=True)  # e.g. https://www.instagram.com/handle/
    all_authors                = Column(JSON, default=list)     # [{username, platform_id, platform, profile_url?}, ...]
    earliest_date_posted       = Column(DateTime, nullable=True)

    # Claude-extracted
    location_name = Column(String)
    category      = Column(String, index=True)             # eat|see_visit|do|shop|service|guide
    subcategory   = Column(String, index=True)
    is_place      = Column(Boolean, default=True)          # False = dish, product, tip, etc.
    venue         = Column(String, nullable=True)          # for non-place items: where to find/do this
    venue_place_id = Column(String, ForeignKey("places.id"), nullable=True)  # resolved FK to the venue Place
    country       = Column(String, nullable=True, index=True)
    city          = Column(String, nullable=True, index=True)
    summary       = Column(String)
    labels        = Column(JSON)                           # freeform descriptors ["hidden gem", ...]
    insider_tips  = Column(String)

    # Geocoded
    lat               = Column(Float, nullable=True)
    lng               = Column(Float, nullable=True)
    geocoder          = Column(String, nullable=True)             # "kakao" | "nominatim"
    geocoder_place_id = Column(String, nullable=True, index=True) # stable external POI id (strongest dedup key)

    # Matching helpers
    normalized_name = Column(String, nullable=True, index=True)   # normalized location_name for cheap matching
    neighborhood    = Column(String, nullable=True)               # sub-city locality

    # Raw source (from primary_author's post)
    raw_caption        = Column(String)
    tagged_accounts    = Column(JSON)
    transcript         = Column(String, nullable=True)
    transcript_missing = Column(Boolean, default=False)
    created_at         = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CdnUrlCache(Base):
    """Tracks CDN video URLs seen across all jobs.

    A CDN URL appearing for many unrelated posts indicates Instagram's
    content-addressed storage is returning the same video for different posts,
    which causes AssemblyAI to serve a cached transcript that doesn't match.
    """
    __tablename__ = "cdn_url_cache"

    cdn_url           = Column(String, primary_key=True)
    hit_count         = Column(Integer, default=1)
    first_seen_job_id = Column(String, ForeignKey("jobs.id"), nullable=True)
    last_seen_at      = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("place_id", "voter"),)

    id         = Column(String, primary_key=True)
    place_id   = Column(String, ForeignKey("places.id"), index=True)
    voter      = Column(String, default="default")         # "default" until multi-user auth added
    value      = Column(Integer)                           # +1 or -1
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
