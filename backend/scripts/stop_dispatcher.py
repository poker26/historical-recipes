"""List running workflows; terminate the book-dispatcher (stop auto-dispatch)."""
import asyncio
from app.temporal.client import get_temporal_client


async def main():
    c = await get_temporal_client()
    print("== RUNNING workflows ==", flush=True)
    running = []
    async for wf in c.list_workflows("ExecutionStatus='Running'"):
        running.append(wf.id)
        print(f"  {wf.id}   [{wf.workflow_type}]", flush=True)

    # Stop the corpus auto-dispatcher so it stops topping the pool with books.
    for wid in ["book-dispatcher"]:
        try:
            await c.get_workflow_handle(wid).terminate(
                reason="main corpus drained; pause auto-dispatch during reference-flora pilot")
            print(f"TERMINATED {wid}", flush=True)
        except Exception as e:
            print(f"terminate {wid} failed: {e}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
