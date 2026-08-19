# The place-record contract

**v0.1 · 2026-08-19**

One record schema, owned by neither app. Joayo emits it, KFP enriches it, Taste Stew consumes it,
and none of them needs to know the others exist. The dataset becomes a parameter; "Korea and Japan
restaurants" is one instance of it.

Companion files: `place-record.schema.json` (JSON Schema, machine-checkable) and
`validate_places.py` (runs it, plus the rules a schema can't express).

---

## First: why there is no database in this picture

You asked whether Taste Stew should get a DB layer, or read Joayo's directly. Measured, the
architecture has already answered:

| | |
|---|---|
| corpus today | 1,738 records, **163 KB gzipped** (387 bytes/record) |
| with Joayo's 908 merged | 2,646 records, **252 KB gzipped** |
| at 10,000 records | ~950 KB gzipped — still one cacheable fetch |

And Taste Stew's filtering is entirely client-side over the whole array:

```ts
data.filter((r) => passes(r, filters) && inRegion(r, region) && passesDetour(r, detour))
```

`inRegion` is a geometric predicate — point-in-corridor, point-in-isochrone-polygon — and
`cuisineOptions()` needs the full set to compute facet counts that exclude their own filter. **None
of that pushes down to SQL.** A query API would have to return the entire corpus on every load, so
a database would be a slower, less available way to deliver the same array. The browser is the
query engine; the CDN is the right delivery.

Reading Joayo's DB directly isn't available anyway: it's SQLite on a Fly volume, and Taste Stew is
a static SPA on Vercel. There is no network path to a file on another host's disk — you'd go
through Joayo's HTTP API, which is the option the numbers above already reject.

**The one thing a DB genuinely buys is writes** — ratings and wishlist synced across devices rather
than stranded in `localStorage`. That is a small, separate API (a place id and a mark), not a
corpus read. Joayo's `PlaceMark` table is already the right shape for it, and currently has 0 rows.
Worth doing when either app has a second user; not part of this contract.

**So the seam is a build artifact.** Joayo writes `places.json`; Taste Stew serves it from a CDN
exactly as it serves `restaurants.json` today. Population without coupling — Fly can be down and
the map still works.

---

## The record

```jsonc
{
  "id": "kfm_1c4839bf3b",              // opaque, stable, NEVER reissued
  "name": "Gebangsikdang",
  "name_local": "게방식당",
  "lat": 37.5665, "lng": 126.9780,
  "address": "서울 중구 을지로 12길 22",
  "country": "KR",                      // ISO 3166-1 alpha-2
  "tz": "Asia/Seoul",                   // IANA. REQUIRED if any hours field is present
  "city": "Seoul",
  "neighborhood": "Euljiro",
  "category": "eat",                    // eat | see | do | shop | service
  "subcategory": "restaurant",
  "cuisine": "Korean (hansik)",         // only meaningful when category = "eat"
  "tags": ["hidden gem", "cash only"],
  "summary": "Ganjang gejang counter, one sitting a day.",
  "tips": "Reserve by phone; lunch only.",

  "hours": {                            // ABSENT = unknown. See the tri-state rule below.
    "days": [0,1,2,3,4],                // 0=Mon … 6=Sun
    "by_day": [[[660,900]],[],[],[],[],[],[]],
    "text": "11:00–15:00, closed Sat–Sun",
    "irregular": true                   // present only when true
  },

  "rating": { "value": 4.3, "scale": "kr", "source": "blended" },
  "price_tier": 2,                      // 1–4
  "awards": [ { "body": "michelin", "kind": "star", "value": 1, "year": 2026 } ],

  "source": {                           // REQUIRED — provenance is not optional
    "kind": "social",                   // curated | social | manual
    "name": "Michelin Korea 2026",      // workbook, creator handle, or "hand-entered"
    "urls": ["https://instagram.com/p/…"],
    "contributors": [ { "handle": "@someone", "platform": "instagram" } ],
    "first_seen": "2026-03-14"
  },

  "location_confidence": "poi_id",      // address | poi_id | name_search | manual | none
  "refs": { "google_place_id": "ChIJ…", "kakao_poi_id": "1791830911" },

  "needs_review": false,
  "is_context": false
}
```

---

## Field ownership

Who is allowed to write each field. A consumer that writes producer fields, or a producer that
invents enricher fields, is how two systems start disagreeing about the same venue.

| field | producer (Joayo) | enricher (KFP) | consumer (Taste Stew) |
|---|---|---|---|
| `id` | **mints** | preserves | read-only |
| `name`, `name_local` | writes | may correct | read-only |
| `lat` / `lng` | writes | **may re-derive from `address`** | read-only |
| `address` | writes | **authoritative; may correct** | read-only |
| `country`, `tz`, `city`, `neighborhood` | writes | may correct | read-only |
| `category`, `subcategory` | writes | may correct | read-only |
| `cuisine`, `rating`, `price_tier`, `awards` | — | **writes** | read-only |
| `hours` | — | **writes** | read-only |
| `source` | **writes** | appends only | read-only |
| `location_confidence` | writes | **may raise or lower** | read-only |
| `refs` | writes | writes | read-only |
| `needs_review`, `is_context` | writes | may clear | read-only |

Taste Stew writes nothing. Its user state — shortlist, landmarks, marks — is keyed on `id` and
lives outside the record, which is what keeps the corpus a build artifact.

---

## The rules a schema cannot express

### 1. ⛔ Absent ≠ empty ≠ false

The single most important rule, and the one both codebases already have scar tissue about.

- **Key absent** = *unknown*. Nobody has established this.
- **Key present with an empty value** = *known to be empty*. `hours.days: []` means "closed every
  day of the week", which is a claim.
- **A boolean flag** is emitted **only when true** (`irregular`, `needs_review`, `is_context`).
  Consumers must test presence, never compare to `false`.

Taste Stew's `openUnknown` toggle exists precisely because "we don't know the hours" and "it's shut"
are different answers, and a consumer that conflates them under-reports a venue the user could have
visited. Producers must never emit an empty array to mean "no data".

### 2. `tz` is required whenever `hours` is present

⛔ Not a nicety. Taste Stew's `kstNow()` currently does `now.getTime() + 9 * 3600000` — a hardcoded
UTC+9. Feed it a Lisbon record and open-now does not error; it confidently answers the wrong
question. Making `tz` a contract requirement is what stops that from ever shipping: a record with
hours and no timezone is invalid, and the consumer resolves "open now" against the record's own zone
rather than a constant.

### 3. `address` is authoritative; `lat`/`lng` are derived

When both are present and disagree, the address wins on re-derivation. This is KFP's standing rule
— *never geocode a name; geocode an address* — promoted from a convention to a contract term. It is
also why `address` is the field a producer must fight to populate: without it there is no
independent channel to check a coordinate against, and every verification collapses into asking the
same provider the same question twice.

### 4. `location_confidence` states how the coordinate was actually obtained

| value | meaning |
|---|---|
| `address` | geocoded from a street address |
| `poi_id` | resolved to a provider's business listing (Kakao POI, Google Place) |
| `name_search` | a name was searched and the top hit taken |
| `manual` | placed by a human |
| `none` | no coordinate |

⛔ It may only be raised by a **new channel with different provenance**. Re-running the same lookup
and getting the same answer is not evidence and must not upgrade `name_search` to anything. Joayo's
current corpus is `poi_id` (a Kakao business listing) reached via `name_search` — record the weaker
of the two until an address confirms it.

### 5. `id` is minted once and never reissued

A retired venue keeps its id forever; a new venue never inherits one. Consumers may store user state
(shortlist, marks, share links) against ids, so a reissued id silently transfers someone's saved
place to a different restaurant. KFP's `record_id` registry is this rule already; the contract just
makes it binding on every producer.

### 6. Unknown fields and unknown enum values pass through

A consumer meeting `category: "stay"` must render the record and ignore the category rather than
drop it or throw. Forward compatibility is what lets one side ship a new category without a
lockstep release.

### 7. `source.kind` is what carries the quality bar

`curated` and `social` records live in one corpus and are held to different standards. That
distinction belongs in the data, not in which server holds it — which is what makes a single store
possible without either bar collapsing into the other. A consumer may filter, style, or badge on
`source.kind`; it must not assume one is absent.

---

## What a consumer may assume

Exactly this, and nothing else:

- `id`, `name`, `category`, `source` are present on every record.
- `lat` and `lng` are either both present or both absent.
- If `hours` is present, `tz` is present.
- `id` is unique within a payload and stable across payloads.

Everything else is optional and its absence means unknown. In particular a consumer must **not**
assume `cuisine`, `rating`, `price_tier`, `awards`, `address` or `hours` exist — those are exactly
the fields a social-sourced record arrives without, and a filter built on them must offer the
"include unknown" escape hatch or it will silently delete every such record the moment it is
touched.

---

## Versioning

The payload is an envelope, not a bare array:

```jsonc
{ "contract": "1.0", "generated": "2026-08-19T20:00:00Z", "count": 2646, "places": [ … ] }
```

Minor version = additive fields only; consumers ignore what they don't know. Major version = a
field changed meaning, and consumers must gate on it. Taste Stew's current payload is a bare array,
so adopting this is one unwrapping line at `App.tsx:97`.

---

## Migration from what exists today

| today (Taste Stew) | contract | note |
|---|---|---|
| `country: 'Korea' \| 'Japan'` | `country: "KR" \| "JP"` | union → ISO code; ~8 sites |
| `name_kr` | `name_local` | country-neutral |
| `disp_val` / `disp_scale` / `disp_src` | `rating: {value, scale, source}` | 15 sites |
| `stars` / `mich_type` / `distinction` | `awards[]` | generalizes beyond Michelin |
| `open_days` / `hours` / `hours_by_day` / `hours_text` / `irregular` | `hours: {days, by_day, text, irregular}` | same semantics, nested |
| `group` / `featured_in` / `source_count` | `source` | `source_count` becomes derived |
| `cluster` / `cluster_stars` / `cluster_*` | *(build-time only)* | clustering is an ETL concern, not a wire field |
| `place_id` | `refs.google_place_id` | |
| `fallback_name_only` | *(drop)* | an artifact of a missing address; the address fixes it |

| today (Joayo) | contract |
|---|---|
| `location_name` | `name` |
| `native_name` | `name_local` |
| `labels` | `tags` |
| `insider_tips` | `tips` |
| `geocoder_place_id` | `refs.kakao_poi_id` |
| `address` *(new, A1)* | `address` |
| `primary_author` / `all_authors` / `source_urls` | `source.contributors` / `source.urls` |
| `see_visit` | `see` |
| `guide` | *(drop — a person, not a place)* |
| `is_place: false` rows | *(not places; a separate `things` payload keyed on venue id)* |

---

## Open questions this does not settle

- **Does KFP's corpus move into Joayo's store?** The contract works either way — one producer or
  two, merged at build time. But if Joayo becomes the store, the eight workbooks want importing and
  KFP becomes an enrichment pass rather than a pipeline that owns its own truth. That is the biggest
  remaining decision and the contract deliberately does not force it.
- **Multi-category records.** A food market is `eat` and `shop`. v0.1 is single-valued with `tags`
  carrying the second sense, because multi-valued categories complicate the facet, the URL encoding
  and the marker colour at once. Revisit if the market count grows beyond the ~14 records it affects
  today.
- **`things`** — Joayo's 1,221 dishes/products, 920 of which resolve to a venue across 360 distinct
  places. They are not places and do not belong in this array. They want their own payload keyed on
  `place_id`, which is a v0.2 problem.
