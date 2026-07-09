# joayo

Discover places worth going — extracted from Instagram/social posts, geocoded, and mapped.

Paste post URLs (or an Instagram export), and joayo pulls out the named places, dishes, and
tips with Claude, geocodes them, de-duplicates across posts, and lets you browse, filter,
search, and vote on the results.

## Stack

- **Backend** — FastAPI + SQLite (`backend/`). Extraction pipeline: fetch → transcribe →
  Claude extraction → geocode → de-dupe.
- **Frontend** — Next.js dashboard (`frontend/`): map, list, categories, creators, filters.

## Local development

```bash
# Backend
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env        # fill in keys (see below)
uvicorn main:app --reload      # http://localhost:8000

# Frontend (new terminal)
cd frontend
npm install
echo 'NEXT_PUBLIC_BACKEND_URL=http://localhost:8000' > .env.local
npm run dev                    # http://localhost:3000
```

Run the tests: `cd backend && pytest`.

## Configuration

All keys are documented in [`.env.example`](.env.example). Summary:

| Var | Required? | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Claude extraction |
| `APIFY_API_TOKEN` | yes (cloud) | Instagram/YouTube scraping — see note below |
| `KAKAO_REST_API_KEY` | yes | Korean geocoding |
| `ASSEMBLYAI_API_KEY` | optional | Reel audio → transcript (falls back to caption-only) |
| `EXTRACT_SECRET` | prod | Shared code gating `/api/extract` (set it in production) |
| `ADMIN_TOKEN` | optional | Protects `/api/admin/*` |
| `CORS_ORIGINS` | prod | Comma-separated allowed frontend origins |

### A note on Instagram access

Browser **cookies work locally** (`INSTAGRAM_COOKIES_FILE`) but **not in the cloud** —
Instagram blocks datacenter IPs and cookies expire. For any deployed/self-service use, set
`APIFY_API_TOKEN` and extraction goes through [Apify](https://apify.com) (managed, residential
proxies). Reading/browsing the data needs none of this — only *extraction* touches Instagram.

## Deployment

Browsing is read-only over the DB; extraction runs server-side and is gated by `EXTRACT_SECRET`.

**Backend → Fly.io** (`backend/fly.toml`, `backend/Dockerfile`). The local `places.db` is baked
into the image and seeded onto a persistent volume on first boot; production extractions then
write to that volume.

```bash
cd backend
# Optional: consolidate the WAL into places.db. The build also bakes the -wal
# sidecar, so the seed is complete either way.
sqlite3 places.db "PRAGMA wal_checkpoint(TRUNCATE);"
fly launch --no-deploy          # or `fly apps create joayo-api`
fly volumes create joayo_data --size 1 --region yyz  # must match fly.toml primary_region
fly secrets set ANTHROPIC_API_KEY=... APIFY_API_TOKEN=... KAKAO_REST_API_KEY=... \
                EXTRACT_SECRET=... CORS_ORIGINS=https://<your-app>.vercel.app
fly deploy                       # bakes the local places.db into the image
```

**Frontend → Vercel** (root `frontend/`). Set `NEXT_PUBLIC_BACKEND_URL` to the Fly URL, then
add that Vercel origin to the backend's `CORS_ORIGINS`.

### Pushing local extractions to prod

To extract locally (free, using cookies on your home IP) and add the results to the deployed
DB **without overwriting** its data or votes, upload your local `places.db` to the import endpoint:

```bash
curl -H "X-Admin-Token: $ADMIN_TOKEN" \
     -F "file=@backend/places.db" \
     https://joayo-api.fly.dev/api/admin/import-places
# → { "imported": 37, "merged": 4, "total": 1712 }
```

It inserts only places whose id isn't already present, then runs the dedup pass and recomputes
ambient-noise flags. Existing rows, votes, and prod-side extractions are preserved.

## Self-hosting

The repo is public but contains **no data and no secrets** (`places.db`, `.env`, and cookies are
gitignored). Clone it, provide your own keys via `.env`, and you have your own joayo.
