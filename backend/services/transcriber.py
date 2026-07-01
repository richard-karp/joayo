import os
import time

import httpx

from services import mocks

ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"
_POLL_INTERVAL = 3    # seconds between status polls
_MAX_POLL_TIME = 600  # 10 minutes before giving up


class RateLimitError(RuntimeError):
    """Raised when AssemblyAI returns 429 and retries are exhausted."""
    def __init__(self, message: str, retry_after: int = 60):
        super().__init__(message)
        self.retry_after = retry_after


def _headers() -> dict:
    return {"authorization": os.getenv("ASSEMBLYAI_API_KEY", "")}


def transcribe(video_cdn_url: str, *, max_rate_limit_retries: int = 3) -> str:
    if not os.getenv("ASSEMBLYAI_API_KEY"):
        return mocks.MOCK_TRANSCRIPT

    with httpx.Client(timeout=30) as client:
        # Submit transcription job, respecting Retry-After on 429
        transcript_id = None
        for attempt in range(max_rate_limit_retries):
            resp = client.post(
                f"{ASSEMBLYAI_BASE}/transcript",
                headers=_headers(),
                json={"audio_url": video_cdn_url},
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "60"))
                if attempt < max_rate_limit_retries - 1:
                    time.sleep(min(retry_after, 30))
                    continue
                raise RateLimitError(
                    f"AssemblyAI rate limited on submission after {max_rate_limit_retries} attempts "
                    f"(Retry-After: {retry_after}s). Reduce request rate or wait before resuming.",
                    retry_after=retry_after,
                )
            if not resp.is_success:
                raise RuntimeError(f"AssemblyAI submission failed: {resp.status_code} {resp.text[:200]}")
            transcript_id = resp.json()["id"]
            break

        # Poll until completed, error, or timeout
        deadline = time.monotonic() + _MAX_POLL_TIME
        while time.monotonic() < deadline:
            poll = client.get(
                f"{ASSEMBLYAI_BASE}/transcript/{transcript_id}",
                headers=_headers(),
            )
            if poll.status_code == 429:
                retry_after = int(poll.headers.get("Retry-After", "60"))
                time.sleep(min(retry_after, 30))
                continue
            if not poll.is_success:
                raise RuntimeError(f"AssemblyAI poll failed: {poll.status_code} {poll.text[:200]}")
            data = poll.json()
            if data["status"] == "completed":
                return data.get("text") or ""
            if data["status"] == "error":
                raise RuntimeError(f"AssemblyAI transcription error: {data.get('error')}")
            time.sleep(_POLL_INTERVAL)

        raise RuntimeError(f"AssemblyAI transcription timed out after {_MAX_POLL_TIME}s")
