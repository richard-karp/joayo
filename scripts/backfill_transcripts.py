#!/usr/bin/env python3
"""Re-extract audio-only reels that were processed caption-only.

Nearly the whole library was extracted before Groq transcription worked, so the
audio (where listicle reels put their actual content) was never used. This script
finds those reels and re-runs them through the current pipeline with force=true.

Run it against a LOCAL backend so fetching goes through yt-dlp (free) instead of
prod's Apify (paid); then push the results up with scripts/push-to-prod.sh.

  --source  API to READ the transcript_missing reel list from (default: prod)
  --target  API to RUN the re-extraction against          (default: localhost)

It submits reels in batches (one job per batch), waits for each job to finish, and
records completed URLs in a progress file so an interrupted run resumes cleanly.
If a job PAUSES (e.g. Instagram rate-limits yt-dlp after many fetches), it stops so
you can wait and re-run — the completed reels are saved either way.

Examples:
  # dry run: see how many reels and a sample, submit nothing
  python scripts/backfill_transcripts.py --dry-run

  # smoke test: just the first 5, against local
  python scripts/backfill_transcripts.py --limit 5

  # the real backfill (local extraction), 20 reels per job
  python scripts/backfill_transcripts.py

Uses only the Python standard library.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROD = "https://joayo-api.fly.dev"


def _get(base: str, path: str, retries: int = 3):
    """GET + parse JSON, retrying transient network errors so a blip during a
    multi-hour run doesn't crash it."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(base + path, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if attempt < retries - 1:
                time.sleep(3)
    raise last  # type: ignore[misc]


def transcript_missing_reels(source_base: str) -> list[str]:
    """Unique /reel/ URLs whose places are still transcript_missing (photos /p/
    have no audio and are skipped)."""
    places = _get(source_base, "/api/places")
    reels: set[str] = set()
    for p in places:
        if not p.get("transcript_missing"):
            continue
        for u in (p.get("source_urls") or []):
            if u and "/reel/" in u:
                reels.add(u.rstrip("/"))
    return sorted(reels)


def submit(target_base: str, urls: list[str], secret: str) -> str:
    data = urllib.parse.urlencode({"urls": "\n".join(urls), "force": "true"}).encode()
    req = urllib.request.Request(target_base + "/api/extract", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if secret:
        req.add_header("X-Extract-Secret", secret)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["job_id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=PROD, help="API to read the reel list from (default: prod)")
    ap.add_argument("--target", default="http://localhost:8000", help="API to re-extract against (default: local)")
    ap.add_argument("--secret", default=os.getenv("EXTRACT_SECRET", ""), help="X-Extract-Secret for target (empty if local gate is open)")
    ap.add_argument("--batch-size", type=int, default=20, help="reels per job (default: 20)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N reels (0 = all)")
    ap.add_argument("--progress", default="scripts/backfill-progress.txt", help="file of completed URLs (resume support)")
    ap.add_argument("--failed", default="scripts/backfill-failed.txt", help="file where per-URL failures are recorded for a targeted re-run")
    ap.add_argument("--poll-timeout", type=int, default=5400, help="max seconds to wait per batch job")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, submit nothing")
    args = ap.parse_args()

    done: set[str] = set()
    if os.path.exists(args.progress):
        done = {l.strip() for l in open(args.progress) if l.strip()}

    reels = [u for u in transcript_missing_reels(args.source) if u not in done]
    if args.limit:
        reels = reels[: args.limit]

    print(f"source={args.source}  target={args.target}")
    print(f"transcript_missing reels to process: {len(reels)}  (already done: {len(done)})")
    if args.dry_run:
        for u in reels[:15]:
            print("  ", u)
        if len(reels) > 15:
            print(f"   ... and {len(reels) - 15} more")
        print("(dry run — nothing submitted)")
        return 0
    if not reels:
        print("Nothing to do.")
        return 0

    os.makedirs(os.path.dirname(args.progress) or ".", exist_ok=True)
    prog = open(args.progress, "a")
    failed_f = open(args.failed, "a")
    total_failed = 0
    try:
        for start in range(0, len(reels), args.batch_size):
            batch = reels[start : start + args.batch_size]
            n = start // args.batch_size + 1
            print(f"\n[batch {n}] {len(batch)} reels → {args.target}")
            try:
                job_id = submit(args.target, batch, args.secret)
            except urllib.error.HTTPError as e:
                print(f"  submit failed: HTTP {e.code} {e.read()[:200]!r}. Stopping.")
                return 2
            print(f"  job {job_id} — polling")

            deadline = time.time() + args.poll_timeout
            last = None
            while time.time() < deadline:
                job = _get(args.target, f"/api/jobs/{job_id}")
                st = job.get("status")
                if st != last:
                    print(f"  status={st} processed={job.get('processed')}/{job.get('total_urls')}")
                    last = st
                if st in ("complete", "complete_with_errors", "cancelled"):
                    break
                if st == "paused":
                    msgs = [w.get("message") for w in (job.get("warnings") or [])]
                    print(f"  PAUSED ({job.get('paused_reason')}): {msgs}")
                    print("  Stopping — this batch is NOT marked done. Wait, then re-run to resume.")
                    return 2
                time.sleep(10)
            else:
                print("  poll timed out; stopping without marking this batch done.")
                return 2

            failed = job.get("failed_urls") or []
            total_failed += len(failed)
            for fu in failed:
                failed_f.write((fu.get("url") or "") + "\n")
            for u in batch:
                prog.write(u + "\n")
            prog.flush()
            failed_f.flush()
            print(f"  batch done — failed this batch: {len(failed)}")
    finally:
        prog.close()
        failed_f.close()

    print(f"\nAll batches processed. Total failed URLs: {total_failed}"
          + (f" — recorded in {args.failed} (re-run with --limit or a hand-picked list)" if total_failed else ""))
    print("Next: push the local results to prod:")
    print("  ADMIN_TOKEN=<your-token> ./scripts/push-to-prod.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
