import json
import pytest
from unittest.mock import patch
from services.url_parser import parse_urls_from_json, parse_urls_from_text, expand_playlists


def test_json_flat():
    data = {"href": "https://www.instagram.com/p/ABC123/"}
    assert parse_urls_from_json(json.dumps(data)) == ["https://www.instagram.com/p/ABC123"]


def test_json_deeply_nested():
    data = {"saved_posts": [{"media": [{"string_map": {"url": "https://www.instagram.com/reel/XYZ789/"}}]}]}
    result = parse_urls_from_json(json.dumps(data))
    assert result == ["https://www.instagram.com/reel/XYZ789"]


def test_json_deduplicates():
    url = "https://www.instagram.com/p/ABC123/"
    data = {"a": url, "b": url, "c": [url]}
    assert parse_urls_from_json(json.dumps(data)) == ["https://www.instagram.com/p/ABC123"]


def test_json_no_supported_urls():
    data = {"url": "https://example.com/foo"}
    assert parse_urls_from_json(json.dumps(data)) == []


def test_json_invalid_raises():
    with pytest.raises(Exception):
        parse_urls_from_json("not json")


def test_text_instagram_basic():
    text = "https://www.instagram.com/p/ABC123/\nhttps://www.instagram.com/reel/DEF456/"
    result = parse_urls_from_text(text)
    assert result == [
        "https://www.instagram.com/p/ABC123",
        "https://www.instagram.com/reel/DEF456",
    ]


def test_text_deduplicates():
    url = "https://www.instagram.com/p/ABC123/"
    text = f"{url}\n{url}"
    assert parse_urls_from_text(text) == ["https://www.instagram.com/p/ABC123"]


def test_text_empty():
    assert parse_urls_from_text("") == []


def test_json_multiple_levels_multiple_urls():
    data = {
        "posts": [
            {"link": "https://www.instagram.com/p/A1/"},
            {"nested": {"href": "https://www.instagram.com/reel/B2/"}},
        ]
    }
    result = parse_urls_from_json(json.dumps(data))
    assert set(result) == {"https://www.instagram.com/p/A1", "https://www.instagram.com/reel/B2"}


# ── YouTube URL parsing ───────────────────────────────────────────────────────

def test_text_youtube_video():
    text = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    result = parse_urls_from_text(text)
    assert result == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_text_youtu_be_short():
    text = "https://youtu.be/dQw4w9WgXcQ"
    result = parse_urls_from_text(text)
    assert result == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_text_youtube_playlist():
    text = "https://www.youtube.com/playlist?list=PLmock123"
    result = parse_urls_from_text(text)
    assert result == ["https://www.youtube.com/playlist?list=PLmock123"]


def test_text_mixed_instagram_and_youtube():
    text = (
        "https://www.instagram.com/p/ABC123/\n"
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
    )
    result = parse_urls_from_text(text)
    assert "https://www.instagram.com/p/ABC123" in result
    assert "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in result


def test_json_youtube_video_in_json():
    data = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    result = parse_urls_from_json(json.dumps(data))
    assert result == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


# ── Playlist expansion ────────────────────────────────────────────────────────

def test_expand_playlists_passes_through_video_urls():
    urls = [
        "https://www.instagram.com/p/ABC123",
        "https://www.youtube.com/watch?v=vid1",
    ]
    result = expand_playlists(urls)
    assert result == urls


def test_expand_playlists_expands_playlist():
    playlist_url = "https://www.youtube.com/playlist?list=PLmock"
    expanded = [
        "https://www.youtube.com/watch?v=vid1",
        "https://www.youtube.com/watch?v=vid2",
    ]
    with patch("services.url_parser.expand_playlist", return_value=expanded):
        result = expand_playlists([playlist_url])
    assert result == expanded


def test_expand_playlists_deduplicates_across_sources():
    playlist_url = "https://www.youtube.com/playlist?list=PLmock"
    expanded = ["https://www.youtube.com/watch?v=vid1", "https://www.youtube.com/watch?v=vid2"]
    with patch("services.url_parser.expand_playlist", return_value=expanded):
        result = expand_playlists([playlist_url, "https://www.youtube.com/watch?v=vid1"])
    assert result.count("https://www.youtube.com/watch?v=vid1") == 1


def test_expand_playlists_keeps_original_on_failure():
    playlist_url = "https://www.youtube.com/playlist?list=PLbroken"
    with patch("services.url_parser.expand_playlist", side_effect=Exception("network error")):
        result = expand_playlists([playlist_url])
    assert result == [playlist_url]
