# Social Post Data Extraction Tool — Project Spec

## Goal
A tool that takes a list of social post URLs (Instagram, TikTok, YouTube, Facebook) and extracts structured data — description, location, tagged accounts, audio content/transcript, and comments — into a spreadsheet, with location data also feedable into a map. Output should be organized/categorizable, not just a raw dump.

## Scale & priorities
- Volume: dozens to low hundreds of posts per run, **recurring** (not one-off) — build a real reusable tool, not a throwaway script.
- Build order: **YouTube first** (official API, no scraping needed), then Instagram, then TikTok and Facebook.

## Data sources per platform

### YouTube — official API
- **YouTube Data API v3**: title/description, tags, channel info, comments.
- Captions/transcript: via API if creator-provided, otherwise a transcript-fetching library.
- No scraping needed. Cleanest, most stable source.

### Instagram — Apify actors
- `apify/instagram-reel-scraper` — caption, hashtags, mentions, **tagged users**, **music info + spoken-word transcript** (recently added), latest ~10 comments w/ replies & timestamps.
- `apify/instagram-post-scraper` — same idea for non-Reel posts (images/carousels), no transcript field since no spoken audio.
- Pricing: ~$0.0026–0.0027 per result on free tier — trivial at this volume.
- **No location field in either actor's output.**

### TikTok — Apify actors (likely 2 actors needed)
- `clockworks/tiktok-scraper` (the long-standing, widely-used one) — caption, hashtags, @mentions-in-caption, music info, comments, engagement.
- Transcript is **not bundled** in the main scraper — needs a separate dedicated transcript actor (several exist; one bundling full video data + optional transcript: `sociavault/tiktok-video-info-scraper`). **Not yet pinned to a specific one — decide during implementation based on a test run.**
- TikTok has no structured "tagged accounts" feature like Instagram — only @mentions inside the caption text.
- **No location field.**

### Facebook — Apify actors
- `apify/facebook-posts-scraper` — caption/text, reactions, **video transcripts (native, no separate actor needed)**, images, external links, "collaborators" field (closest equivalent to tagged accounts, not a clean structured list).
- `apify/facebook-comments-scraper` — comments by post URL, run as a second pass after collecting post URLs.
- **No confirmed location field** in standard output.

## Cross-platform gap: location
**No platform's standard scraper/API exposes a structured location/place-tag field.** This is consistent across all four, not a per-platform engineering shortfall. Options to handle it:
1. Leave it as a manual/optional field, filled in only for posts where it matters.
2. Infer from caption text via an LLM pass (heuristic, not authoritative).
3. Browser-based spot-check for specific posts (Claude in Chrome / Cowork) — narrow, occasional use, not part of the core pipeline.

## Proposed unified spreadsheet schema
One row per post, consistent across platforms:

| Column | Notes |
|---|---|
| platform | youtube / instagram / tiktok / facebook |
| url | original post URL |
| author/account | username or channel |
| caption_description | full text |
| hashtags | list |
| mentions_or_tagged_accounts | list — structured tags where available (IG), else @mentions parsed from caption |
| audio_music_info | song/artist or "original audio" flag, where available |
| transcript | spoken-word transcript, where available |
| top_comments | a few representative comments, with author/timestamp |
| location_raw | nullable — filled manually/heuristically, not guaranteed |
| location_lat / location_lng | filled after geocoding location_raw, if present |
| category_tags | your own organizational taxonomy — TBD |
| date_posted | |
| date_extracted | |

## Pipeline architecture
```
List of post URLs (per platform)
   → platform-specific fetcher (YouTube API call / Apify actor run)
   → raw JSON per post
   → normalization layer → unified schema rows above
   → xlsx export
   → geocode location_raw where present → lat/lng
   → map-ready output
```

## What's still open / needs deciding in Claude Code
- Pin a specific TikTok transcript actor after a test run (cost + reliability check).
- Decide geocoding provider (Google Geocoding API, Nominatim/OpenStreetMap, etc.).
- Decide categorization taxonomy for `category_tags` (manual rules vs. LLM-assisted tagging pass).
- Decide map output format (static export, Google My Maps import, or a custom map view).
- Credentials needed: Apify API token, YouTube Data API key, geocoding API key (if using a paid provider) — store as environment variables, not in code.

## Recommended build order
1. YouTube fetcher → normalized rows → xlsx (prove the schema works end-to-end on the easiest source).
2. Instagram via Apify Reel/Post Scraper → same normalization.
3. Facebook via Apify Posts + Comments Scraper.
4. TikTok via Apify scraper + transcript actor (pick one).
5. Geocoding pass + map export.
6. Categorization/organization pass.
