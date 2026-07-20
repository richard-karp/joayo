#!/usr/bin/env bash
#
# Auto-resume the transcript backfill through transient pauses (Anthropic /
# Instagram rate limits) until every reel is done. Re-runs backfill_transcripts.py,
# which skips already-done reels, sleeping between attempts. Gives up if 3
# consecutive attempts make zero progress (something is persistently wrong).
#
# Usage:  ./scripts/backfill_loop.sh
# Needs the local backend running on :8000 and the Mac kept awake (caffeinate -i).
#
set -u
cd "$(dirname "$0")/.."
PROG=scripts/backfill-progress.txt
SLEEP="${SLEEP:-120}"
stalls=0

while true; do
  before=$(wc -l < "$PROG" 2>/dev/null || echo 0)
  python3 -u scripts/backfill_transcripts.py --batch-size 5 --poll-timeout 10800
  rc=$?
  after=$(wc -l < "$PROG" 2>/dev/null || echo 0)

  if [ "$rc" -eq 0 ]; then
    echo "=== BACKFILL COMPLETE ($after reels done) ==="
    echo "Next: ADMIN_TOKEN=<token> ./scripts/push-to-prod.sh"
    break
  fi

  if [ "$after" -le "$before" ]; then
    stalls=$((stalls + 1))
    echo "=== no progress this attempt (stall $stalls/3) — rc=$rc, at $after/650 ==="
    if [ "$stalls" -ge 3 ]; then
      echo "=== STALLED: 3 attempts with no progress. Check backend/logs and resume manually. ==="
      break
    fi
  else
    stalls=0
    echo "=== progress: $after/650 done; paused (rc=$rc), resuming in ${SLEEP}s ==="
  fi
  sleep "$SLEEP"
done
