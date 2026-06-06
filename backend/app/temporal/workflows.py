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
        medical_vocab_batch_activity,
        normalize_medical_activity,
        match_ingredients_activity,
        index_activity,
        enrich_inat_activity,
        klex_list_activity,
        klex_download_activity,
        ping_activity,
    )

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


def steps_for_domain(domain: str):
    d = (domain or "").lower()
    # fungi (mushroom guides) reuse the entire herbalism pipeline — same monograph
    # parsing, recipe extraction and cross-linking — and are distinguished only by
    # the kingdom tag the extractor stamps on each row (see extract_plant_entries).
    if d in ("herbalism", "fungi"):
        return PIPELINE_STEPS_HERBALISM
    if d == "reference":
        return PIPELINE_STEPS_REFERENCE
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
class PingWorkflow:
    """Connectivity smoke test: proves the worker polls the queue and runs
    activities against the live cluster."""

    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            ping_activity, name, start_to_close_timeout=timedelta(seconds=30),
        )
