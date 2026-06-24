# -*- coding: utf-8 -*-
"""Launch the Layer-2 reader-monograph refresh (ReaderMonographWorkflow, singleton
`reader-monograph` on the dispatcher queue). Hash-idempotent: regenerates only plants whose
distilled LLM-facing input changed (this session's identity merges / canon actions / decoupled
recipes / GBIF latins), skips the rest. start_workflow on an already-RUNNING id raises
WorkflowAlreadyStartedError → we report it rather than double-launch (the known gotcha)."""
import asyncio

from app.temporal.client import get_temporal_client
from app.temporal.workflows import ReaderMonographWorkflow


async def main():
    c = await get_temporal_client()
    try:
        h = await c.start_workflow(
            ReaderMonographWorkflow.run, id="reader-monograph", task_queue="dispatcher")
        print("STARTED id=%s run=%s" % (h.id, h.result_run_id))
    except Exception as e:  # noqa: BLE001
        print("NOT STARTED: %s — %s" % (type(e).__name__, str(e)[:160]))


if __name__ == "__main__":
    asyncio.run(main())
