"""Dedicated Temporal worker for the klex.ru → MinIO mirror.

Runs in its OWN process / container on its OWN task queue so the long download
sweep is fully isolated from the main book-pipeline worker. That matters: the
book pipeline's OCR / LLM activities are CPU-heavy and saturate their worker's
process; sharing it starves the download workflow's deterministic task thread and
trips Temporal's deadlock detector ("workflow didn't yield within 2 seconds").
On a separate, lightweight worker the I/O-only download work always makes progress.

Run:  python -m app.temporal.klex_worker
"""

import asyncio
import logging

from temporalio.worker import Worker

from app.temporal.client import get_temporal_client
from app.temporal import activities
from app.temporal.workflows import KlexHerbDownloadWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("temporal.klex_worker")

# Separate queue: only this worker polls it, so the sweep can't be crowded out by
# (or crowd out) the main pipeline. The launcher starts the workflow here too.
KLEX_TASK_QUEUE = "klex-download"


async def main():
    client = await get_temporal_client()
    logger.info("klex worker connecting; queue=%s", KLEX_TASK_QUEUE)
    worker = Worker(
        client,
        task_queue=KLEX_TASK_QUEUE,
        workflows=[KlexHerbDownloadWorkflow],
        activities=[
            activities.klex_list_activity,
            activities.klex_download_activity,
        ],
        # Downloads are I/O-bound; a couple in flight is plenty and keeps the
        # event loop responsive so the workflow task never deadlocks.
        max_concurrent_activities=2,
    )
    logger.info("klex worker started; polling '%s'", KLEX_TASK_QUEUE)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
