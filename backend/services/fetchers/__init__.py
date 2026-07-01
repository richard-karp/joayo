from services.raw_post import RawPost
from services.fetchers import instagram, youtube


def fetch_post(url: str, embedded_caption: str | None = None) -> RawPost:
    if "instagram.com" in url:
        return instagram.fetch(url, embedded_caption=embedded_caption)
    if "youtube.com" in url or "youtu.be" in url:
        return youtube.fetch(url)
    if "facebook.com" in url:
        raise NotImplementedError("Facebook fetcher not yet implemented (Phase 3)")
    if "tiktok.com" in url:
        raise NotImplementedError("TikTok fetcher not yet implemented (Phase 4)")
    raise ValueError(f"Unsupported platform: {url}")
