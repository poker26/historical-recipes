"""Dedicated Temporal worker for the corpus dispatcher.

Runs in its OWN process / container on its OWN task queue so the lightweight
``BookDispatcherWorkflow`` is fully isolated from the main book-pipeline worker.
That matters: the pipeline's OCR / LLM / embedding activities are CPU-heavy and
periodically block the main worker's single asyncio event loop for >2 minutes,
which starves the dispatcher's deterministic workflow-task processing and trips
WorkflowTaskTimedOut — so on the shared worker the 45s dispatcher loop never
ticks. On a separate, near-idle worker its workflow tasks always complete
instantly and the pool stays topped up.

``maintain_pool_activity`` runs HERE (it's only fast async I/O — a list_workflows
+ a few DB queries + start_workflow calls), but the book-pipelines it starts are
submitted to the MAIN task queue (the activity passes
``task_queue=settings.temporal_task_queue``), so all heavy work still lands on the
pipeline worker. This worker only orchestrates.

Run:  python -m app.temporal.dispatcher_worker
"""

import asyncio
import logging

from temporalio.worker import Worker

from app.temporal.client import get_temporal_client
from app.temporal import activities
from app.temporal import cleanup_activities
from app.temporal import quest_activities
from app.temporal import monograph_activities
from app.temporal.workflows import (
    BookDispatcherWorkflow, PlantCleanupWorkflow, FillLatinWorkflow, OsmIngestWorkflow,
    QuestSetBuilderWorkflow, ReaderMonographWorkflow,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("temporal.dispatcher_worker")

# Separate queue: only this worker polls it, so the dispatcher can't be crowded
# out by (or crowd out) the main pipeline. The launcher starts the workflow here.
DISPATCHER_TASK_QUEUE = "dispatcher"


async def main():
    client = await get_temporal_client()
    logger.info("dispatcher worker connecting; queue=%s", DISPATCHER_TASK_QUEUE)
    worker = Worker(
        client,
        task_queue=DISPATCHER_TASK_QUEUE,
        workflows=[BookDispatcherWorkflow, PlantCleanupWorkflow, FillLatinWorkflow,
                   OsmIngestWorkflow, QuestSetBuilderWorkflow, ReaderMonographWorkflow],
        activities=[
            activities.maintain_pool_activity,
            # Autonomous plant-cleanup chain + quests-build + Layer-2 monograph batch.
            # All I/O-bound (LLM/iNat/Overpass/DB awaits), so unlike the CPU-heavy
            # pipeline activities they never block the event loop → safe to co-host.
            cleanup_activities.run_enrichment_activity,
            cleanup_activities.run_backfill_activity,
            cleanup_activities.run_rename_activity,
            cleanup_activities.fill_latin_activity,
            quest_activities.osm_ingest_region_activity,
            quest_activities.build_place_sets_activity,
            monograph_activities.generate_monographs_activity,
        ],
        max_concurrent_activities=4,
    )
    logger.info("dispatcher worker started; polling '%s'", DISPATCHER_TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
