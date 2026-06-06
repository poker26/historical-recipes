"""Launch (or inspect) the durable klex.ru → MinIO mirror workflow.

The actual crawl/download runs as ``KlexHerbDownloadWorkflow`` on the Temporal
worker, so it survives worker restarts and resumes where it left off (completed
books are in workflow history; already-uploaded objects are skipped). This script
just starts that workflow and, optionally, polls its status.

Run inside the backend container (it has the Temporal address + MinIO creds):

    # start the mirror into the default "klex-herb" bucket:
    docker compose exec backend python scripts/download_klex_herb.py

    # start and tail status until it finishes:
    docker compose exec backend python scripts/download_klex_herb.py --watch

    # just show status of an already-running mirror:
    docker compose exec backend python scripts/download_klex_herb.py --status

    # custom bucket / pacing / format preference:
    docker compose exec backend python scripts/download_klex_herb.py \
        --bucket klex-herb --delay 1.5 --formats pdf,djvu,zip

If a mirror is already RUNNING, starting again is refused (re-use --watch/--status).
The workflow is idempotent, so if it ever stops half-way just run this again to
resume — it will skip everything already in the bucket.
"""

import argparse
import asyncio
import sys

WF_ID = "klex-herb-download"


async def _run(args) -> int:
    from temporalio.service import RPCError

    from app.temporal.client import get_temporal_client
    from app.temporal.workflows import KlexHerbDownloadWorkflow

    client = await get_temporal_client()
    handle = client.get_workflow_handle(WF_ID)

    running = False
    try:
        desc = await handle.describe()
        running = bool(desc.status and desc.status.name == "RUNNING")
    except RPCError:
        pass  # never started

    if args.status:
        if not running:
            print("No klex mirror running.")
        else:
            print(f"klex mirror RUNNING (workflow_id={WF_ID}).")
            print("Use the Temporal UI for live per-book heartbeats.")
        return 0

    if not running:
        params = {
            "bucket": args.bucket,
            "prefix": args.prefix,
            "formats": args.formats,
            "delay_seconds": args.delay,
        }
        handle = await client.start_workflow(
            KlexHerbDownloadWorkflow.run,
            args=[params],
            id=WF_ID,
            task_queue=args.task_queue,
        )
        print(f"Started klex mirror: workflow_id={handle.id} run_id={handle.result_run_id}")
        print(f"  bucket={args.bucket!r}  formats={args.formats!r}  delay={args.delay}s"
              f"  queue={args.task_queue!r}")
    else:
        print(f"klex mirror already RUNNING (workflow_id={WF_ID}).")

    if args.watch:
        print("Waiting for completion (Ctrl-C to detach — the workflow keeps running)...")
        result = await handle.result()
        print("Done:", result)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Start/inspect the klex.ru MinIO mirror workflow")
    ap.add_argument("--bucket", default="klex-herb", help="target MinIO bucket")
    ap.add_argument("--task-queue", default="klex-download",
                    help="Temporal task queue (dedicated klex worker polls 'klex-download')")
    ap.add_argument("--prefix", default="", help="object-key prefix inside the bucket")
    ap.add_argument("--formats", default="pdf,djvu,zip,fb2,docx,doc,txt",
                    help="format preference order; first available per book wins")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds between books (politeness)")
    ap.add_argument("--watch", action="store_true", help="block until the workflow finishes")
    ap.add_argument("--status", action="store_true", help="only report running status, don't start")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
