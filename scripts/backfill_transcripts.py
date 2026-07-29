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

A reel is done once it extracts successfully — with a transcript OR caption-only. A
reel with no usable audio (music, no narration) is *correctly* caption-only, so that's
a complete result, not a failure. Only reels that HARD-FAIL (in the job's failed_urls:
Instagram throttling the video fetch, or an extraction error) are re-queued and retried
on the next run — up to --max-attempts, then given up (recorded in --failed) so a
persistently-unfetchable reel can't loop forever. Per-reel retry counts live in the
--attempts JSON.

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


def reels_with_transcript(target_base: str) -> set[str]:
    """Reel URLs that now have at least one transcript-based place (transcript_missing
    False). This — NOT the inverse of transcript_missing_reels — is the reliable
    "did this reel actually get a transcript" signal: force re-extraction ADDS new
    transcript-based places but leaves the reel's original caption-only places (still
    transcript_missing=True) in the DB, so every recovered reel also still has missing
    places. The presence of any non-missing place is what marks it recovered."""
    places = _get(target_base, "/api/places")
    good: set[str] = set()
    for p in places:
        if p.get("transcript_missing"):
            continue
        for u in (p.get("source_urls") or []):
            if u and "/reel/" in u:
                good.add(u.rstrip("/"))
    return good


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
    ap.add_argument("--failed", default="scripts/backfill-failed.txt", help="file where reels that gave up (never got a transcript) are recorded")
    ap.add_argument("--attempts", default="scripts/backfill-attempts.json", help="JSON tracking retry counts for reels that came back caption-only")
    ap.add_argument("--max-attempts", type=int, default=5, help="give up on a reel still missing its transcript after this many completed attempts")
    ap.add_argument("--poll-timeout", type=int, default=5400, help="max seconds to wait per batch job")
    ap.add_argument("--dry-run", action="store_true", help="list what would run, submit nothing")
    args = ap.parse_args()

    done: set[str] = set()
    if os.path.exists(args.progress):
        done = {l.strip() for l in open(args.progress) if l.strip()}

    # Retry counts for reels that were processed but came back caption-only. Reels
    # here (and not in `done`) are re-queued below and retried on the next run.
    attempts: dict[str, int] = {}
    if os.path.exists(args.attempts):
        try:
            attempts = {k: int(v) for k, v in json.load(open(args.attempts)).items()}
        except Exception:
            attempts = {}

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

            # Classify each reel by outcome:
            #   - transcript: got a transcript (>=1 non-missing place on target) -> done
            #   - caption-only: extracted successfully but no transcript -> ALSO done.
            #       A reel with no usable audio (music / no narration) is *correctly*
            #       caption-only — the caption is all there is — so it's complete, not a
            #       failure. Retrying it just re-extracts the same result and wastes budget.
            #   - hard fail (in the job's failed_urls: fetch throttle, extraction error) ->
            #       the genuinely transient case; re-queue and retry up to --max-attempts,
            #       then give up (record in failed) so it can't loop forever.
            recovered = reels_with_transcript(args.target)
            failed_now = {(fu.get("url") or "").rstrip("/") for fu in (job.get("failed_urls") or [])}
            n_tr = n_capt = n_requeued = n_gaveup = 0
            for u in batch:
                if u in recovered:
                    prog.write(u + "\n")
                    attempts.pop(u, None)
                    n_tr += 1
                elif u in failed_now:
                    attempts[u] = attempts.get(u, 0) + 1
                    if attempts[u] >= args.max_attempts:
                        prog.write(u + "\n")          # give up — stop retrying
                        failed_f.write(u + "\n")
                        attempts.pop(u, None)
                        n_gaveup += 1
                    else:
                        n_requeued += 1
                else:
                    prog.write(u + "\n")
                    attempts.pop(u, None)
                    n_capt += 1
            prog.flush()
            failed_f.flush()
            with open(args.attempts, "w") as af:
                json.dump(attempts, af, indent=0)
            total_failed += n_gaveup
            print(f"  batch done — transcript: {n_tr}, caption-only: {n_capt}, retry: {n_requeued}, gave up: {n_gaveup}")
    finally:
        prog.close()
        failed_f.close()

    pending = len(attempts)
    print(f"\nAll batches processed. Gave up (unfetchable after {args.max_attempts} tries): {total_failed}"
          + (f" — recorded in {args.failed}" if total_failed else ""))
    if pending:
        print(f"Re-queued for the next run (hard-failed this time, under the retry cap): {pending}")
    print("Next: push the local results to prod:")
    print("  ADMIN_TOKEN=<your-token> ./scripts/push-to-prod.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
