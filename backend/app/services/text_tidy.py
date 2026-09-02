"""Следы OCR в тексте, который читает человек.

Корпус набран из сканов, и часть повреждений распознавания доезжает до карточки:
«усталости , анемии .» (пробел перед знаком), «голе­ни» (мягкий перенос внутри
слова), «W illfo r t» (слово растащено пробелами), «дь!м» («!» вместо «ы»).
Замер 2026-09-02: из 13 655 читательских монографов повреждения видны в 262
lead_fact и в 7 крючках витрины.

Здесь две разные вещи, и их нельзя путать:

* :func:`tidy` — БЕЗОПАСНЫЙ ремонт на показе. Только то, что нельзя понять иначе:
  мягкий перенос, пробел перед знаком препинания, пробел внутри скобок. Текст
  в базе не трогаем — он цитата из источника, и восстанавливать её догадками
  нельзя.
* :func:`damage` — распознавание того, что ремонту НЕ поддаётся: растащенное
  слово или «!» вместо буквы. Такой текст лучше не показывать вовсе, чем
  показывать сломанным.

Дореформенная орфография («дѣйствіе», «съ», «ѳ») — НЕ повреждение, а подлинное
написание источника: она проходит обе функции нетронутой.
"""
import re

_SOFT_HYPHEN = "­"
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t ]+([,.;:!?»)])")
_SPACE_AFTER_OPEN = re.compile(r"([«(])[ \t ]+")
_MULTISPACE = re.compile(r"[ \t ]{2,}")

# Неремонтируемое: заглавная буква и два обрывка подряд («W illfo r t»),
# восклицательный знак посреди слова (OCR так теряет «ы»).
_LATIN_SPLIT = re.compile(r"\b[A-Z][a-z]{0,3}\s+[a-z]{1,4}\s+[a-z]{1,3}\b")
_BANG_IN_WORD = re.compile(r"[а-яёѣіѳѵ]![а-яёѣіѳѵ]|ь!")


def tidy(value: str | None) -> str | None:
    """Убрать следы вёрстки, не трогая слова. Идемпотентна."""
    if not value:
        return value
    t = value.replace(_SOFT_HYPHEN, "")
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    t = _SPACE_AFTER_OPEN.sub(r"\1", t)
    return _MULTISPACE.sub(" ", t).strip()


def damage(value: str | None) -> list[str]:
    """Что в тексте сломано непоправимо (после :func:`tidy`). Пусто — текст годен."""
    if not value:
        return []
    t = tidy(value) or ""
    out = []
    if _LATIN_SPLIT.search(t):
        out.append("слово растащено пробелами")
    if _BANG_IN_WORD.search(t):
        out.append("«!» вместо буквы")
    return out


def tidy_deep(obj):
    """Пройти tidy по всем строкам структуры (монограф — вложенные словари/списки)."""
    if isinstance(obj, str):
        return tidy(obj)
    if isinstance(obj, list):
        return [tidy_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: tidy_deep(v) for k, v in obj.items()}
    return obj
