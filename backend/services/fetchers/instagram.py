import os
import re
from datetime import datetime, timezone

from services.raw_post import RawPost

_SHORTCODE_RE = re.compile(r'instagram\.com/(?:p|reel)/([\w-]+)')
_HASHTAG_RE = re.compile(r'#(\w+)')
_MENTION_RE = re.compile(r'@([\w.]+)')


def fetch(url: str, embedded_caption: str | None = None) -> RawPost:
    """Fetch an Instagram post.

    Fallback chain:
      1. yt-dlp — tries to get video CDN URL for audio transcription
      2. Instaloader — if INSTAGRAM_USERNAME is set
      3. Apify — if APIFY_API_TOKEN is set
      4. Embedded caption — use caption text from the export JSON when all fetchers fail
    """
    last_error = None

    # 1. yt-dlp (free, no credentials needed for public posts)
    try:
        return _fetch_ytdlp(url)
    except Exception as e:
        last_error = e

    # 2. Instaloader (free, requires INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD)
    if os.getenv("INSTAGRAM_USERNAME"):
        try:
            return _fetch_instaloader(url)
        except Exception as e:
            last_error = e

    # 3. Apify (paid, requires APIFY_API_TOKEN)
    if os.getenv("APIFY_API_TOKEN"):
        try:
            return _fetch_apify(url)
        except Exception as e:
            last_error = e

    # 4. Fall back to embedded caption from export JSON (no network call needed)
    if embedded_caption:
        return _fetch_from_caption(url, embedded_caption)

    raise RuntimeError(f"All Instagram fetchers failed for {url}: {last_error}")


# ── yt-dlp ───────────────────────────────────────────────────────────────────

def _fetch_ytdlp(url: str) -> RawPost:
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True}
    cookies_file = os.getenv("INSTAGRAM_COOKIES_FILE")
    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError("yt-dlp returned no data")

    caption = info.get("description") or ""
    video_url = _best_video_url(info)

    date_posted = None
    if raw_date := info.get("upload_date"):  # YYYYMMDD
        try:
            date_posted = datetime.strptime(raw_date, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    handle = _extract_handle(info)
    profile_url = _extract_profile_url(info, handle)

    return RawPost(
        platform="instagram",
        url=url,
        author=handle,
        author_platform_id=str(info.get("uploader_id") or info.get("channel_id") or ""),
        author_profile_url=profile_url,
        caption=caption,
        hashtags=_extract_hashtags(caption),
        tagged_accounts=_extract_mentions(caption),
        video_cdn_url=video_url,
        location_string=None,
        top_comments=[],
        date_posted=date_posted,
        raw_json=info,
    )


def _best_video_url(info: dict) -> str | None:
    if formats := info.get("formats"):
        video_formats = [f for f in formats if f.get("vcodec") != "none" and f.get("url")]
        if video_formats:
            return video_formats[-1]["url"]
    return info.get("url") or None


# ── Instaloader ───────────────────────────────────────────────────────────────

_instaloader_instance = None


def _get_instaloader():
    """Return a logged-in Instaloader instance, creating it once per process."""
    global _instaloader_instance
    if _instaloader_instance is None:
        import instaloader
        L = instaloader.Instaloader(quiet=True, download_videos=False)
        L.login(os.getenv("INSTAGRAM_USERNAME"), os.getenv("INSTAGRAM_PASSWORD"))
        _instaloader_instance = L
    return _instaloader_instance


def _fetch_instaloader(url: str) -> RawPost:
    import instaloader

    shortcode = _extract_shortcode(url)
    L = _get_instaloader()
    post = instaloader.Post.from_shortcode(L.context, shortcode)

    caption = post.caption or ""
    handle = post.owner_username or ""
    return RawPost(
        platform="instagram",
        url=url,
        author=handle,
        author_platform_id=str(post.owner_id or ""),
        author_profile_url=f"https://www.instagram.com/{handle}/" if handle else None,
        caption=caption,
        hashtags=list(post.caption_hashtags) if post.caption_hashtags else _extract_hashtags(caption),
        tagged_accounts=[u.username for u in (post.tagged_users or [])],
        video_cdn_url=post.video_url if post.is_video else None,
        location_string=str(post.location.name) if post.location else None,
        top_comments=[],
        date_posted=post.date_utc.replace(tzinfo=timezone.utc) if post.date_utc else None,
        raw_json={
            "shortcode": shortcode,
            "is_video": post.is_video,
            "likes": post.likes,
        },
    )


# ── Apify ─────────────────────────────────────────────────────────────────────

def _fetch_apify(url: str) -> RawPost:
    from apify_client import ApifyClient

    client = ApifyClient(os.getenv("APIFY_API_TOKEN"))
    actor_input = {"directUrls": [url], "resultsLimit": 1}

    raw = _run_actor(client, "apify/instagram-post-scraper", actor_input)
    if raw is None:
        raw = _run_actor(client, "apify/instagram-reel-scraper", actor_input)
    if raw is None:
        raise ValueError(f"Apify returned no data for {url}")

    return _map_apify(url, raw)


def _run_actor(client, actor_id: str, actor_input: dict) -> dict | None:
    run = client.actor(actor_id).call(run_input=actor_input)
    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return items[0] if items else None


def _map_apify(url: str, raw: dict) -> RawPost:
    date_posted = None
    if ts := raw.get("timestamp"):
        try:
            date_posted = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return RawPost(
        platform="instagram",
        url=url,
        author=raw.get("ownerUsername") or raw.get("username") or "",
        author_platform_id=str(raw.get("ownerId") or raw.get("userId") or ""),
        caption=raw.get("caption") or "",
        hashtags=raw.get("hashtags") or [],
        tagged_accounts=[u.get("username", "") for u in (raw.get("taggedUsers") or [])],
        video_cdn_url=raw.get("videoUrl") or raw.get("videoPlayUrl") or None,
        location_string=raw.get("locationName") or None,
        top_comments=raw.get("latestComments") or [],
        date_posted=date_posted,
        raw_json=raw,
    )


# ── Embedded caption (export JSON fallback) ───────────────────────────────────

def _fetch_from_caption(url: str, caption: str) -> RawPost:
    """Build a RawPost from the caption text embedded in the Instagram data export."""
    return RawPost(
        platform="instagram",
        url=url,
        author="",
        author_platform_id=None,
        caption=caption,
        hashtags=_extract_hashtags(caption),
        tagged_accounts=_extract_mentions(caption),
        video_cdn_url=None,  # no video URL without fetching
        location_string=None,
        top_comments=[],
        date_posted=None,
        raw_json={"source": "instagram_export_caption"},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_profile_url(info: dict, handle: str) -> str | None:
    """Return the canonical Instagram profile URL."""
    for key in ("uploader_url", "channel_url"):
        val = info.get(key) or ""
        if "instagram.com/" in val and "/reel/" not in val and "/p/" not in val:
            return val.rstrip("/") + "/"
    # Build from handle only if it's clearly a handle (not a numeric ID or shortcode)
    if handle and re.match(r'^[\w.]+$', handle) and not handle.isdigit():
        return f"https://www.instagram.com/{handle}/"
    return None


def _extract_handle(info: dict) -> str:
    """Extract the @handle from yt-dlp info.

    Instagram sets: uploader=display_name, channel=@handle, uploader_url=None
    """
    # channel is the actual @handle on Instagram
    channel = info.get("channel") or ""
    if channel and re.match(r'^[\w.]+$', channel):
        return channel
    # uploader_url / channel_url: explicit profile URL (not always present)
    for key in ("uploader_url", "channel_url"):
        val = info.get(key) or ""
        if "instagram.com/" in val and "/reel/" not in val and "/p/" not in val:
            slug = val.rstrip("/").split("/")[-1]
            if slug and re.match(r'^[\w.]+$', slug):
                return slug
    # Fall back to display name — never use numeric uploader_id or webpage_url shortcode
    return info.get("uploader") or ""


def _extract_shortcode(url: str) -> str:
    m = _SHORTCODE_RE.search(url)
    if not m:
        raise ValueError(f"Could not extract shortcode from {url}")
    return m.group(1)


def _extract_hashtags(text: str) -> list[str]:
    return _HASHTAG_RE.findall(text)


def _extract_mentions(text: str) -> list[str]:
    return _MENTION_RE.findall(text)
