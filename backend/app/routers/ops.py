"""Ops dashboard — generic Temporal workflow status for the long cleanup workflows
(fill-latin, plant-cleanup, quest-sets, monographs…). Shows RUNNING **and** recently
CLOSED runs with live heartbeat progress, so a FAILED workflow is VISIBLE on the web
UI instead of dying silently overnight. Reuses the heartbeat-extraction pattern from
the book-pipeline dashboard.
"""
from fastapi import APIRouter

router = APIRouter()

# The durable cleanup/dispatcher workflows worth watching (not the per-book pipeline,
# which has its own dashboard at /api/wizard/active).
CLEANUP_TYPES = [
    "FillLatinWorkflow",
    "BiotopeCanonWorkflow",
    "PlantCleanupWorkflow",
    "QuestSetBuilderWorkflow",
    "ReaderMonographWorkflow",
    "OsmIngestWorkflow",
    "BookDispatcherWorkflow",
    "MedicalNormalizerWorkflow",
]


async def _failure_message(handle) -> str | None:
    """Best-effort: the message of the first failure in the workflow history."""
    try:
        async for ev in handle.fetch_history_events():
            for attr in dir(ev):
                if attr.endswith("_event_attributes"):
                    a = getattr(ev, attr)
                    f = getattr(a, "failure", None)
                    if f and getattr(f, "message", None):
                        c = getattr(f, "cause", None)
                        return ((getattr(c, "message", None) or f.message) or "")[:300]
    except Exception:
        return None
    return None


@router.get("/workflows")
async def workflows(per_type: int = 3):
    """The latest few runs of each cleanup workflow type, with live progress."""
    from app.temporal.client import get_temporal_client

    client = await get_temporal_client()
    out: list[dict] = []
    for t in CLEANUP_TYPES:
        n = 0
        try:
            async for wf in client.list_workflows(f"WorkflowType = '{t}'"):
                status = wf.status.name if wf.status else "UNKNOWN"
                e = {
                    "id": wf.id, "type": t, "status": status,
                    "start_time": wf.start_time.isoformat() if wf.start_time else None,
                    "close_time": wf.close_time.isoformat() if wf.close_time else None,
                    "progress": None, "attempt": None, "error": None,
                }
                handle = client.get_workflow_handle(wf.id, run_id=wf.run_id)
                if status == "RUNNING":
                    try:
                        desc = await handle.describe()
                        pending = list(getattr(desc.raw_description, "pending_activities", []))
                        if pending:
                            e["attempt"] = pending[0].attempt
                            hb = getattr(pending[0], "heartbeat_details", None)
                            if hb and getattr(hb, "payloads", None):
                                decoded = await client.data_converter.decode(list(hb.payloads))
                                if decoded:
                                    e["progress"] = decoded[0]
                    except Exception:
                        pass
                elif status == "FAILED":
                    e["error"] = await _failure_message(handle)
                out.append(e)
                n += 1
                if n >= per_type:
                    break
        except Exception:
            continue
    out.sort(key=lambda w: w.get("start_time") or "", reverse=True)
    return {"workflows": out}
