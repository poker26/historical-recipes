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
        classify_activity,
        extract_activity,
        cleanup_activity,
        translate_activity,
        analyze_activity,
        extract_recipes_activity,
        extract_plant_entries_activity,
        extract_vocabulary_activity,
        normalize_corpus_activity,
        match_ingredients_activity,
        index_activity,
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
    maximum_attempts=4,
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
    ("classify", classify_activity, _SHORT, None),
    ("extract", extract_activity, _LONG, _HEARTBEAT),
    ("cleanup", cleanup_activity, _LONG, _HEARTBEAT),
    ("translate", translate_activity, _LONG, _HEARTBEAT),
    ("analyze", analyze_activity, _LONG, _HEARTBEAT),
    ("extract_plant_entries", extract_plant_entries_activity, _LONG, _HEARTBEAT),
    ("extract_recipes", extract_recipes_activity, _LONG, _HEARTBEAT),
    ("match_ingredients", match_ingredients_activity, _SHORT, None),
    ("index", index_activity, _LONG, _HEARTBEAT),
]
# Reference-normalizer domain: a property-first reference (e.g. a phytochemistry
# monograph) whose product is a controlled vocabulary + a corpus-wide normalize,
# NOT per-book entities. Shares the text-prep front, then diverges:
# extract_vocabulary builds/grows the compound vocab (+ normalized PlantCompound
# occurrences), normalize_corpus maps every plant's free-text compounds to it.
# No analyze/extract_recipes — there are no recipes to pull from a chemistry book.
PIPELINE_STEPS_REFERENCE = [
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
    async def run(self, book_id: str, start_step: str = "classify", domain: str = "recipes") -> dict:
        steps = steps_for_domain(domain)
        step_names = [s[0] for s in steps]
        if start_step not in step_names:
            start_step = "classify"
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
class PingWorkflow:
    """Connectivity smoke test: proves the worker polls the queue and runs
    activities against the live cluster."""

    @workflow.run
    async def run(self, name: str) -> str:
        return await workflow.execute_activity(
            ping_activity, name, start_to_close_timeout=timedelta(seconds=30),
        )
