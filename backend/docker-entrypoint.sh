#!/bin/sh
set -e

# Seed the persistent volume with the DB baked into the image on first boot.
# On later deploys the volume already has the live data, so the seed is skipped
# and any places extracted in production are preserved.
if [ ! -f /data/places.db ]; then
  if [ -f /app/places.db ]; then
    echo "Seeding /data/places.db from baked image copy..."
    cp /app/places.db /data/places.db
    # Copy the WAL sidecar too (if any) so uncheckpointed data isn't lost;
    # SQLite replays it into the DB on first open.
    [ -f /app/places.db-wal ] && cp /app/places.db-wal /data/places.db-wal
  else
    echo "No baked places.db found; starting with an empty DB."
  fi
fi

exec uvicorn main:app --host 0.0.0.0 --port 8000
