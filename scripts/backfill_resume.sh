#!/usr/bin/env bash
#
# Daily one-command resume for the transcript backfill.
# Extraction runs on Groq gpt-oss-120b (free tier), which has a ~200K tokens/day
# cap (~60-70 reels/day). Run this once a day: it starts the local backend if
# needed, keeps the Mac awake, and grinds until today's token budget is spent
# (the loop stalls out), then exits. Repeat daily until progress hits 650.
#
#   ./scripts/backfill_resume.sh
#
# When done (or any checkpoint), push results up:  ADMIN_TOKEN=<t> ./scripts/push-to-prod.sh
set -u
cd "$(dirname "$0")/.."

up() { curl -s -o /dev/null --max-time 5 -X POST http://localhost:8000/api/extract 2>/dev/null; }

if ! up; then
  echo "local backend not responding — starting it..."
  ( cd backend && nohup venv/bin/uvicorn main:app --port 8000 > /tmp/joayo_backend.log 2>&1 & )
  for _ in $(seq 1 25); do up && break; sleep 2; done
fi
if ! up; then
  echo "ERROR: backend still not up. Check /tmp/joayo_backend.log" >&2
  exit 1
fi

echo "backend up. progress: $(wc -l < scripts/backfill-progress.txt 2>/dev/null || echo 0)/650"
echo "resuming (Mac stays awake; stops when today's Groq budget is spent)..."
SLEEP=120 exec caffeinate -i ./scripts/backfill_loop.sh
