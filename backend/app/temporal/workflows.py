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


@workflow.defn
class BookPipelineWorkflow:
    """Run a book through the full 7-step pipeline, end to end.

    Each step is durable: if the worker restarts, Temporal replays history and
    resumes from the last completed activity rather than from scratch.
    """

    @workflow.run
    async def run(self, book_id: str) -> dict:
        results: dict = {}

        async def step(name: str, fn, timeout):
            workflow.logger.info(f"pipeline step start: {name}")
            res = await workflow.execute_activity(
                fn, book_id, start_to_close_timeout=timeout, retry_policy=_RETRY,
            )
            results[name] = res
            workflow.logger.info(f"pipeline step done: {name} -> {res}")
            return res

        await step("classify", classify_activity, _SHORT)
        await step("extract", extract_activity, _LONG)
        await step("cleanup", cleanup_activity, _LONG)
        await step("translate", translate_activity, _LONG)
        await step("analyze", analyze_activity, _LONG)
        await step("extract_recipes", extract_recipes_activity, _LONG)
        await step("match_ingredients", match_ingredients_activity, _SHORT)
        await step("index", index_activity, _LONG)

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
