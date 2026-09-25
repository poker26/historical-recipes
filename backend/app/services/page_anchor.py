"""Привязка дословной цитаты к странице скана.

Каждый факт корпуса (применение, рецепт, кулинарная запись, сбор, ареал,
токсичность, применение масла, упоминание) хранит книгу и дословный текст, но
не знает, с какой страницы он взят: сборка книги склеивала распознанные
страницы в один текст, чистила его моделью, а у дореформенных книг ещё и
переводила орфографию, и граница страниц терялась. Сам распознанный текст
каждой страницы при этом сохранён в ``book_pages.raw_text``.

Здесь чистая логика поиска, без базы и без Temporal: подготовить страницы
книги, подготовить цитату, найти страницу. Активность в
``app/temporal/anchor_activities.py`` гоняет её по корпусу.

Как ищем. Сначала точно: три окна по сорок знаков из цитаты ищутся как
подстроки на каждой странице. Если окна нашлись на одной странице или на двух
соседних, это ответ с оценкой 100. Если точно не нашлось, ищем нечётко:
по словам цитаты выбираем несколько страниц-кандидатов (обратный индекс
слов книги), и на каждой считаем ``partial_ratio`` начала цитаты против
текста страницы. Лучшая страница берётся, когда её оценка не ниже порога и
заметно выше второй. Иначе ответа нет, и это тоже записывается, чтобы не
искать повторно.

Текст и страниц, и цитаты приводится к одному виду: нижний регистр, одна
форма пробелов, убраны переносы слов по строкам, дореформенные буквы
переведены в современные (цитаты дореформенных книг брались уже из
переведённого текста).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.services.normalizer import normalize_orthography

# Пороги нечёткого поиска. 85 подобрано так, чтобы ошибки распознавания в
# нескольких буквах не мешали, а случайно похожие абзацы не проходили.
FUZZY_MIN_SCORE = 85
FUZZY_MIN_MARGIN = 4          # лучшая страница должна опережать вторую хотя бы на столько
FUZZY_CANDIDATES = 8          # сколько страниц-кандидатов проверять нечётко
WINDOW = 40                   # длина окна точного поиска, знаков
NEEDLE = 140                  # сколько знаков начала цитаты идёт в нечёткий поиск
MIN_QUOTE = 25                # короче этого цитату не привязываем: слишком много совпадений

_WS = re.compile(r"\s+")
_HYPHEN_BREAK = re.compile(r"(\w)[-¬­]\s*\n\s*(\w)")   # «рас-\nтение» → «растение»
_SOFT = re.compile(r"[­¬]")                 # мягкий перенос и знак переноса
_TOKEN = re.compile(r"[а-яёa-z]{5,}")


def normalize(text: str | None) -> str:
    """Приводит текст страницы или цитаты к одному виду для сравнения."""
    if not text:
        return ""
    t = _HYPHEN_BREAK.sub(r"\1\2", text)
    t = _SOFT.sub("", t)
    t = normalize_orthography(t)
    t = t.replace("ё", "е")
    t = _WS.sub(" ", t).strip().lower()
    return t


@dataclass
class Page:
    number: int
    text: str            # уже нормализованный


@dataclass
class BookIndex:
    """Страницы одной книги плюс обратный индекс слов для отбора кандидатов."""
    pages: list[Page]
    postings: dict[str, set[int]]     # слово → номера страниц, где оно есть

    @classmethod
    def build(cls, raw_pages: list[tuple[int, str | None]]) -> "BookIndex":
        pages: list[Page] = []
        postings: dict[str, set[int]] = defaultdict(set)
        for number, raw in raw_pages:
            text = normalize(raw)
            if not text:
                continue
            pages.append(Page(number, text))
            for tok in set(_TOKEN.findall(text)):
                postings[tok].add(number)
        return cls(pages=pages, postings=dict(postings))

    def candidates(self, quote: str, limit: int = FUZZY_CANDIDATES) -> list[Page]:
        """Страницы, где встречается больше всего слов цитаты. Редкие слова
        весят больше частых, чтобы «настой» и «принимать» не тянули всю книгу."""
        toks = set(_TOKEN.findall(quote))
        if not toks or not self.pages:
            return []
        n_pages = len(self.pages)
        score: dict[int, float] = defaultdict(float)
        for tok in toks:
            hit = self.postings.get(tok)
            if not hit:
                continue
            weight = 1.0 / (1.0 + len(hit) / max(n_pages, 1) * 10)
            for num in hit:
                score[num] += weight
        if not score:
            return []
        best = sorted(score.items(), key=lambda kv: -kv[1])[:limit]
        wanted = {num for num, _ in best}
        return [p for p in self.pages if p.number in wanted]


@dataclass
class Anchor:
    page: int | None
    score: int
    method: str          # exact | fuzzy | none | short


def _windows(quote: str) -> list[str]:
    n = len(quote)
    if n <= WINDOW:
        return [quote]
    starts = {0, n // 3, n // 2}
    out = []
    for s in sorted(starts):
        w = quote[s:s + WINDOW]
        if len(w) >= MIN_QUOTE:
            out.append(w)
    return out


def locate(index: BookIndex, quote_raw: str | None) -> Anchor:
    """Ищет страницу для одной цитаты. Возвращает страницу, оценку и способ."""
    quote = normalize(quote_raw)
    if len(quote) < MIN_QUOTE:
        return Anchor(None, 0, "short")
    if not index.pages:
        return Anchor(None, 0, "none")

    # 1. Точный поиск окнами.
    windows = _windows(quote)
    hits: dict[int, int] = defaultdict(int)
    first_window_pages: list[int] = []
    for i, w in enumerate(windows):
        for p in index.pages:
            if w in p.text:
                hits[p.number] += 1
                if i == 0:
                    first_window_pages.append(p.number)
    if hits:
        nums = sorted(hits)
        # Одна страница или тесная группа соседних страниц (цитата через разворот).
        if len(nums) == 1 or nums[-1] - nums[0] <= 2:
            start = min(first_window_pages) if first_window_pages else nums[0]
            return Anchor(start, 100, "exact")
        # Окна разошлись по книге: берём страницу, где совпало больше окон,
        # если такая одна и явно впереди.
        ranked = sorted(hits.items(), key=lambda kv: -kv[1])
        if ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            return Anchor(ranked[0][0], 95, "exact")

    # 2. Нечёткий поиск по страницам-кандидатам.
    needle = quote[:NEEDLE]
    cands = index.candidates(quote)
    if not cands:
        return Anchor(None, 0, "none")
    scored = sorted(
        ((int(fuzz.partial_ratio(needle, p.text)), p.number) for p in cands),
        reverse=True,
    )
    best_score, best_page = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0
    if best_score >= FUZZY_MIN_SCORE and best_score - second >= FUZZY_MIN_MARGIN:
        return Anchor(best_page, best_score, "fuzzy")
    # Две соседние страницы с одинаково высокой оценкой: цитата на разворот.
    if best_score >= FUZZY_MIN_SCORE and len(scored) > 1 \
            and abs(scored[1][1] - best_page) <= 1 and scored[1][0] >= FUZZY_MIN_SCORE:
        return Anchor(min(best_page, scored[1][1]), best_score, "fuzzy")
    return Anchor(None, best_score, "none")
