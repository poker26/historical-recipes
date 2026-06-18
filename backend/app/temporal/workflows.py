"""Temporal workflows orchestrating the book pipeline.

Workflow code must be deterministic, so it contains NO I/O — it only sequences
activity calls and branches on their returned metadata.  Heavy imports
(services, DB, httpx) are pulled in through ``imports_passed_through`` so the
workflow sandbox doesn't try to re-import / validate them.
"""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import (
        convert_activity,
        classify_activity,
        extract_activity,
        cleanup_activity,
        translate_activity,
        analyze_activity,
        extract_recipes_activity,
        extract_plant_entries_activity,
        extract_vocabulary_activity,
        normalize_corpus_activity,
        extract_oils_activity,
        normalize_oils_activity,
        medical_vocab_batch_activity,
        normalize_medical_activity,
        match_ingredients_activity,
        index_activity,
        enrich_inat_activity,
        klex_list_activity,
        klex_download_activity,
        ping_activity,
        maintain_pool_activity,
    )
    from app.temporal.cleanup_activities import (
        run_enrichment_activity,
        run_backfill_activity,
        run_rename_activity,
        fill_latin_activity,
        biotope_canon_activity,
        conflict_check_activity,
        recipe_relink_activity,
        genus_assembly_activity,
        edible_safety_activity,
    )
    from app.temporal.quest_activities import (
        osm_ingest_region_activity,
        build_place_sets_activity,
    )
    from app.temporal.monograph_activities import generate_monographs_activity

# Generous per-step ceilings: the pre-reform 1M-char book took >60 min on a
# single LLM step, so long steps get up to 3h.  Retries are bounded and skip
# non-transient errors (bad input raises ValueError → no point retrying).
_LONG = timedelta(hours=3)
_SHORT = timedelta(minutes=20)
# Heartbeat timeout: without it, a worker restart mid-activity orphans the
# attempt, and Temporal only retries after start_to_close elapses (up to 3h) —
# a long, confusing stall. The long OCR/LLM steps heartbeat per page/chunk/
# recipe, so a heartbeat timeout lets Temporal detect a dead worker and retry
# within minutes. It must comfortably exceed the gap between heartbeats (one
# LLM call can take up to the 600s httpx timeout, plus 429 retry backoff).
_HEARTBEAT = timedelta(minutes=15)
_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    # A big book (e.g. the 105-chunk «Ботанический Словарь») extracts ~25 chunks
    # per 3h start_to_close window, so it needs several attempts to finish; the
    # chunk-resumable activities make each retry cheap (skip done chunks), so a
    # generous cap just lets large inputs complete rather than dying one chunk
    # short. Non-resumable/transient failures still stop fast via the backoff.
    maximum_attempts=10,
    non_retryable_error_types=["ValueError"],
)


# Ordered pipeline definitions, one per domain: (step, activity, start_to_close,
# heartbeat).  A single source of truth so the workflow and the API agree on
# step order.  heartbeat_timeout is set only for steps that heartbeat frequently;
# classify is trivially fast and match_ingredients heartbeats just once, so they
# stay None to avoid false heartbeat timeouts.
#
# The two domains share the first four steps (ingest + text prep) and diverge at
# structuring: recipes go analyze -> extract_recipes -> match_ingredients, while
# herbalism additionally parses plant monographs.  Herbalism books also contain
# genuine recipes (decoctions, teas, herbal collections), so the herbalism flow
# now ALSO runs analyze -> extract_recipes -> match_ingredients to capture those
# medicinal preparations.  Ordering: analyze precedes extract_recipes (it produces
# the recipe-block sections), and extract_plant_entries precedes match_ingredients
# so the plant monographs already exist when recipe ingredients are linked to
# plants — maximising in-book cross-linking.  Recipes from a herbalism book stay
# distinguishable by their book's domain.
PIPELINE_STEPS_RECIPES = [
    ("convert", convert_activity, _LONG, None),
    ("classify", classify_activity, _SHORT, None),
    ("extract", extract_activity, _LONG, _HEARTBEAT),
    ("cleanup", cleanup_activity, _LONG, _HEARTBEAT),
    ("translate", translate_activity, _LONG, _HEARTBEAT),
    ("analyze", analyze_activity, _LONG, _HEARTBEAT),
    ("extract_recipes", extract_recipes_activity, _LONG, _HEARTBEAT),
    ("match_ingredients", match_ingredients_activity, _SHORT, None),
    ("index", index_activity, _LONG, _HEARTBEAT),
]
PIPELINE_STEPS_HERBALISM = [
    ("convert", convert_activity, _LONG, None),
    ("classify", classify_activity, _SHORT, None),
    ("extract", extract_activity, _LONG, _HEARTBEAT),
    ("cleanup", cleanup_activity, _LONG, _HEARTBEAT),
    ("translate", translate_activity, _LONG, _HEARTBEAT),
    ("analyze", analyze_activity, _LONG, _HEARTBEAT),
    ("extract_plant_entries", extract_plant_entries_activity, _LONG, _HEARTBEAT),
    ("extract_recipes", extract_recipes_activity, _LONG, _HEARTBEAT),
    ("match_ingredients", match_ingredients_activity, _SHORT, None),
    ("index", index_activity, _LONG, _HEARTBEAT),
    # Tail step: pull iNat photos for this book's new plants. Best-effort — the
    # activity never raises, so a throttled/offline iNat can't fail the pipeline.
    ("enrich_inat", enrich_inat_activity, _LONG, _HEARTBEAT),
]
# Reference-normalizer domain: a property-first reference (e.g. a phytochemistry
# monograph) whose product is a controlled vocabulary + a corpus-wide normalize,
# NOT per-book entities. Shares the text-prep front, then diverges:
# extract_vocabulary builds/grows the compound vocab (+ normalized PlantCompound
# occurrences), normalize_corpus maps every plant's free-text compounds to it.
# No analyze/extract_recipes — there are no recipes to pull from a chemistry book.
PIPELINE_STEPS_REFERENCE = [
    ("convert", convert_activity, _LONG, None),
    ("classify", classify_activity, _SHORT, None),
    ("extract", extract_activity, _LONG, _HEARTBEAT),
    ("cleanup", cleanup_activity, _LONG, _HEARTBEAT),
    ("translate", translate_activity, _LONG, _HEARTBEAT),
    ("extract_vocabulary", extract_vocabulary_activity, _LONG, _HEARTBEAT),
    ("normalize_corpus", normalize_corpus_activity, _SHORT, None),
    ("index", index_activity, _LONG, _HEARTBEAT),
]
# Aromatherapy domain: an oil-first reference (a chapter per named essential oil)
# whose product is the essential-oil entities + their use-facts, normalized onto
# the SAME action/indication vocabularies as plants. Shares the text-prep front,
# then extract_oils builds the oil monographs and normalize_oils maps their uses.
# No analyze/extract_recipes — there are no recipes in an oils reference.
PIPELINE_STEPS_AROMATHERAPY = [
    ("convert", convert_activity, _LONG, None),
    ("classify", classify_activity, _SHORT, None),
    ("extract", extract_activity, _LONG, _HEARTBEAT),
    ("cleanup", cleanup_activity, _LONG, _HEARTBEAT),
    ("translate", translate_activity, _LONG, _HEARTBEAT),
    ("extract_oils", extract_oils_activity, _LONG, _HEARTBEAT),
    ("normalize_oils", normalize_oils_activity, _SHORT, None),
    ("index", index_activity, _LONG, _HEARTBEAT),
]


def steps_for_domain(domain: str):
    d = (domain or "").lower()
    # fungi (mushroom guides) reuse the entire herbalism pipeline — same monograph
    # parsing, recipe extraction and cross-linking — and are distinguished only by
    # the kingdom tag the extractor stamps on each row (see extract_plant_entries).
    if d in ("herbalism", "fungi"):
        return PIPELINE_STEPS_HERBALISM
    if d == "reference":
        return PIPELINE_STEPS_REFERENCE
    if d == "aromatherapy":
        return PIPELINE_STEPS_AROMATHERAPY
    return PIPELINE_STEPS_RECIPES


def step_names_for_domain(domain: str) -> list[str]:
    return [s[0] for s in steps_for_domain(domain)]


# Default (recipes) step order — kept for back-compat with callers that import
# STEP_NAMES / PIPELINE_STEPS without a domain.
PIPELINE_STEPS = PIPELINE_STEPS_RECIPES
STEP_NAMES = [s[0] for s in PIPELINE_STEPS]


@workflow.defn
class BookPipelineWorkflow:
    """Run a book through the full 7-step pipeline, end to end.

    Each step is durable: if the worker restarts, Temporal replays history and
    resumes from the last completed activity rather than from scratch.

    ``start_step`` lets a run begin partway through (e.g. resume a book whose
    earlier steps are already committed to the DB) so expensive work like a
    multi-hour cleanup/translate isn't repeated.  Steps before it are skipped;
    the activities are self-loading from the DB, so the resumed run picks up
    the state the skipped steps left behind.
    """

    @workflow.run
    async def run(self, book_id: str, start_step: str = "convert", domain: str = "recipes") -> dict:
        steps = steps_for_domain(domain)
        step_names = [s[0] for s in steps]
        if start_step not in step_names:
            start_step = step_names[0]  # first step (convert) — full run from scratch
        start_idx = step_names.index(start_step)

        results: dict = {"_started_at_step": start_step, "_domain": domain}
        for name, fn, timeout, heartbeat in steps[start_idx:]:
            workflow.logger.info(f"pipeline step start: {name}")
            kwargs = {"start_to_close_timeout": timeout, "retry_policy": _RETRY}
            if heartbeat is not None:
                kwargs["heartbeat_timeout"] = heartbeat
            res = await workflow.execute_activity(fn, book_id, **kwargs)
            results[name] = res
            workflow.logger.info(f"pipeline step done: {name} -> {res}")

        return results


@workflow.defn
class BookDispatcherWorkflow:
    """Server-side corpus dispatcher — keeps the book pipeline pool full.

    Durable replacement for the laptop-bound manual tick loop: it lives on the
    worker, so it keeps draining the ``uploaded`` queue regardless of whether any
    human session is awake, and survives worker restarts (Temporal resumes it).

    Each tick calls ``maintain_pool_activity`` which tops the in-flight
    ``book-pipeline-*`` count back up to ``target`` (≤ ``burst`` new starts per
    tick to avoid a dispatch-replay CPU spike). It ignores the user's separate
    ``klex-herb-*`` mirror worker entirely. Terminates when the corpus is fully
    drained (nothing running, no ``uploaded`` left); ``continue_as_new`` every
    ``iters_before_renew`` ticks keeps the history bounded.
    """

    @workflow.run
    async def run(self, target: int = 8, burst: int = 4, interval_seconds: int = 45,
                  iters_before_renew: int = 80, dispatched_total: int = 0) -> dict:
        for _ in range(iters_before_renew):
            try:
                r = await workflow.execute_activity(
                    maintain_pool_activity,
                    args=[target, burst],
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_RETRY,
                )
            except Exception as e:
                # A single tick can exhaust its retries during a DB/network outage
                # (e.g. the Supabase pooler dropping the connection for ~minutes).
                # A long-running dispatcher must NOT die from one bad tick — log it,
                # wait, and try again on the next tick. This is the difference between
                # self-healing and silently stalling the whole corpus for days.
                workflow.logger.warning(f"pool tick failed, retrying next tick: {e}")
                await workflow.sleep(timedelta(seconds=interval_seconds))
                continue
            dispatched_total += len(r.get("started", []))
            if r.get("done"):
                return {"finished": True, "dispatched_total": dispatched_total,
                        "intermediate_left": r.get("intermediate_left", 0)}
            await workflow.sleep(timedelta(seconds=interval_seconds))
        # Trim history: carry the running total into a fresh execution.
        workflow.continue_as_new(
            args=[target, burst, interval_seconds, iters_before_renew, dispatched_total])


@workflow.defn
class InatEnrichmentWorkflow:
    """Corpus-wide iNaturalist enrichment as a durable, resumable sweep.

    Replaces the ad-hoc detached shell loop that kept dying on SSH drops, 429
    storms and backend redeploys. Runs paced batches (each a separate, retriable
    activity) until no unsynced plants remain — surviving worker restarts via
    Temporal. Trigger manually (web button) or on a schedule.

    Termination: stops when ``remaining`` hits 0 (definitive no-matches are
    marked synced by the activity, so it actually drains), when a batch processes
    nothing, or — under sustained throttling where a whole batch is 429'd (no
    resolves, no definitive no-matches) — after a few backed-off stalls.
    """

    @workflow.run
    async def run(self, batch_size: int = 120, max_batches: int = 200) -> dict:
        totals = {"processed": 0, "taxa_resolved": 0, "photos_set": 0,
                  "no_match": 0, "throttled": 0, "batches": 0, "remaining": None}
        stall = 0
        for _ in range(max_batches):
            res = await workflow.execute_activity(
                enrich_inat_activity,
                args=[None, batch_size, False],
                start_to_close_timeout=_LONG,
                heartbeat_timeout=_HEARTBEAT,
                retry_policy=_RETRY,
            )
            totals["processed"] += res.get("processed", 0)
            totals["taxa_resolved"] += res.get("taxa_resolved", 0)
            totals["photos_set"] += res.get("photos_set", 0)
            totals["no_match"] += res.get("no_match", 0)
            totals["throttled"] += res.get("throttled", 0)
            totals["batches"] += 1
            totals["remaining"] = res.get("remaining")

            if res.get("remaining") in (0, -1) or res.get("processed", 0) == 0:
                break
            # Whole batch throttled — iNat is rate-limiting hard. Back off and
            # bail after repeated stalls so we don't spin indefinitely.
            if res.get("taxa_resolved", 0) == 0 and res.get("no_match", 0) == 0:
                stall += 1
                if stall >= 4:
                    break
                await workflow.sleep(timedelta(minutes=3))
            else:
                stall = 0
        return totals


@workflow.defn
class MedicalNormalizerWorkflow:
    """Corpus-wide medical normalizer as a durable, resumable sweep.

    The medical sibling of ``InatEnrichmentWorkflow`` — the right home for the long,
    429-prone Phase-A LLM canonicalization. Phase A grows the action + indication
    vocabularies from the distinct ``action_raw`` / ``indications`` strings the
    corpus already holds; Phase B then relinks every ``PlantMedicinalUse`` to them.

    Each vocab batch is a separate retriable activity, so a provider rate-limit is
    just a retried attempt (not a lost batch), and progress is visible per-activity
    in the Temporal UI. Per axis it loops until the uncovered backlog hits 0 or
    stops shrinking (terms the model declines to canonicalize never become covered,
    so ``remaining``-stall is the real terminator). Phase B runs once at the end.
    """

    @workflow.run
    async def run(self, batch_size: int = 80, max_batches: int = 100,
                  normalize: bool = True) -> dict:
        totals: dict = {"actions_created": 0, "actions_batches": 0,
                        "indications_created": 0, "indications_batches": 0}
        for axis in ("actions", "indications"):
            # Sweep the uncovered backlog by offset rather than always re-serving the
            # sorted head: an atom the model declines to map sits at the head forever
            # and would block every mappable atom behind it. We walk offset across a
            # full pass, and only stop the axis when a WHOLE sweep fails to shrink the
            # remaining count (the leftovers are genuinely unmappable) — not on a
            # single unproductive batch, which is normal mid-sweep.
            offset = 0
            sweep_baseline = None  # uncovered count at the start of the current sweep
            for _ in range(max_batches):
                res = await workflow.execute_activity(
                    medical_vocab_batch_activity,
                    args=[axis, batch_size, offset],
                    start_to_close_timeout=_LONG,
                    heartbeat_timeout=_HEARTBEAT,
                    retry_policy=_RETRY,
                )
                totals[f"{axis}_created"] += res.get("created", 0)
                totals[f"{axis}_batches"] += 1
                remaining = res.get("remaining", 0)
                total = res.get("total", 0)
                totals[f"{axis}_remaining"] = remaining
                if total == 0 or remaining == 0:
                    break
                if sweep_baseline is None:
                    sweep_baseline = total
                offset += batch_size
                if offset >= total:
                    # completed a full pass over the backlog
                    if remaining >= sweep_baseline:
                        break  # a whole sweep mapped nothing new — the rest is unmappable
                    sweep_baseline = remaining
                    offset = 0

        if normalize:
            totals["normalize"] = await workflow.execute_activity(
                normalize_medical_activity,
                start_to_close_timeout=_SHORT,
                retry_policy=_RETRY,
            )
        return totals


@workflow.defn
class KlexHerbDownloadWorkflow:
    """Durable, resumable mirror of klex.ru/razdel/herb into a dedicated MinIO bucket.

    The download is long (~585 books, tens of GB), so doing it as a workflow gives
    us crash-safe resumption that a plain script can't:

    * Each book is its own activity, so a completed book is recorded in workflow
      history and is NEVER re-fetched after a worker restart — Temporal replays
      history and continues from the next book.
    * ``klex_download_activity`` is idempotent (it skips a book whose object already
      exists in the bucket), so even a from-scratch re-run is cheap.
    * The book list is fetched once and carried across ``continue_as_new`` segments,
      so history stays small over the whole sweep and replay is fast. ``start_index``
      + ``totals`` are threaded through each segment to preserve progress.

    Mid-file worker death is handled by the activity's heartbeat timeout (the
    download heartbeats every few MB), so that single book is retried, not orphaned.
    """

    @workflow.run
    async def run(self, params: dict | None = None) -> dict:
        params = dict(params or {})
        bucket = params.get("bucket", "klex-herb")
        prefix = params.get("prefix", "")
        formats = params.get("formats", "")
        delay = float(params.get("delay_seconds", 1.0))
        segment = int(params.get("segment_size", 100))  # books per continue-as-new
        start_index = int(params.get("start_index", 0))
        totals = params.get("totals") or {
            "ok": 0, "skip": 0, "no_file": 0, "failed": 0, "bytes": 0, "total": 0,
        }

        # Fetch the catalogue once; carry it forward so later segments don't re-list
        # (re-listing could shift indices and desync start_index).
        books = params.get("books")
        if books is None:
            books = await workflow.execute_activity(
                klex_list_activity,
                start_to_close_timeout=_SHORT,
                retry_policy=_RETRY,
            )
            totals["total"] = len(books)

        i = start_index
        while i < len(books):
            code = books[i][0]
            status = None
            try:
                res = await workflow.execute_activity(
                    klex_download_activity,
                    args=[code, bucket, prefix, formats],
                    start_to_close_timeout=_LONG,
                    heartbeat_timeout=_HEARTBEAT,
                    retry_policy=_RETRY,
                )
                status = res.get("status")
                if status == "ok":
                    totals["ok"] += 1
                    totals["bytes"] += int(res.get("size", 0))
                elif status == "skip":
                    totals["skip"] += 1
                elif status == "no_file":
                    totals["no_file"] += 1
            except Exception as e:  # one bad book must not kill the whole sweep
                totals["failed"] += 1
                workflow.logger.warning(f"klex book {code} failed: {e}")

            i += 1
            # Pace EVERY book, including skips. A skip is NOT free: the activity
            # still fetches the klex detail page to learn the file slug before it
            # can check the bucket, so it counts against klex's per-IP rate limit
            # exactly like a real download. klex throttles bursts hard (~35 rapid
            # requests trips a 503 ban), so the only safe sweep is a uniform,
            # generous delay between every request. "Спешить некуда" — slow but
            # it finishes; a resume just re-walks the done books at the same gentle
            # pace and skips their bytes.
            if delay and i < len(books):
                await workflow.sleep(timedelta(seconds=delay))

            # Trim history periodically: hand the rest off to a fresh run.
            if i - start_index >= segment and i < len(books):
                workflow.continue_as_new(args=[{
                    "bucket": bucket, "prefix": prefix, "formats": formats,
                    "delay_seconds": delay, "segment_size": segment,
                    "start_index": i, "totals": totals, "books": books,
                }])

        return totals


@workflow.defn
class PlantCleanupWorkflow:
    """Autonomous, durable plant-cleanup chain (Layer-1 identity). Runs the phases
    in sequence as resumable heartbeat activities — no open session, no external
    watcher. Each activity loops internally over batches and is idempotent (skips
    already-done rows), so a worker restart just retries and resumes from the data.

    Chain: enrichment (iNat modern+photo) → latin backfill (clean-Russian latin-less
    species) → enrichment again (photos for the freshly-backfilled latins) → rename
    (garbage name → name_modern, collision-safe). Singleton id 'plant-cleanup'.
    """

    @workflow.run
    async def run(self) -> dict:
        kw = {"start_to_close_timeout": timedelta(hours=24),
              "heartbeat_timeout": _HEARTBEAT, "retry_policy": _RETRY}
        out: dict = {}
        out["enrich_1"] = await workflow.execute_activity(run_enrichment_activity, **kw)
        out["backfill"] = await workflow.execute_activity(run_backfill_activity, **kw)
        out["enrich_2"] = await workflow.execute_activity(run_enrichment_activity, **kw)
        out["rename"] = await workflow.execute_activity(
            run_rename_activity, start_to_close_timeout=_SHORT, retry_policy=_RETRY)
        workflow.logger.info(f"PlantCleanupWorkflow done: {out}")
        return out


@workflow.defn
class FillLatinWorkflow:
    """Durable NULL-latin fill (identity foundation): resolve name_latin for clean-
    Russian-name cards lacking it, so the latin-keyed dedup can see them. Single
    resumable heartbeat activity (idempotent, skips cards already processed).
    Singleton id 'fill-latin'."""

    @workflow.run
    async def run(self) -> dict:
        out = await workflow.execute_activity(
            fill_latin_activity, start_to_close_timeout=timedelta(hours=48),
            heartbeat_timeout=_HEARTBEAT, retry_policy=_RETRY)
        workflow.logger.info(f"FillLatinWorkflow done: {out}")
        return out


@workflow.defn
class RecipeRelinkWorkflow:
    """Corpus-wide recipe→plant relink (Phase 2b): clear + rematch all recipe/ingredient
    links against the cleaned plant identity. Idempotent full recompute. GATE: run only
    AFTER the plant-identity foundation is closed (merge + fill-latin + conflict + the
    1g alias.collision strip). Singleton 'recipe-relink'."""

    @workflow.run
    async def run(self) -> dict:
        out = await workflow.execute_activity(
            recipe_relink_activity, start_to_close_timeout=timedelta(hours=6),
            heartbeat_timeout=_HEARTBEAT, retry_policy=_RETRY)
        workflow.logger.info(f"RecipeRelinkWorkflow done: {out}")
        return out


@workflow.defn
class IdentityConflictWorkflow:
    """Durable name↔latin conflict fix (identity foundation): correct cards whose
    Russian name resolves to a different genus than the stored latin (corruption, not
    a taxonomic synonym). Cursor-resumable heartbeat activity. Singleton 'identity-conflict'."""

    @workflow.run
    async def run(self) -> dict:
        out = await workflow.execute_activity(
            conflict_check_activity, start_to_close_timeout=timedelta(hours=48),
            heartbeat_timeout=_HEARTBEAT, retry_policy=_RETRY)
        workflow.logger.info(f"IdentityConflictWorkflow done: {out}")
        return out


@workflow.defn
class BiotopeCanonWorkflow:
    """Durable biotope normalization (side-domain): canonicalize free-text habitat
    into controlled biotopes. Single resumable heartbeat activity, idempotent.
    Singleton id 'biotope-canon'."""

    @workflow.run
    async def run(self) -> dict:
        out = await workflow.execute_activity(
            biotope_canon_activity, start_to_close_timeout=timedelta(hours=48),
            heartbeat_timeout=_HEARTBEAT, retry_policy=_RETRY)
        workflow.logger.info(f"BiotopeCanonWorkflow done: {out}")
        return out


@workflow.defn
class EdibleSafetyWorkflow:
    """Durable forager-safety classification (RFC-edible-safety): assign every species
    an ordinal level 0-4 («will it hurt me if I eat it»), replacing the over-set
    is_toxic. Deterministic safe-default for no-risk plants + LLM (qwen3-235b) for the
    risky subset, deadly-anchor as an un-lowerable L4 floor. Idempotent (safety_level
    NULL = unprocessed), commit-per-plant. Singleton 'edible-safety'. GATE: run BEFORE
    monograph mass-gen (the level is shown as the card's first, safety-critical block)."""

    @workflow.run
    async def run(self) -> dict:
        out = await workflow.execute_activity(
            edible_safety_activity, start_to_close_timeout=timedelta(hours=24),
            heartbeat_timeout=_HEARTBEAT, retry_policy=_RETRY)
        workflow.logger.info(f"EdibleSafetyWorkflow done: {out}")
        return out


@workflow.defn
class GenusTierWorkflow:
    """Durable genus-tier assembly (RFC-reference-granularity): create a hub genus row
    (rank='genus') per Russian noun-token realized by ≥2 species, grounded by latin
    genus; parent the confirmed members. Idempotent (find-or-create + first-claim
    parent). Singleton 'genus-tier'. Run BEFORE the genus-aware recipe relink."""

    @workflow.run
    async def run(self) -> dict:
        out = await workflow.execute_activity(
            genus_assembly_activity, start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=_HEARTBEAT, retry_policy=_RETRY)
        workflow.logger.info(f"GenusTierWorkflow done: {out}")
        return out


@workflow.defn
class OsmIngestWorkflow:
    """Autonomous OSM named-place ingest for a region bbox (quests Phase 6).
    Tiles the bbox + upserts named places into quest_places. Durable + resumable
    (idempotent osm_id upsert). Overpass-only (no iNat) → safe to run anytime."""

    @workflow.run
    async def run(self, s: float, w: float, n: float, e: float, tile: float = 0.1) -> dict:
        return await workflow.execute_activity(
            osm_ingest_region_activity, args=[s, w, n, e, tile],
            start_to_close_timeout=timedelta(hours=6), heartbeat_timeout=_HEARTBEAT,
            retry_policy=_RETRY)


@workflow.defn
class QuestSetBuilderWorkflow:
    """Autonomous species-set precompute for all places × a half-month window
    (quests Phase 6). iNat-heavy → run when the cleanup's iNat work is idle."""

    @workflow.run
    async def run(self, window_label: str) -> dict:
        return await workflow.execute_activity(
            build_place_sets_activity, window_label,
            start_to_close_timeout=timedelta(hours=12), heartbeat_timeout=_HEARTBEAT,
            retry_policy=_RETRY)


@workflow.defn
class ReaderMonographWorkflow:
    """Layer-2 batch: precompute reader-monographs for all clean-identity plants
    (RFC-reader-monograph §7). Idempotent (hash-gate) → re-runnable as the corpus
    grows. GATE: run only after Layer-1 data cleanup is complete (§10.5) — and
    mind iNat/LLM contention with the cleanup chain."""

    @workflow.run
    async def run(self, batch: int = 100) -> dict:
        return await workflow.execute_activity(
            generate_monographs_activity, batch,
            start_to_close_timeout=timedelta(hours=24), heartbeat_timeout=_HEARTBEAT,
            retry_policy=_RETRY)


@workflow.defn
class PingWorkflow:
    """Connectivity smoke test: proves the worker polls the queue and runs
    activities against the live cluster."""

    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            ping_activity, name, start_to_close_timeout=timedelta(seconds=30),
        )
