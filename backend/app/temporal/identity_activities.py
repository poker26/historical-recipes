"""Чистка идентичности карточек как задачи Temporal.

Четыре шага из ``app/services/identity_cleanup.py``: дубли по имени, оболочки
без фактов, латынь через GBIF, переопределение моделью. Каждый шаг идёт часами
(GBIF и модель по одному запросу за раз), поэтому живёт в воркере диспетчера с
heartbeat после каждой карточки. Возобновление по данным: сделанное помечено в
базе и в повторную выборку не попадает.
"""

from __future__ import annotations

from temporalio import activity

from app.services.identity_cleanup import run_dedup, run_gbif, run_reid, run_shells
from app.services.identity_resolve import run_resolve


def _report(progress: dict) -> None:
    activity.heartbeat(progress)


@activity.defn
async def identity_dedup_activity(apply: bool = False) -> dict:
    return await run_dedup(apply=apply, progress=_report)


@activity.defn
async def identity_shells_activity(apply: bool = False, limit: int = 0) -> dict:
    return await run_shells(apply=apply, limit=limit, progress=_report)


@activity.defn
async def identity_gbif_activity(limit: int = 0) -> dict:
    return await run_gbif(limit=limit, progress=_report)


@activity.defn
async def identity_reid_activity(limit: int = 0) -> dict:
    return await run_reid(limit=limit, progress=_report)


@activity.defn
async def identity_resolve_activity(apply: bool = False, limit: int = 0) -> dict:
    return await run_resolve(apply=apply, limit=limit, progress=_report)
