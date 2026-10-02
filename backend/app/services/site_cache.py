"""Сброс кэша карточек на сайте botanik.fun сразу после правки данных.

Сайт держит ответы бэкенда о карточке шесть часов (метка кэша ``plant:{id}``), и без
сброса исправление доходит до страницы только через этот срок. Бэкенд и диспетчер
зовут адрес сайта ``/api/revalidate`` изнутри docker-сети с секретом из
REVALIDATE_SECRET; без секрета ничего не происходит.

Как пользоваться:

- шаг меняет карточку внутри транзакции и вызывает ``mark(id)``; после коммита задача
  вызывает ``await flush()``, и сайт сбрасывает все отмеченные карточки. Отметки
  раздельные для каждой задачи Temporal: одна задача не сбросит чужие карточки до их
  коммита;
- код, который уже закоммитил правку, зовёт ``await revalidate_plants([id, …])``.

Ошибка сброса не роняет работу: страница просто обновится по истечении срока кэша.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

SITE_INTERNAL_URL = os.environ.get("SITE_INTERNAL_URL", "http://site:3000")
_BATCH = 1000

_pending: dict[str, set[str]] = defaultdict(set)


def _key() -> str:
    """Отметки одной задачи Temporal; вне Temporal общий ключ."""
    try:
        from temporalio import activity
        if activity.in_activity():
            info = activity.info()
            return f"{info.workflow_id}/{info.activity_id}"
    except Exception:  # noqa: BLE001
        pass
    return ""


def mark(*ids) -> None:
    """Запомнить карточки, у которых поменялись данные; сбросятся при flush()."""
    _pending[_key()].update(str(i).lower() for i in ids if i)


async def flush() -> int:
    """Сбросить кэш карточек, отмеченных этой задачей. Звать после коммита."""
    ids = _pending.pop(_key(), set())
    return await revalidate_plants(sorted(ids)) if ids else 0


async def revalidate_plants(ids: Iterable) -> int:
    """Сбросить кэш карточек на сайте. Возвращает, сколько карточек сайт принял."""
    secret = os.environ.get("REVALIDATE_SECRET", "").strip()
    ids = [str(i).lower() for i in ids if i]
    if not secret or not ids:
        return 0
    done = 0
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for k in range(0, len(ids), _BATCH):
                r = await client.post(f"{SITE_INTERNAL_URL}/api/revalidate", json={"ids": ids[k:k + _BATCH]},
                                      headers={"x-revalidate-secret": secret})
                if r.status_code == 200:
                    done += int(r.json().get("plants", 0))
                else:
                    logger.warning("site revalidate: HTTP %s", r.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("site revalidate failed: %s", type(e).__name__)
    return done
