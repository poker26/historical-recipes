"""Названия рецептов по составу как задача Temporal.

Модель отвечает по рецепту за несколько секунд, а рецептов сотни, поэтому прогон живёт в
воркере диспетчера. Возобновление по данным: переименованный рецепт записан в журнал
processing_log и в выборку больше не попадает. Логика в ``app/services/recipe_names.py``.
"""

from __future__ import annotations

import asyncio

from temporalio import activity

from app.services.recipe_names import run_recipe_rename


@activity.defn
async def recipe_rename_activity(apply: bool = False, limit: int = 0, force: bool = False) -> dict:
    last: dict = {}

    def report(progress: dict) -> None:
        nonlocal last
        last = progress
        activity.heartbeat(progress)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(60)
            activity.heartbeat(last)

    tick = asyncio.create_task(ticker())
    try:
        return await run_recipe_rename(apply=apply, limit=limit, force=force, progress=report)
    finally:
        tick.cancel()
