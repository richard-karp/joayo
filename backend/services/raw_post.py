from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawPost:
    platform: str
    url: str
    author: str
    author_platform_id: str | None
    caption: str
    hashtags: list[str]
    tagged_accounts: list[str]
    video_cdn_url: str | None
    location_string: str | None
    top_comments: list[dict]
    date_posted: datetime | None
    raw_json: dict
    author_profile_url: str | None = None
