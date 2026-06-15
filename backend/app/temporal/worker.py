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
from app.temporal.workflows import (
    BookPipelineWorkflow,
    BookDispatcherWorkflow,
    InatEnrichmentWorkflow,
    MedicalNormalizerWorkflow,
    KlexHerbDownloadWorkflow,
    PingWorkflow,
)

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
        workflows=[BookPipelineWorkflow, BookDispatcherWorkflow, InatEnrichmentWorkflow,
                   MedicalNormalizerWorkflow, KlexHerbDownloadWorkflow, PingWorkflow],
        activities=[
            activities.convert_activity,
            activities.classify_activity,
            activities.extract_activity,
            activities.cleanup_activity,
            activities.translate_activity,
            activities.analyze_activity,
            activities.extract_recipes_activity,
            activities.extract_plant_entries_activity,
            activities.extract_vocabulary_activity,
            activities.normalize_corpus_activity,
            activities.extract_oils_activity,
            activities.normalize_oils_activity,
            activities.medical_vocab_batch_activity,
            activities.normalize_medical_activity,
            activities.match_ingredients_activity,
            activities.index_activity,
            activities.enrich_inat_activity,
            activities.klex_list_activity,
            activities.klex_download_activity,
            activities.ping_activity,
            activities.maintain_pool_activity,
        ],
        # Pipeline steps are few but very long-running and I/O-bound (LLM calls).
        # The host sits near-idle (CPU ~0.5/6 cores, GBs of free RAM), so the cap
        # is about the OpenRouter rate ceiling, not local resources. Raised 4→8 to
        # run more book-pipelines in parallel; the per-step inner fan-out
        # (MAX_CONCURRENT, _RECIPE_SECTION_CONCURRENCY) multiplies on top, so watch
        # the logs for 429 before pushing this higher.
        max_concurrent_activities=8,
    )

    logger.info("Worker started; polling task queue '%s'", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
