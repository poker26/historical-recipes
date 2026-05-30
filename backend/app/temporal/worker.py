"""Temporal worker entrypoint.

Run as its own process / container:  ``python -m app.temporal.worker``

It connects to the shared cluster, registers the pipeline workflows and all
step activities, and polls the project task queue.  Because the pipeline steps
are long (LLM calls of many minutes), the activity executor uses a generous
threadpool and we don't cap concurrent activities aggressively.
"""

import asyncio
import logging

from temporalio.worker import Worker

from app.config import settings
from app.temporal.client import get_temporal_client
from app.temporal import activities
from app.temporal.workflows import BookPipelineWorkflow, PingWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("temporal.worker")


async def main():
    client = await get_temporal_client()
    logger.info(
        "Worker connecting: address=%s namespace=%s queue=%s",
        settings.temporal_address, settings.temporal_namespace, settings.temporal_task_queue,
    )

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[BookPipelineWorkflow, PingWorkflow],
        activities=[
            activities.classify_activity,
            activities.extract_activity,
            activities.cleanup_activity,
            activities.translate_activity,
            activities.analyze_activity,
            activities.extract_recipes_activity,
            activities.match_ingredients_activity,
            activities.index_activity,
            activities.ping_activity,
        ],
        # Pipeline steps are few but very long-running; one at a time per book
        # is the natural cadence, but allow a little headroom across books.
        max_concurrent_activities=4,
    )

    logger.info("Worker started; polling task queue '%s'", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
