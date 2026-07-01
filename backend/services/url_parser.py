import json
import re

_INSTAGRAM_RE = re.compile(r'https?://(?:www\.)?instagram\.com/(p|reel)/([\w-]+)/?')
_YOUTUBE_VIDEO_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/(?:watch\?(?:[^&\s]*&)*v=|shorts/)|youtu\.be/)([\w-]+)'
)
_YOUTUBE_PLAYLIST_RE = re.compile(
    r'https?://(?:www\.)?youtube\.com/(?:playlist\?|.*[?&])list=([\w-]+)'
)

MAX_PLAYLIST_VIDEOS = 50


def _scan(obj) -> list[str]:
    """Recursively walk a JSON-decoded object and collect all supported post URLs."""
    if isinstance(obj, str):
        urls = []
        for m in _INSTAGRAM_RE.finditer(obj):
            urls.append(m.group(0).rstrip("/"))
        for m in _YOUTUBE_VIDEO_RE.finditer(obj):
            urls.append(f"https://www.youtube.com/watch?v={m.group(1)}")
        for m in _YOUTUBE_PLAYLIST_RE.finditer(obj):
            urls.append(f"https://www.youtube.com/playlist?list={m.group(1)}")
        return urls
    if isinstance(obj, dict):
        return [u for v in obj.values() for u in _scan(v)]
    if isinstance(obj, list):
        return [u for item in obj for u in _scan(item)]
    return []


def expand_playlist(playlist_url: str) -> list[str]:
    """Use yt-dlp (flat extraction) to turn a playlist URL into individual video URLs."""
    import yt_dlp

    opts = {
        "quiet": True,
        "extract_flat": True,
        "playlistend": MAX_PLAYLIST_VIDEOS,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(playlist_url, download=False)

    entries = info.get("entries") or []
    return [
        f"https://www.youtube.com/watch?v={e['id']}"
        for e in entries
        if e and e.get("id")
    ]


def expand_playlists(urls: list[str]) -> list[str]:
    """Replace any playlist URLs in the list with their constituent video URLs."""
    result = []
    seen: set[str] = set()

    for url in urls:
        if "youtube.com/playlist" in url:
            try:
                video_urls = expand_playlist(url)
            except Exception:
                # If expansion fails, keep the original URL so it ends up in failed_urls
                video_urls = [url]
            for u in video_urls:
                if u not in seen:
                    seen.add(u)
                    result.append(u)
        else:
            if url not in seen:
                seen.add(url)
                result.append(url)

    return result


def parse_urls_from_json(data: bytes | str) -> list[str]:
    obj = json.loads(data)
    urls = _scan(obj)
    return list(dict.fromkeys(urls))


def _fix_encoding(text: str) -> str:
    """Instagram exports encode multi-byte chars as latin-1 codepoints (ð…).
    Re-encoding as latin-1 then decoding as UTF-8 restores the original text."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _is_collections_json(obj) -> bool:
    return (
        isinstance(obj, list)
        and len(obj) > 0
        and isinstance(obj[0], dict)
        and any(
            isinstance(lv, dict) and lv.get("label") == "Name"
            for lv in obj[0].get("label_values", [])
        )
    )


def list_collections(data: bytes | str) -> list[dict]:
    """Return [{name, count}] for each collection in a saved_collections.json file."""
    obj = json.loads(data)
    if not _is_collections_json(obj):
        return []
    result = []
    for col in obj:
        name = next(
            (lv["value"] for lv in col.get("label_values", []) if lv.get("label") == "Name"),
            None,
        )
        if name is None:
            continue
        urls = _scan(col)
        result.append({"name": name, "count": len(set(urls))})
    return result


def parse_urls_from_collection(data: bytes | str, collection_name: str) -> list[str]:
    """Extract all post URLs from a named collection in saved_collections.json."""
    return [p["url"] for p in parse_posts_from_collection(data, collection_name)]


def parse_posts_from_collection(data: bytes | str, collection_name: str) -> list[dict]:
    """Extract [{url, caption}] from a named collection in saved_collections.json.

    The export JSON embeds the post caption alongside each URL, allowing us to
    skip the Instagram fetch entirely and use the real text directly.
    """
    obj = json.loads(data)
    if not _is_collections_json(obj):
        urls = parse_urls_from_json(data)
        return [{"url": u} for u in urls]

    for col in obj:
        name = next(
            (lv["value"] for lv in col.get("label_values", []) if lv.get("label") == "Name"),
            None,
        )
        if not (name and name.lower() == collection_name.lower()):
            continue

        seen: dict[str, dict] = {}
        for lv in col.get("label_values", []):
            items = lv.get("dict")
            if not isinstance(items, list):
                continue
            for item in items:
                inner = item.get("dict")
                if not isinstance(inner, list):
                    continue
                url = next(
                    (e["value"] for e in inner if e.get("label") == "URL" and e.get("value")),
                    None,
                )
                raw_caption = next(
                    (e["value"] for e in inner if e.get("label") == "Caption" and e.get("value")),
                    None,
                )
                caption = _fix_encoding(raw_caption) if raw_caption else None
                if not url:
                    # fall back to scanning the item for URLs
                    scanned = _scan(item)
                    url = scanned[0] if scanned else None
                if not url:
                    continue
                url = url.rstrip("/")
                m = _INSTAGRAM_RE.search(url)
                if not m:
                    continue
                url = m.group(0).rstrip("/")
                if url not in seen:
                    seen[url] = {"url": url, "caption": caption}
                elif caption and not seen[url].get("caption"):
                    seen[url]["caption"] = caption

        return list(seen.values())

    return []


def parse_urls_from_text(text: str) -> list[str]:
    urls = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for m in _INSTAGRAM_RE.finditer(line):
            urls.append(m.group(0).rstrip("/"))
        for m in _YOUTUBE_VIDEO_RE.finditer(line):
            urls.append(f"https://www.youtube.com/watch?v={m.group(1)}")
        for m in _YOUTUBE_PLAYLIST_RE.finditer(line):
            urls.append(f"https://www.youtube.com/playlist?list={m.group(1)}")
    return list(dict.fromkeys(urls))
