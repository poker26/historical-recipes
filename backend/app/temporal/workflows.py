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
        match_ingredients_activity,
        index_activity,
        ping_activity,
    )

# Generous per-step ceilings: the pre-reform 1M-char book took >60 min on a
# single LLM step, so long steps get up to 3h.  Retries are bounded and skip
# non-transient errors (bad input raises ValueError → no point retrying).
_LONG = timedelta(hours=3)
_SHORT = timedelta(minutes=20)
_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=10),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=4,
    non_retryable_error_types=["ValueError"],
)


# Ordered pipeline definition: (step name, activity, per-step timeout).
# A single source of truth so the workflow and the API agree on step order.
PIPELINE_STEPS = [
    ("classify", classify_activity, _SHORT),
    ("extract", extract_activity, _LONG),
    ("cleanup", cleanup_activity, _LONG),
    ("translate", translate_activity, _LONG),
    ("analyze", analyze_activity, _LONG),
    ("extract_recipes", extract_recipes_activity, _LONG),
    ("match_ingredients", match_ingredients_activity, _SHORT),
    ("index", index_activity, _LONG),
]
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
    async def run(self, book_id: str, start_step: str = "classify") -> dict:
        if start_step not in STEP_NAMES:
            start_step = "classify"
        start_idx = STEP_NAMES.index(start_step)

        results: dict = {"_started_at_step": start_step}
        for name, fn, timeout in PIPELINE_STEPS[start_idx:]:
            workflow.logger.info(f"pipeline step start: {name}")
            res = await workflow.execute_activity(
                fn, book_id, start_to_close_timeout=timeout, retry_policy=_RETRY,
            )
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
