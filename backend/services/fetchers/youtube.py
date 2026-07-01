import os
import re
from datetime import datetime

import yt_dlp

from services.raw_post import RawPost
from services import mocks

_VIDEO_ID_RE = re.compile(r'(?:v=|youtu\.be/|shorts/)([\w-]+)')


def _video_id(url: str) -> str | None:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _fetch_transcript(video_id: str) -> str | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
        entries = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(e["text"] for e in entries)
    except Exception:
        return None


def fetch(url: str) -> RawPost:
    if not os.getenv("APIFY_API_TOKEN") and not os.getenv("YOUTUBE_FETCH_REAL"):
        return mocks.mock_youtube_post(url)

    opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_id = info.get("id") or _video_id(url)
    transcript = _fetch_transcript(video_id) if video_id else None

    # Parse upload_date: yt-dlp returns "YYYYMMDD"
    date_posted: datetime | None = None
    if raw_date := info.get("upload_date"):
        try:
            date_posted = datetime.strptime(raw_date, "%Y%m%d")
        except ValueError:
            pass

    # Caption = description (primary text for extraction)
    caption = info.get("description") or info.get("title") or ""

    raw_json: dict = {"id": video_id}
    if transcript:
        raw_json["transcript"] = transcript

    return RawPost(
        platform="youtube",
        url=url,
        author=info.get("uploader") or info.get("channel") or "",
        author_platform_id=info.get("channel_id"),
        caption=caption,
        hashtags=info.get("tags") or [],
        tagged_accounts=[],
        # Set video_cdn_url so the pipeline enters the transcript branch and finds
        # the native transcript in raw_json. When no transcript is available, None
        # causes the pipeline to fall back to caption-only mode.
        video_cdn_url=url if transcript is not None else None,
        location_string=None,
        top_comments=[],
        date_posted=date_posted,
        raw_json=raw_json,
    )
