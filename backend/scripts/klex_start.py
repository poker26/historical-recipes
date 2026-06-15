"""Start the klex mirror workflow over an EXPLICIT remaining-books list.

Unlike download_klex_herb.py (which lists klex itself and walks all 585), this
feeds a pre-computed ``books`` list straight into the workflow so it fetches
ONLY those — zero wasted klex requests on already-mirrored books. The list is
produced offline by scripts/klex_plan.py (which reconciles the catalogue against
the MinIO bucket using only Temporal history + the bucket, no klex traffic).

This matters because klex bans egress IPs permanently once a request-rate
threshold is crossed, so every request on a fresh IP must count.

Run inside a container that can reach Temporal (e.g. the klex worker on the
fresh-IP host):
    python scripts/klex_start.py --books-file /tmp/remaining.json --delay 180
"""

import argparse
import asyncio
import json

WF_ID = "klex-herb-download"


async def _run(a) -> int:
    from temporalio.service import RPCError

    from app.temporal.client import get_temporal_client
    from app.temporal.workflows import KlexHerbDownloadWorkflow

    books = None
    if a.books_file:
        with open(a.books_file) as f:
            books = json.load(f)
        # accept either [[code,title],...] or ["code",...]
        books = [b if isinstance(b, list) else [b, ""] for b in books]

    wf_id = a.wf_id
    client = await get_temporal_client()
    handle = client.get_workflow_handle(wf_id)
    try:
        desc = await handle.describe()
        if desc.status and desc.status.name == "RUNNING":
            print(f"REFUSING: {wf_id} already RUNNING. Terminate it first.")
            return 1
    except RPCError:
        pass

    params = {
        "bucket": a.bucket,
        "formats": a.formats,
        "delay_seconds": a.delay,
        "segment_size": a.segment,
    }
    if books is not None:
        params["books"] = books

    handle = await client.start_workflow(
        KlexHerbDownloadWorkflow.run,
        args=[params],
        id=wf_id,
        task_queue=a.task_queue,
    )
    print(f"Started {handle.id} run={handle.result_run_id}")
    print(f"  books={'ALL (list klex)' if books is None else len(books)}"
          f"  delay={a.delay}s  segment={a.segment}  queue={a.task_queue!r}"
          f"  bucket={a.bucket!r}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--books-file", help="JSON list of [code,title] to fetch (else all)")
    ap.add_argument("--bucket", default="klex-herb")
    ap.add_argument("--formats", default="pdf,djvu,zip,fb2,docx,doc,txt")
    ap.add_argument("--delay", type=float, default=180.0,
                    help="seconds between EVERY book (incl. skips) — politeness")
    ap.add_argument("--segment", type=int, default=40,
                    help="books per continue-as-new (history trim)")
    ap.add_argument("--task-queue", default="klex-download")
    ap.add_argument("--wf-id", default=WF_ID,
                    help="workflow id (use a non-default name to avoid an "
                         "external process that terminates the well-known id)")
    return asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
