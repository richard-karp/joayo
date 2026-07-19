import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import NamedTuple

import httpx

from services import mocks

logger = logging.getLogger("transcriber")

# We call Groq's OpenAI-compatible Whisper endpoint directly over HTTP (no SDK).
# Whisper only needs audio, so we download the reel video and extract a small mono
# 16kHz audio track with ffmpeg before uploading — that keeps every request well
# under Groq's free-tier size cap (25MB) regardless of the source video's size.
GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")

_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Free-tier safety: Groq's free tier caps Whisper at 20 requests/minute. We enforce a
# process-wide minimum gap between requests so a job transcribing many posts
# back-to-back can never burst past that cap. 4s -> <=15 req/min, leaving headroom.
# (The daily cap is enforced reactively: a 429 means Groq refused, so we never
#  actually exceed it — we back off and, after repeated hits, the job pauses.)
_MIN_REQUEST_INTERVAL = float(os.getenv("GROQ_MIN_REQUEST_INTERVAL", "4.0"))
_rate_lock = threading.Lock()
_next_slot = 0.0  # monotonic time the next request is allowed to start

# Groq/Whisper verbose_json reports the detected language as a full English name
# ("English", "Korean"). Downstream language checks key off ISO-639-1 codes ("en"),
# so normalize the common cases; anything unmapped passes through (lowercased).
_LANG_NAME_TO_ISO = {
    "english": "en", "korean": "ko", "japanese": "ja", "chinese": "zh",
    "thai": "th", "vietnamese": "vi", "spanish": "es", "french": "fr",
    "german": "de", "italian": "it", "portuguese": "pt", "indonesian": "id",
}


class TranscriptResult(NamedTuple):
    text: str
    detected_language: str | None


class RateLimitError(RuntimeError):
    """Raised when Groq returns 429 and retries are exhausted."""
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


def _normalize_language(lang: str | None) -> str | None:
    if not lang:
        return None
    key = lang.strip().lower()
    return _LANG_NAME_TO_ISO.get(key, key)


def _await_rate_slot() -> None:
    """Block until this caller's throttle slot. Spaces every transcription request by
    at least _MIN_REQUEST_INTERVAL across all threads/jobs so we stay under Groq's
    per-minute cap. Reserves the slot under the lock, then sleeps outside it so
    concurrent callers stagger cleanly instead of all waking at once."""
    global _next_slot
    with _rate_lock:
        now = time.monotonic()
        slot = max(now, _next_slot)
        _next_slot = slot + _MIN_REQUEST_INTERVAL
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _extract_audio(video_url: str, dest_dir: str) -> str:
    """Download the reel video and extract a small mono 16kHz mp3 for Whisper.
    Returns the audio file path. Raises on download or ffmpeg failure."""
    video_path = os.path.join(dest_dir, "source")
    audio_path = os.path.join(dest_dir, "audio.mp3")

    with httpx.Client(timeout=180, follow_redirects=True) as client:
        with client.stream("GET", video_url, headers={"User-Agent": _UA}) as resp:
            resp.raise_for_status()
            with open(video_path, "wb") as f:
                for chunk in resp.iter_bytes(1 << 16):
                    f.write(chunk)

    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", audio_path],
        capture_output=True,
        timeout=120,  # local file, but never let a pathological input hang the worker
    )
    if proc.returncode != 0 or not os.path.exists(audio_path):
        tail = proc.stderr.decode("utf-8", "replace")[-400:]
        raise RuntimeError(f"ffmpeg audio extraction failed: {tail}")
    return audio_path


def transcribe(video_cdn_url: str, *, max_rate_limit_retries: int = 3) -> TranscriptResult:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        # No key (local dev / unset): fall back to a mock transcript, as before.
        return TranscriptResult(text=mocks.MOCK_TRANSCRIPT, detected_language=None)

    headers = {"Authorization": f"Bearer {api_key}"}

    with tempfile.TemporaryDirectory() as tmp:
        try:
            audio_path = _extract_audio(video_cdn_url, tmp)
        except Exception as e:
            logger.warning("audio extraction failed for %s: %s", (video_cdn_url or "")[:80], e)
            raise

        with httpx.Client(timeout=120) as client:
            for attempt in range(max_rate_limit_retries):
                _await_rate_slot()
                with open(audio_path, "rb") as af:
                    files = {
                        "model": (None, GROQ_MODEL),
                        "file": ("audio.mp3", af, "audio/mpeg"),
                        "response_format": (None, "verbose_json"),  # includes detected `language`
                        "temperature": (None, "0"),
                    }
                    resp = client.post(GROQ_TRANSCRIPTION_URL, headers=headers, files=files)

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60"))
                    if attempt < max_rate_limit_retries - 1:
                        time.sleep(min(retry_after, 30))
                        continue
                    raise RateLimitError(
                        f"Groq rate limited after {max_rate_limit_retries} attempts "
                        f"(Retry-After: {retry_after}s). Wait before resuming.",
                        retry_after=retry_after,
                    )
                if not resp.is_success:
                    logger.warning("Groq transcription failed: %s %s", resp.status_code, resp.text[:300])
                    raise RuntimeError(
                        f"Groq transcription failed: {resp.status_code} {resp.text[:200]}"
                    )

                body = resp.json()
                return TranscriptResult(
                    text=body.get("text") or "",
                    detected_language=_normalize_language(body.get("language")),
                )

    raise RuntimeError("Groq transcription failed: retries exhausted")
