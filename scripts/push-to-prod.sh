#!/usr/bin/env bash
#
# Push locally-extracted places to the deployed joayo instance (additive merge).
#
# Consolidates the local SQLite WAL, then uploads places.db to the import endpoint,
# which inserts only NEW places (existing rows, votes, and in-cloud extractions are
# preserved — never an overwrite), dedups, and recomputes ambient-noise flags.
#
# Stop the local backend first so places.db is fully consolidated.
#
# Usage:
#   ADMIN_TOKEN=<your-token> ./scripts/push-to-prod.sh
#
# Optional overrides:
#   API_URL=https://joayo-api.fly.dev   DB=backend/places.db
#
set -euo pipefail

API_URL="${API_URL:-https://joayo-api.fly.dev}"
DB="${DB:-backend/places.db}"

if [ -z "${ADMIN_TOKEN:-}" ]; then
  echo "error: ADMIN_TOKEN is not set." >&2
  echo "  usage: ADMIN_TOKEN=<your-token> $0" >&2
  exit 1
fi

# Resolve DB relative to the repo root so the script works from any directory.
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "$DB" in
  /*) DB_PATH="$DB" ;;
  *)  DB_PATH="$REPO_ROOT/$DB" ;;
esac

if [ ! -f "$DB_PATH" ]; then
  echo "error: DB not found at $DB_PATH" >&2
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "error: sqlite3 not found on PATH" >&2
  exit 1
fi

echo "→ Consolidating WAL into $DB_PATH"
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null

echo "→ Uploading to $API_URL/api/admin/import-places"
resp="$(curl -sS -w $'\n%{http_code}' \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -F "file=@$DB_PATH" \
  "$API_URL/api/admin/import-places")"

body="$(printf '%s\n' "$resp" | sed '$d')"
code="$(printf '%s\n' "$resp" | tail -n1)"

if [ "$code" != "200" ]; then
  echo "error: import failed (HTTP $code)" >&2
  printf '%s\n' "$body" >&2
  exit 1
fi

if command -v jq >/dev/null 2>&1; then
  printf '%s\n' "$body" | jq .
else
  printf '%s\n' "$body"
fi
echo "✓ Done."
