"""Photo → mushroom identification via OUR OWN engine (BioCLIP-2 kNN on server 2).

История: ветка стартовала на Kindwise Mushroom.id (тест-ключ), но платный-за-вызов
движок — неверная модель для бесплатного приложения, а тест-кредиты кончились и
каждый вызов стал 429 (грибной сезон 2026-08 юзеры провели с мёртвой веткой).
Заменён СВОИМ бесплатным движком (решение Олега 2026-08-24): BioCLIP-2 (MIT) + kNN
по индексу 841 вида (546 корпусных + топ снимаемых в РФ), сервис `fungi-engine` на
server 2 (:8977, контейнер из id-shadow-стека). Пилот-замер на 2460 полевых фото:
**top-1 76.5%, top-5 95.7%** — класс Kindwise, цена 0.

Контракт тот же, что у ``plant_id.identify`` → ``{engine, candidates:[{latin, score,
…}]}``; мост ``_bridge`` (по ``_latin_key``) прикрепляет гриб-монографы, safety-блок
и жёсткий «не употреблять по фото» дисклеймер живут в роутере как раньше.

⚠️ SAFETY: photo-ID of mushrooms has DEADLY lookalikes (бледная поганка ↔ шампиньон).
Никаких изменений в safety-слое: дисклеймер несъёмный, deadly_twin из корпуса.

Like the other engine layers this never raises: every failure returns an ``error`` dict.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Косинус → сопоставимая с вероятностью уверенность (см. калибровку ниже).
_CAL_LO, _CAL_HI = 0.78, 0.95


def _calibrate(sim: float | None) -> float:
    if sim is None:
        return 0.0
    return round(min(1.0, max(0.0, (float(sim) - _CAL_LO) / (_CAL_HI - _CAL_LO))), 4)


async def identify(
    images: list[bytes],
    *,
    image_urls: list[str] | None = None,
    limit: int = 5,
    **_ignore,
) -> dict:
    """Identify a mushroom from a photo (первое из ``images``; ``image_urls`` — через
    скачивание). Returns ``{engine, candidates:[…]}`` on success, ``{error: …}`` on
    any failure. Never raises."""
    if not settings.fungi_engine_url:
        return {"error": "fungi engine not configured (set FUNGI_ENGINE_URL)"}
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            jpeg = images[0] if images else None
            if jpeg is None and image_urls:
                r0 = await client.get(image_urls[0])
                if r0.status_code == 200:
                    jpeg = r0.content
            if not jpeg:
                return {"error": "no images provided"}
            r = await client.post(
                f"{settings.fungi_engine_url.rstrip('/')}/identify",
                content=jpeg,
                headers={"X-Engine-Token": settings.fungi_engine_token,
                         "Content-Type": "image/jpeg"})
        if r.status_code != 200:
            return {"error": f"fungi engine {r.status_code}: {r.text[:160]}"}
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("fungi engine transport error: %s", str(e)[:100])
        return {"error": f"fungi engine transport error: {str(e)[:120]}"}
    cands = [{
        "latin": c.get("latin"),
        "latin_author": c.get("latin"),
        # Движок отдаёт КОСИНУСНУЮ БЛИЗОСТЬ, а не вероятность: у любого фото она
        # лежит в 0.75–0.96, и показывать её как «91% уверенности» — враньё.
        # Калибровка на реальных фото (2026-08-25, 120 грибов vs 120 растений):
        # грибы медиана 0.888 / p10 0.824; растения медиана 0.752 / p90 0.826.
        # Линейно растягиваем рабочий диапазон 0.78–0.95 в 0–1.
        "score": _calibrate(c.get("score")),
        "raw_similarity": c.get("score"),
        "common_names": [],
        "genus": (c.get("latin") or "").split(" ")[0] or None,
        "family": None,
        "gbif_id": None,
        "powo_id": None,
    } for c in (body.get("candidates") or [])[:limit] if c.get("latin")]
    # p_fungus — вероятность «это гриб» от классификатора царства на ТОМ ЖЕ
    # эмбеддинге (см. router: он решает, чей ответ показывать). None, если движок
    # старой версии или веса не загрузились — тогда роутер падает на пороги.
    return {"engine": body.get("engine") or "bioclip-fungi", "candidates": cands,
            "p_fungus": body.get("p_fungus")}
