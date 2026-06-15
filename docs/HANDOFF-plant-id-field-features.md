# Handoff: backend changes for the «Что растёт» field client

**For:** the agent maintaining `historical-recipes`.
**From:** the agent building the Android client (`github.com/poker26/chto-rastet-android`).
**Status:** code written, compiles (`python -m py_compile` clean), **NOT committed, NOT deployed.** You decide how/when to integrate and ship.

All changes live in the working tree right now, intermixed with other in-flight
work (klex / essential-oils). A clean, self-contained patch of **only these
changes** is at `docs/plant-id-field-features.patch` (10 files, +545/−5). Apply
it onto a clean branch with `git apply docs/plant-id-field-features.patch`.

---

## What this adds (two features + one RFC)

These back the Android client that was just released. Both are **best-effort and
fail-closed** — if anything goes wrong they swallow the error and the normal
`/api/identify` response is unaffected.

### P3 — Russian names for non-corpus candidates
Pl@ntNet returns English common names. For species **not** in our herbarium the
app used to show English, while corpus plants show Russian — confusing. Correct
Russian names live in iNaturalist. New cached resolver fills `name_ru` (+
`inat_taxon_id`) for non-corpus candidates by Latin name.

- `services/inaturalist.py` → new `resolve_names_ru(db, latins)`: cache-first,
  one concurrent iNat lookup per cache miss, **definitive** no-match cached
  (`taxon_id=None`), **transient** failures NOT cached. ≤60 req/min budget,
  reuses the existing 429 backoff.
- Cache table `inat_taxon_cache` keyed by `latin_key` (= `_latin_key()` from
  `plant_matching`, the same genus+species author-stripped key used for herbarium
  dedup) → migration **012**.
- `routers/identify.py::_bridge` now collects `unmatched_latins`, calls
  `resolve_names_ru`, and sets `name_ru` / `inat_taxon_id` only on non-corpus
  candidates. (Corpus candidates already carry our Russian name.)

### P2 — server-side archive of every field upload
Each identification's photo + capture metadata is archived for debugging / future
training data, in a **dedicated MinIO bucket** kept apart from the corpus bucket,
plus a DB log row.

- `routers/identify.py`: `identify_plant` now accepts optional metadata form
  fields and calls `_archive(...)` after `_bridge`. New helpers `_archive(...)`
  (uploads photo to `field-uploads` at `uploads/{YYYY}/{MM}/{uuid}.jpg`, writes
  an `Identification` row; on any error → rollback + log, never raises) and
  `_parse_captured_at(raw)` (ISO-8601 or epoch sec/millis → `datetime`).
- Log table `identifications` → migration **013** (id, created_at, photo_key,
  engine, organ, lat/lng/geo_accuracy/captured_at, exif JSONB, device_* / os_* /
  app_version, top_latin/top_score, matched_plant_id FK→plants ON DELETE SET
  NULL, matched_count, remaining_requests, candidates JSONB).
- `config.py`: `minio_field_bucket: str = "field-uploads"` (env
  **`MINIO_FIELD_BUCKET`**).
- `services/minio.py`: new `ensure_bucket(bucket)` (idempotent) + optional
  `bucket=` param on `upload_file` (defaults to the app bucket — existing calls
  unchanged).

### P4 — RFC for MCP tool gaps
`docs/RFC-mcp-tools-v2.md` — design doc only, no code. Documents 4 gaps
(base64 images into `identify_plant`; `name_ru` via the shared cached resolver
above; `taxon_id`/`latin` into `find_observations_nearby`; a rejected standalone
resolver). Read at your leisure; nothing to deploy.

---

## File inventory

**Modified (tracked):**
- `backend/app/config.py` — `minio_field_bucket` setting.
- `backend/app/models/__init__.py` — register the two new models.
- `backend/app/services/minio.py` — `ensure_bucket` + `bucket=` param.
- `backend/app/routers/identify.py` — metadata form fields, `_archive`,
  `_parse_captured_at`, `name_ru`/`inat_taxon_id` wiring in `_bridge`.
- `backend/app/services/inaturalist.py` — `resolve_names_ru`.

**New (untracked):**
- `backend/app/models/inat_cache.py` — `InatTaxonCache`.
- `backend/app/models/identification.py` — `Identification`.
- `backend/alembic/versions/012_inat_taxon_cache.py`
- `backend/alembic/versions/013_identifications.py`
- `docs/RFC-mcp-tools-v2.md`
- `docs/plant-id-field-features.patch` (this handoff's patch)

---

## ⚠️ Migration chain caveat — READ BEFORE `alembic upgrade`

The committed head is `010_plant_name_modern`. The current local chain is:

```
010_plant_name_modern  (committed)
  └─ 011_essential_oils      (UNTRACKED — from the essential-oils in-flight work, NOT mine)
       └─ 012_inat_taxon_cache   (mine, down_revision="011_essential_oils")
            └─ 013_identifications (mine, down_revision="012_inat_taxon_cache")
```

My **012 is chained onto 011_essential_oils**, which is someone else's untracked
migration. Pick one:

- **If the essential-oils work ships with/before this:** nothing to do — `alembic
  upgrade head` runs `011 → 012 → 013` cleanly. 012/013 don't touch any oils
  tables; the coupling is purely the `down_revision` pointer.
- **If you ship this WITHOUT oils:** repoint 012's `down_revision` from
  `"011_essential_oils"` to `"010_plant_name_modern"` (one-line edit in
  `012_inat_taxon_cache.py`). 013 stays on 012. Then `alembic upgrade head`
  runs `012 → 013`.

Migrations are **manual** here (backend `CMD` is just uvicorn — no alembic in the
entrypoint).

---

## Deploy steps (your standard flow)

1. Integrate the changes (apply the patch on a clean branch, or cherry-pick the
   files), resolve the migration-chain caveat above.
2. Pre-flight: **no active `docker compose exec` jobs** on prod (server 1,
   `/opt/historical-recipes`) — they die on backend recreate.
3. `git pull --ff-only` → `docker compose build backend` → `docker compose up -d
   backend`; wait healthy. **Do NOT rebuild `worker`** (Temporal jobs).
4. `docker compose exec backend alembic upgrade head` (applies 012 + 013, and 011
   if included).
5. MinIO bucket: `ensure_bucket()` auto-creates `field-uploads` on first upload
   (best-effort). If the MinIO creds can't create buckets, pre-create it manually
   and/or set `MINIO_FIELD_BUCKET` in prod `.env`.

## Client contract / compatibility

The released APK already (a) sends the optional metadata form fields on
`POST /api/identify` and (b) reads `name_ru` / `inat_taxon_id` off each candidate.
This is **backward-compatible both ways**: the old backend ignores the extra
multipart fields, and the client falls back to the Latin name when `name_ru` is
absent. So there's no hard deploy ordering — but the features only light up once
this backend is live.

## ⚠️ The "502 on first try, success on retry" — ROOT CAUSE FOUND (your side)

**It is NOT an nginx timeout or a cold start.** Confirmed from prod logs:

```
app.services.plant_id WARNING Pl@ntNet request failed: ProxyError: 502 Bad Gateway
INFO: POST /api/identify/ HTTP/1.0" 200 OK
```

The chain:

1. `plant_id.identify()` reaches Pl@ntNet through the **trusttunnel egress proxy**
   (`settings.plantnet_proxy`). That proxy **intermittently returns 502 Bad
   Gateway** — flaky on the first hit, fine on the next. This is the entire
   mechanism behind "fails first, works on retry."
2. `identify()` does **no internal retry**: one `httpx.ProxyError` → it returns
   `{"error": "identification engine request failed: …502 Bad Gateway"}`.
3. `routers/identify.py::identify_plant` returns that dict **with HTTP status
   200** (no `HTTPException`). So the failure is invisible to any status-based
   client retry — it rides in the body's `error` string (which literally contains
   "502", which is why the user reported "502").

The in-compose nginx (`historical-recipes-nginx-1`, the `:8126` gateway) is **not**
the culprit: `/api/` already has `proxy_read_timeout 300s` and no upstream
keepalive (fresh connection per request, so no stale-keepalive 502s either).

### Recommended server-side fix (cleanest — hides the flaky proxy)

Add a small **retry inside `plant_id.identify()`** around the httpx call for
transient transport/gateway failures — `httpx.ProxyError` / `ConnectError` /
`ReadTimeout`, and HTTP `502/503/504`. 2–3 attempts, short backoff (~0.5s/1s),
all *inside* the request (well within the 300s nginx window). This makes the
endpoint return real candidates instead of a 200-with-error on the common case,
and the client never sees the flap at all. (Optionally also widen/stabilise the
trusttunnel proxy itself, but a retry is the durable fix.)

### Client-side mitigation — ALREADY SHIPPED

The app previously retried only on transient **HTTP status** codes, so a
200-with-error-body slipped through. As of `chto-rastet-android@efad32d` the
client also **retries transient errors carried in the 200 body** (matches
`engine request failed` / `bad gateway` / `proxyerror` / `5xx` / timeout; 4
attempts, ~1s/2s/4s). So users should stop seeing the spurious 502 even before
the server fix lands — but the server-side retry is still worth doing to remove
the added round-trips and to fix the MCP `identify_plant` path too.

New optional form fields on `POST /api/identify` (multipart), all nullable:
`lat`, `lng`, `geo_accuracy` (float), `captured_at` (ISO or epoch string),
`exif_json` (string), `device_model`, `device_manufacturer`, `os_version`
(string), `os_sdk` (int), `app_version` (string). Existing `images`, `organs`,
`limit` unchanged.
