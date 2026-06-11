# RFC: MCP toolset v2 — identification ergonomics & field data

Status: **Draft** (2026-06-06) · Author: design notes · Created: 2026-06-06
Supersedes nothing; **extends** `RFC-mcp-tools.md` (the 9-tool baseline that is now
built and live).

> The original RFC fixed a read-only retrieval surface over the corpus. Building
> the Android field client ("Что растёт") surfaced four concrete gaps in the
> *identification* path that the baseline did not anticipate: the photo tool only
> accepts URLs, its candidate names are English, observations are corpus-only, and
> there is no Latin→Russian name resolver. This RFC closes those gaps. All four
> are thin wrappers / shared-service fixes — no new retrieval pillar.

## Context: what changed since v1

The baseline toolset (`backend/app/mcp/server.py`) is live. Two new things drive
this RFC:

1. **A field client** (Android, sideloaded) that takes a photo, calls
   `POST /api/identify/`, and renders candidates with cross-links into the
   corpus. It exposed UX consequences of the API shape that an LLM-only consumer
   would hit too.
2. **A field-data capture feature** (separate work item, see the field-upload
   plan): every identification's photo + EXIF + geolocation + device/OS is
   archived to a dedicated MinIO bucket (`field-uploads`) and an `identification`
   log table, for debugging and future training data.

## The four gaps

### Gap 1 — `identify_plant` accepts only image URLs

Today `mcp__historical-recipes__identify_plant(image_urls, organs, limit)` wraps
`POST /api/identify/by-url` (`backend/app/routers/identify.py:92`). An MCP
consumer with a **local** image file has nowhere to put it — it must first host
the image at a public URL, which most agents cannot do. The REST multipart path
(`POST /api/identify/`) exists but is not exposed over MCP.

**Proposal:** accept inline image bytes over MCP.
- Add `images_base64: list[str]` (optional, mutually exclusive with `image_urls`)
  to the `identify_plant` tool.
- Server decodes and forwards as multipart to the existing
  `plant_id.identify()` service — same code path as the REST upload, no new
  identification logic.
- Size guard: reject > ~4 MB/image decoded (PlantNet caps at 5 images anyway);
  recommend clients downscale (the Android client already does — long side 1024,
  JPEG q80, ~200 KB).
- **Decision needed:** base64-over-MCP vs. exposing a presigned-PUT to the new
  `field-uploads` bucket and then identify-by-key. Lean **base64** for v2 (one
  round-trip, no bucket exposure to consumers); revisit presigned-PUT if payload
  size becomes a problem.

### Gap 2 — candidate `common_names` are English

`plant_id.py` normalizes PlantNet's `species.commonNames` straight through
(`backend/app/services/plant_id.py:52`); these are English. In the field client a
**non-corpus** candidate therefore shows an English name while a corpus candidate
shows Russian (`Plant.name` / `name_modern`) — confusing side by side. The MCP
`identify_plant` output has the same flaw.

**Proposal (shared-service fix, benefits REST + MCP equally):** enrich each
candidate with a Russian name from iNaturalist by Latin name.
- Reuse `inaturalist.resolve_taxon_photo()` /
  `GET /v1/taxa?q={latin}&locale=ru` (`backend/app/services/inaturalist.py:109`),
  which already returns a Russian `common_name`.
- Add `name_ru` to each candidate. Resolution order for display:
  corpus `name`/`name_modern` → iNat `name_ru` → English `common_names[0]` →
  Latin. (Corpus candidates already carry Russian, so iNat is only hit for
  non-corpus latins.)
- Resolve candidates **concurrently** (`asyncio.gather`) reusing the existing 429
  backoff; cap at the displayed `limit`.
- **Cache** Latin→Russian in a new `inat_taxon_cache` table keyed on
  `_latin_key` (`backend/app/services/plant_matching.py:140`) so repeated
  identifications don't re-hit iNat (≤60 req/min budget) and survive restarts.

### Gap 3 — `find_observations_nearby` is corpus-only

The tool requires a `plant_id` and routes to
`GET /api/plants/{plant_id}/observations`
(`backend/app/mcp/server.py:484`, router `plants.py:328`), which reads
`Plant.inat_taxon_id`. A species that was **identified but is not in our corpus**
has no `plant_id`, so its live observations are unreachable — exactly the
common case for a field photo of something we haven't catalogued. The v1 RFC's
open question ("accept plant_id vs taxon_id vs both — lean both") was built
plant_id-only; this closes it.

**Proposal:** accept `taxon_id` **or** `latin` in addition to `plant_id`.
- `plant_id` → resolve to `inat_taxon_id` (today's path).
- `taxon_id` → pass straight to `inaturalist.find_observations()`.
- `latin` → resolve via `resolve_taxon_photo()` (cached per Gap 2) to a
  `taxon_id`, then observations.
- The identify candidate payload already exposes the Latin; with Gap 2's cache
  the taxon_id is effectively free. Wire a `taxon_id` into the candidate payload
  when known, so the agent can chain identify → observations without a corpus
  hit.

### Gap 4 — no standalone Latin→Russian name resolver

Considered (`resolve_name(latin)`), **rejected for v2**. Once Gap 2 returns
`name_ru` on identify candidates and Gap 3 accepts a Latin, a separate resolver
buys nothing. Revisit only if a consumer needs Russian names outside the
identify/observations flow.

## Tier → tool map (metering)

No new tier. All four changes stay within the existing `identify` and `live`
tiers recorded by `_record_usage()` (`backend/app/mcp/server.py:77`); the dormant
metering seam is unchanged.

| Tier | Affected tool | Change |
|---|---|---|
| identify | `identify_plant` | +`images_base64` input, +`name_ru`/`taxon_id` in output |
| live | `find_observations_nearby` | +`taxon_id` / `latin` inputs |

## What is deliberately NOT in scope

- The `field-uploads` bucket and `identification` log table are **not** exposed
  over MCP. They are an internal debugging/training capture (server + Android
  client), not a consumer retrieval surface. No "read my identification history"
  tool — history is a per-device client concern.
- No write/ingest/admin tools (unchanged from v1).
- No LLM hosting/proxy (unchanged from v1).

## Work items

1. **Gap 2 first** (highest value, unblocks the field client + fixes MCP output):
   `name_ru` enrichment in `plant_id.identify()` via cached iNat taxon lookup;
   add `inat_taxon_cache` table (migration).
2. **Gap 3:** widen `find_observations_nearby` + the `/observations` router to
   accept `taxon_id` / `latin`; thread `taxon_id` into identify candidates.
3. **Gap 1:** add `images_base64` to the `identify_plant` MCP tool, forwarding to
   the multipart service path.
4. Update `AGENTS.md` tool descriptions: identify accepts inline images and
   returns Russian names; observations accept a bare taxon/latin.

## Dependencies / blockers

- iNat rate limit (≤60 req/min, ~1 req/1.6 s pacing used by the enrichment pass).
  The `inat_taxon_cache` is the mitigation; identify resolves ≤`limit` latins per
  call, concurrently, so worst case is small and warms the cache.
- PlantNet free tier 500 IDs/day — unchanged; this RFC adds no PlantNet calls.

## Open questions

- Gap 1 transport: base64 inline (leaning) vs. presigned-PUT to `field-uploads`.
  Decide if/when payload size bites.
- Cache invalidation: iNat Russian common names are stable; a TTL is likely
  unnecessary, but consider a periodic refresh aligned with `enrich_plants_inat`.
- Should `name_ru` resolution also backfill the corpus (promote to `name_modern`
  for matched plants whose `name_modern` is null)? Lean no — keep identify
  read-only; corpus promotion stays in the enrichment pass.
