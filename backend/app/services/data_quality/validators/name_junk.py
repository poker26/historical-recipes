"""identity.name_junk (P0) — в отображаемом имени вида лежит не имя.

Пойман в проде 2026-08-27: в «Эфире» на пол-экрана растянулось
«Липа коринфскаяhttps://www.google.ru/books/edition/Zapiski_Gosud_Nikitskogo…».
Имя `name_modern` пришло от LLM, которая нормализовала OCR-строку вместе с
приклеенной к ней ссылкой на источник, и на записи это никто не проверил.

Имя вида — короткая подпись, она уезжает в ленту, в карточку, в пуш и в
магазинные скриншоты. Ссылка, разметка или хвост в сотню символов там означают,
что поле заполнено мусором, а не что название такое длинное.
"""
import re

from sqlalchemy import select

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, validator

# Ссылка в любом виде + типичные хвосты разметки. Кириллица/латиница с дефисами,
# скобками и запятыми — нормальное имя, его не трогаем.
_URL_RE = re.compile(r"(https?://|www\.|%[0-9A-Fa-f]{2}|<[a-z]+>)", re.IGNORECASE)
_MAX_NAME = 90          # «Пижма обыкновенная (Глистник, Дикая рябина, …)» = 88, это ещё имя

# Длинное — не всегда мусор: в алиасах законно живут перечни иноязычных названий
# («Нем. Birkenpilz, Rauher Locherschwamm, …», «Франц. Bolete des polonais, …») и
# списки народных имён. Это данные, а не поломка, и держать их в очереди P0 —
# значит приучить смотреть мимо неё. А вот проза («Купула, ОКРУЖАЮЩАЯ соцветие,
# густо ПОКРЫТА…») мусор и по длине, и по сути.
_LANG_PREFIX = re.compile(r"^(нем\.|немецкое|франц\.|французское|англ\.|английское|итал\.|"
                          r"исп\.|польск\.|швед\.|голл\.|браз\.|укр\.|чешск\.|латыш\.|"
                          r"der |die |das )", re.IGNORECASE)
_TYPO_JUNK = re.compile(r"[!@$/\{}\[\]|+]|\d")
_PROSE_WORD = re.compile(r"\w+(ющая|ющий|ющее|ющими|ющих|нная|нное|нными|нных|"
                         r"ется|ится|вшая|вший|покрыт)", re.IGNORECASE)


def _is_name_list(value: str) -> bool:
    """Перечень названий, а не поломка: пометка языка либо три и больше запятых
    без цифр и типографского сора. Одного причастия мало (в сортах аконита честно
    стоит «вьющийся»), прозой считаем от двух."""
    if len(_PROSE_WORD.findall(value)) >= 2:
        return False
    return bool(_LANG_PREFIX.match(value)) or (
        value.count(",") >= 3 and not _TYPO_JUNK.search(value))


def _junk_reason(value: str | None) -> str | None:
    if not value:
        return None
    if _URL_RE.search(value):
        return "ссылка или разметка внутри имени"
    if len(value) > _MAX_NAME and not _is_name_list(value):
        return f"длина {len(value)} символов — это уже не подпись"
    return None


@validator("identity.name_junk", severity="P0", auto_fixable=True,
           description="ссылка/разметка/непомерная длина в name или name_modern")
async def check_name_junk(db) -> list[Finding]:
    rows = (await db.execute(select(Plant.id, Plant.name, Plant.name_modern,
                                    Plant.name_latin, Plant.names_historical))).all()
    findings: list[Finding] = []
    for pid, name, modern, latin, hist in rows:
        problems = {}
        # Исторические имена — тоже поле показа И вход матчера: алиас со ссылкой на
        # хвосте не совпадёт ни с чем (58 таких карточек, найдены после «Липы»).
        bad_aliases = [(h, _junk_reason(h)) for h in (hist or []) if _junk_reason(h)]
        if bad_aliases:
            # Причину берём настоящую: помимо ссылок сюда попадают OCR-хвосты
            # библиографии («B. junceum Pall.— Pallas. (1795). 48.— …»), а это не
            # алиас вовсе и обрезанием не чинится — такие надо выбрасывать.
            reasons = sorted({r for _, r in bad_aliases})
            problems["names_historical"] = {
                "value": [h for h, _ in bad_aliases[:5]], "reason": "; ".join(reasons),
                "cleaned": [clean_display_name(h) for h, _ in bad_aliases[:5]]}
        for field, value in (("name", name), ("name_modern", modern)):
            reason = _junk_reason(value)
            if reason:
                problems[field] = {"value": value, "reason": reason,
                                   "cleaned": clean_display_name(value)}
        if not problems:
            continue
        findings.append(Finding(
            check_id="identity.name_junk", severity="P0",
            entity_type="plant", entity_id=str(pid),
            title=f"«{(name or latin or '')[:40]}»: в имени мусор "
                  f"({', '.join(f'{k}: {v['reason']}' for k, v in problems.items())})",
            evidence={"plant_latin": latin, "fields": problems},
            suggested_fix={"action": "clean_name", "plant_id": str(pid),
                           "fields": {k: v["cleaned"] for k, v in problems.items()}},
            auto_fixable=True,
        ))
    return findings


def clean_display_name(value: str | None) -> str | None:
    """Отрезать от имени приклеенный хвост (ссылку/разметку) и лишние пробелы.

    Применяется И на записи (см. вызовы), И как авто-починка находки: имя, у
    которого отрезали ссылку, остаётся нормальным именем — «Липа коринфская».
    Если после чистки не осталось букв, возвращаем None — пусть лучше пусто, чем
    огрызок."""
    if not value:
        return None
    cut = re.split(r"https?://|www\.", value, maxsplit=1)[0]
    cut = re.sub(r"<[^>]{1,20}>", " ", cut)
    cut = re.sub(r"\s+", " ", cut).strip(" .,;:-—·")
    return cut or None
