"""Год издания и автор книги по её первым и последним страницам.

RFC-botanik-site §9.2. У большинства книг библиотеки пусты год и автор, а от
года зависит класс доступа: книга, изданная до 1917 года включительно,
открывается на сайте целиком (``routers/library.py``). Здесь чистая логика:
собрать текст первых и последних страниц, спросить модель, проверить ответ по
тексту и по сканам и решить, что записать сразу, а что отдать на ревью в
админку. Прогон по корпусу живёт в ``app/temporal/book_meta_activities.py``.

Почему так осторожно. Ошибочный год до 1917 опубликует целиком сканы книги, на
которую ещё действуют авторские права. Поэтому такой год записывается сам только
при трёх условиях сразу: год дословно найден в распознанном тексте, книга набрана
дореформенной орфографией, и в ней нет примет современного издания (ISBN, УДК,
ББК, «Подписано в печать», тираж, знак ©). Остальные годы до 1918 уходят на
ревью. Год после 1917 доступа не меняет и записывается сам, когда он найден
дословно или прочитан со скана без противоречий с текстом.

Модель сначала читает текст страниц. Если год там не найден или распознан с
искажением («Донецк—1 $66»), модель со зрением читает сами сканы титула, его
оборота и последней страницы.

Записываются только пустые поля: заполненный год или автор не перезаписывается.
Каждое изменение пишется в ``processing_log`` (шаг ``book_meta``) со значением до
и после, по этой записи изменение можно откатить (``revert_book_meta``).

Итог по каждой книге лежит находкой ``book.meta`` в ``data_quality_findings``.
Если что-то ждёт ревью, находка открыта, а кнопка «Исправить» в админке
записывает предложенное (исполнитель ``set_book_meta`` в ``routers/quality.py``).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from rapidfuzz import fuzz
from sqlalchemy import text

from app.config import settings
from app.database import async_session
from app.services import llm as llm_svc
from app.services import minio as minio_svc
from app.services.page_anchor import normalize

logger = logging.getLogger(__name__)

CHECK_ID = "book.meta"
LOG_STEP = "book_meta"
# Тот же порог, что LIBRARY_OPEN_UNTIL_YEAR в routers/library.py: книга с годом не
# позже него открывается целиком.
OPEN_UNTIL_YEAR = int(os.environ.get("LIBRARY_OPEN_UNTIL_YEAR", "1917"))

HEAD_PAGES = 8          # первые страницы: титул, его оборот, аннотация
TAIL_PAGES = 6          # последние: выходные данные
BODY_PAGES = 12         # середина книги: по ней меряем орфографию
PAGE_CHARS = 1600       # сколько знаков страницы уходит модели
TXT_HEAD = 8000         # книга одним текстом (txt, docx): начало
TXT_TAIL = 5000         # и конец
BODY_CHARS = 30000
MIN_CONF = 0.7          # ниже этого ответ модели идёт только на ревью
STRONG_CONF = 0.85      # год из аннотации или «прочего» места пишем сам только при такой уверенности
MIN_YEAR = 1600
SCAN_WIDTH = 1400       # ширина картинки скана для модели, px
MAX_SCANS = 4
CONCURRENCY = 3         # сколько книг идёт к модели одновременно

# Модель для текста: та же длинноконтекстная qwen3-235b-2507, что разбирает книги.
llm_svc.MODELS.setdefault("book_meta", settings.llm_model_long_context)


# ---------------------------------------------------------------- страницы книги

@dataclass
class BookPages:
    id: str
    title: str
    author: str | None
    year: int | None
    file_path: str | None
    single: bool                              # книга одним текстом (txt, docx)
    head: list[tuple[int, str]]               # (номер страницы, текст)
    tail: list[tuple[int, str]]
    body: str                                 # образец середины книги
    scan_pages: list[tuple[int, str | None]]  # (номер, PNG в MinIO или None для страницы PDF)

    def corpus(self) -> str:
        """Весь текст, который видит модель. По нему проверяются её цитаты."""
        return "\n".join(t for _, t in self.head + self.tail)


async def load_book(book_id: str) -> BookPages | None:
    async with async_session() as db:
        b = (await db.execute(text(
            "SELECT id, title, author, year, file_path FROM books WHERE id = CAST(:b AS uuid)"),
            {"b": book_id})).first()
        if not b:
            return None
        # Столбец текста зовётся txt: имя t у строки SQLAlchemy занято (Row.t это сама строка).
        head = (await db.execute(text(
            "SELECT page_number, coalesce(raw_text, '') AS txt, image_path FROM book_pages "
            "WHERE book_id = CAST(:b AS uuid) ORDER BY page_number LIMIT :n"),
            {"b": book_id, "n": HEAD_PAGES})).all()
        if not head:
            return None
        tail = (await db.execute(text(
            "SELECT page_number, coalesce(raw_text, '') AS txt, image_path FROM book_pages "
            "WHERE book_id = CAST(:b AS uuid) ORDER BY page_number DESC LIMIT :n"),
            {"b": book_id, "n": TAIL_PAGES})).all()
        mid = (head[0].page_number + tail[0].page_number) // 2
        body = (await db.execute(text(
            "SELECT coalesce(raw_text, '') AS txt FROM book_pages WHERE book_id = CAST(:b AS uuid) "
            "AND page_number BETWEEN :a AND :z ORDER BY page_number"),
            {"b": book_id, "a": mid - BODY_PAGES // 2, "z": mid + BODY_PAGES // 2})).all()

    single = len(head) == 1
    if single:
        raw = head[0].txt
        h = [(head[0].page_number, raw[:TXT_HEAD])]
        t = [(head[0].page_number, raw[-TXT_TAIL:])] if len(raw) > TXT_HEAD else []
        m = len(raw) // 2
        body_text = raw[max(0, m - BODY_CHARS // 2): m + BODY_CHARS // 2]
    else:
        h = [(r.page_number, r.txt) for r in head]
        seen = {n for n, _ in h}
        t = [(r.page_number, r.txt) for r in reversed(tail) if r.page_number not in seen]
        body_text = "\n".join(r.txt for r in body)[:BODY_CHARS]

    # Сканы для модели со зрением: первые страницы (титул и его оборот) и последняя
    # страница с текстом (выходные данные). У текстовых PDF картинки нет, страницу
    # рисуем из самого PDF.
    is_pdf = (b.file_path or "").lower().endswith(".pdf")
    scans: list[tuple[int, str | None]] = []
    if not single:
        for r in head[:3]:
            if r.image_path or is_pdf:
                scans.append((r.page_number, r.image_path))
        last = next((r for r in tail if len(r.txt.strip()) > 40), tail[0])
        if (last.image_path or is_pdf) and last.page_number not in {n for n, _ in scans}:
            scans.append((last.page_number, last.image_path))
    return BookPages(
        id=str(b.id), title=b.title or "", author=(b.author or "").strip() or None, year=b.year,
        file_path=b.file_path, single=single, head=h, tail=t, body=body_text, scan_pages=scans[:MAX_SCANS])


# ---------------------------------------------------------------- приметы книги

_WORD = re.compile(r"[А-Яа-яЁёѢѣІіѲѳѴѵ]{2,}")
_MODERN_MARKERS = {
    "isbn": re.compile(r"\bISBN\b|ІЅВ|\b97[89][-\s]?\d{1,5}[-\s]\d{1,7}[-\s]\d{1,7}[-\s]\d\b", re.I),
    "udc": re.compile(r"\bУДК\b"),
    "bbk": re.compile(r"\bББК\b"),
    "signed_to_print": re.compile(r"Подписано\s+(?:в\s+|к\s+)?печат", re.I),
    "print_run": re.compile(r"\bТираж\b", re.I),
    "copyright": re.compile(r"©"),
    "typeset": re.compile(r"Сдано\s+в\s+(?:набор|производство)", re.I),
}
_REPRINT = re.compile(r"репринт|воспроизвед\w*\s+(?:по|с)\s+издани|печата\w*\s+по\s+издани|факсимил", re.I)
# Строка из рекламы других книг издательства: цена («Ц. 45 к.», «Д. 1 р. 85 к.», «цѣна 30 к.»).
_AD_LINE = re.compile(r"\b[ЦД]\.\s*\d|ц[ѣе]н[аы]\s*\d|\d\s*(?:к\.|коп\b|р\.|руб)", re.I)
_UNKNOWN_AUTHOR = re.compile(r"^(?:автор\s+)?неизвест", re.I)


def author_empty(a: str | None) -> bool:
    """Поле автора пустое или заполнено словом «Неизвестен»."""
    s = (a or "").strip()
    return not s or bool(_UNKNOWN_AUTHOR.match(s))


def signals(bp: BookPages) -> dict:
    """Орфография середины книги и приметы современного издания на первых и последних страницах.

    Дореформенный набор узнаётся по твёрдому знаку на конце слов и по ять. В
    современном тексте их почти нет даже с ошибками распознавания, а в
    дореформенном твёрдый знак стоит на конце каждого пятого-шестого слова."""
    words = _WORD.findall(bp.body or bp.corpus())
    n = len(words)
    hard = sum(1 for w in words if w[-1] in "ъЪ")
    yat = sum(w.count("ѣ") + w.count("Ѣ") for w in words)
    hard_share = hard / n if n else 0.0
    yat_1k = yat * 1000 / n if n else 0.0
    corpus = bp.corpus()
    return {
        "words": n,
        "hard_end": round(hard_share, 3),
        "yat_per_1k": round(yat_1k, 1),
        "pre_reform": n >= 150 and (hard_share >= 0.04 or yat_1k >= 3.0),
        "modern_markers": sorted(k for k, rx in _MODERN_MARKERS.items() if rx.search(corpus)),
        "reprint": bool(_REPRINT.search(corpus)),
    }


# ---------------------------------------------------------------- модель

_TEXT_PROMPT = """Перед тобой распознанный текст первых и последних страниц одной книги, у каждой страницы указан номер. Нужно найти год выхода именно этого издания и автора книги.

Где обычно стоит год издания. На титульном листе внизу, рядом с городом: «Москва, 1985», «С.-Петербургъ, 1872». На обороте титула и в выходных данных в конце книги: «© Издательство „Колос“, 1990», «Подписано в печать 02.02.2009», «Тираж 10 000». В аннотации: «Второе издание вышло в 1986 году».

Какие годы брать нельзя. Годы из основного текста книги, из списка литературы, из рекламы других книг издательства, из предисловия, где говорится о событиях, и годы цензурного разрешения чужих книг.

Если книга перепечатывает старое издание, годом этого издания считай год перепечатки, а год оригинала запиши в original_year.

Если год распознан с искажением, например «Донецк—1 $66», напиши год, который там напечатан, и поставь ocr_garbled: true.

Автор это тот, кто написал или составил книгу, как указано на титуле. Рецензенты, редакторы серии и переводчики автором не считаются. Если на титуле есть только составитель или ответственный редактор, укажи его и допиши «(сост.)» или «(ред.)». Пиши в виде «Фамилия И.О.». Несколько авторов перечисли через запятую, а если их больше трёх, назови первых трёх и допиши «и др.». Фамилии пиши в современной орфографии: «Фроловъ» становится «Фролов». Инициалы пиши только те, что напечатаны: если на титуле полное имя без отчества («Надежда Стогова»), пиши «Стогова Н.», отчество не додумывай. Фамилию переписывай так, как она распознана, не исправляй её по другим местам книги.

Для года и для автора приведи дословный фрагмент распознанного текста, в котором они стоят, и номер страницы. Фрагмент копируй как есть, вместе с ошибками распознавания.

Если года или автора в тексте нет, верни null. Не угадывай по содержанию книги.

Ответ строго в JSON:
{"year": 1966, "year_quote": "…", "year_page": 2, "year_basis": "title|imprint|copyright|annotation|other", "ocr_garbled": false, "is_reprint": false, "original_year": null, "author": "Губергриц А.Я., Соломченко Н.И.", "author_quote": "…", "author_page": 1, "confidence": 0.9, "notes": "одна фраза о том, почему так"}"""

_SCAN_PROMPT = """Это сканы первых страниц книги (титульный лист и его оборот) и её последней страницы с текстом. Картинки пронумерованы по порядку, начиная с 1. Прочитай год выхода этого издания и автора книги так, как они напечатаны.

Год обычно стоит внизу титула рядом с городом, на обороте титула рядом со знаком © или в выходных данных на последней странице («Подписано в печать», тираж). Годы из рекламы других книг и из основного текста не бери.

Автор это тот, кто написал или составил книгу. Пиши в виде «Фамилия И.О.» в современной орфографии. Инициалы пиши только те, что напечатаны: если напечатано полное имя без отчества («Надежда Стогова»), пиши «Стогова Н.», отчество не додумывай.

Если года или автора на картинках не видно, верни null.

Ответ строго в JSON:
{"year": 1966, "year_text": "как напечатано", "image": 2, "author": "Фамилия И.О.", "author_text": "как напечатано", "confidence": 0.9}"""


def pages_block(bp: BookPages) -> str:
    parts = [f"Название книги в каталоге: «{bp.title}»"]
    if bp.single:
        n, t = bp.head[0]
        parts.append(f"--- начало текста, стр. {n} ---\n{t}")
        if bp.tail:
            parts.append(f"--- конец текста, стр. {bp.tail[0][0]} ---\n{bp.tail[0][1]}")
    else:
        for n, t in bp.head:
            parts.append(f"--- стр. {n} ---\n{t[:PAGE_CHARS]}")
        if bp.tail:
            parts.append("--- … середина книги пропущена … ---")
            for n, t in bp.tail:
                parts.append(f"--- стр. {n} ---\n{t[:PAGE_CHARS]}")
    return "\n\n".join(parts)


def _json_obj(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return raw[0] if raw and isinstance(raw[0], dict) else {}
    s = str(raw or "")
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        v = json.loads(s[a:b + 1])
    except json.JSONDecodeError:
        return {}
    return v if isinstance(v, dict) else {}


async def ask_text(bp: BookPages) -> dict:
    msgs = [{"role": "system", "content": _TEXT_PROMPT}, {"role": "user", "content": pages_block(bp)}]
    try:
        res = await llm_svc.chat_completion_json(msgs, task="book_meta", temperature=0.1, max_tokens=1200)
    except ValueError as e:
        logger.warning("book meta %s: модель по тексту не ответила: %s", bp.id, str(e)[:200])
        return {"error": str(e)[:300]}
    out = _json_obj(res)
    out["model"] = llm_svc.MODELS.get("book_meta")
    return out


def _render_scans(bp: BookPages) -> list[bytes]:
    """Картинки страниц для модели со зрением: PNG из MinIO или страница PDF."""
    import fitz  # PyMuPDF есть в образе

    out: list[bytes] = []
    pdf_doc = None
    tmp: str | None = None
    try:
        for n, img_path in bp.scan_pages:
            try:
                if img_path:
                    doc = fitz.open(stream=minio_svc.download_file(img_path), filetype="png")
                    page = doc[0]
                    scale = min(1.0, SCAN_WIDTH / max(page.rect.width, 1))
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    out.append(pix.tobytes("jpeg", jpg_quality=80))
                    doc.close()
                elif (bp.file_path or "").lower().endswith(".pdf"):
                    if pdf_doc is None:
                        fd, tmp = tempfile.mkstemp(suffix=".pdf")
                        os.close(fd)
                        with open(tmp, "wb") as fh:
                            fh.write(minio_svc.download_file(bp.file_path))
                        pdf_doc = fitz.open(tmp)
                    if 1 <= n <= pdf_doc.page_count:
                        page = pdf_doc[n - 1]
                        scale = SCAN_WIDTH / max(page.rect.width, 1)
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        out.append(pix.tobytes("jpeg", jpg_quality=80))
            except Exception as e:  # noqa: BLE001 — битая страница не должна ронять книгу
                logger.warning("book meta %s: скан стр. %s не отрисовался: %s", bp.id, n, e)
    finally:
        if pdf_doc is not None:
            pdf_doc.close()
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
    return out


async def ask_scan(bp: BookPages) -> dict:
    imgs = await asyncio.to_thread(_render_scans, bp)
    if not imgs:
        return {}
    content: list[dict] = [{"type": "text", "text": _SCAN_PROMPT}]
    for im in imgs:
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(im).decode()}})
    try:
        raw = await llm_svc.chat_completion([{"role": "user", "content": content}],
                                            task="ocr_hard", temperature=0.1, max_tokens=800)
    except ValueError as e:
        logger.warning("book meta %s: модель по сканам не ответила: %s", bp.id, str(e)[:200])
        return {"error": str(e)[:300], "images": len(imgs)}
    out = _json_obj(raw)
    out["images"] = len(imgs)
    out["pages"] = [n for n, _ in bp.scan_pages][:len(imgs)]
    out["model"] = llm_svc.MODELS.get("ocr_hard")
    return out


# ---------------------------------------------------------------- проверка ответа

def _int(v: Any) -> int | None:
    try:
        return int(str(v).strip()[:4]) if v not in (None, "", "null") else None
    except (TypeError, ValueError):
        return None


def _conf(v: Any) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def grounded(quote: str | None, corpus_norm: str) -> bool:
    """Цитата модели действительно есть в тексте страниц."""
    q = normalize(quote or "")
    if len(q) < 4 or not corpus_norm:
        return False
    if q in corpus_norm:
        return True
    return fuzz.partial_ratio(q, corpus_norm) >= 90


def year_in(quote: str | None, year: int) -> bool:
    """Год стоит в цитате цифрами (пробелы между цифрами не мешают)."""
    return str(year) in re.sub(r"[\s.,'`’]", "", quote or "")


_NOT_PERSON = re.compile(
    r"издательств|редакци|типограф|министерств|институт|академи|общество|управлени|"
    r"коллектив|университет|комитет|отдел|лаборатори|www|http|@", re.I)


def author_ok(a: str | None) -> str | None:
    """Строка похожа на имя человека: без цифр, без названий учреждений, разумной длины."""
    s = re.sub(r"\s+", " ", (a or "")).strip().strip(",;")
    if not (3 <= len(s) <= 120) or re.search(r"\d", s) or _NOT_PERSON.search(s):
        return None
    return s


def surnames(author: str) -> list[str]:
    out = []
    for part in author.split(","):
        part = re.sub(r"\(.*?\)|\bи др\.?", "", part).strip()
        if not part:
            continue
        # «Губергриц А.Я.» и «А.Я. Губергриц»: фамилия это самое длинное слово без точки.
        words = [w for w in re.findall(r"[А-Яа-яЁёѢѣІіѲѳѴѵA-Za-z-]+\.?", part) if not w.endswith(".")]
        if words:
            out.append(max(words, key=len))
    return out


def format_author(a: str) -> str:
    """Единый вид имени: современная орфография, инициалы без пробела («П.С.»),
    фамилия не капсом, не больше трёх авторов, дальше «и др.»."""
    from app.services.normalizer import normalize_orthography

    s = normalize_orthography(re.sub(r"\s+", " ", a).strip())
    s = re.sub(r"\b([А-ЯЁA-Z])\.\s+(?=[А-ЯЁA-Z]\.)", r"\1.", s)
    s = re.sub(r"\b([А-ЯЁ])([А-ЯЁ]{2,})\b", lambda m: m.group(1) + m.group(2).lower(), s)
    tail = ""
    if re.search(r"\bи др\.?\s*$", s):
        s, tail = re.sub(r",?\s*\bи др\.?\s*$", "", s), " и др."
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) > 3:
        parts, tail = parts[:3], " и др."
    return ", ".join(parts) + tail


def _same_surname(a: str, b: str) -> bool:
    sa, sb = surnames(a), surnames(b)
    if not sa or not sb:
        return True
    x, y = normalize(sa[0]), normalize(sb[0])
    return fuzz.ratio(x, y) >= 90


def author_grounded(author: str, corpus_norm: str) -> bool:
    """Фамилия первого автора есть в тексте страниц. Окончание может отличаться падежом."""
    names = surnames(author)
    if not names:
        return False
    tok = normalize(names[0])
    return len(tok) >= 3 and (tok in corpus_norm or (len(tok) >= 5 and tok[:-1] in corpus_norm))


@dataclass
class Decision:
    year: int | None = None
    year_source: str | None = None      # text | scan | scan+text | text-unverified
    year_auto: bool = False
    author: str | None = None
    author_source: str | None = None    # text | scan
    author_auto: bool = False
    reasons: list[str] = field(default_factory=list)


def page_text(bp: BookPages | None, n: int | None) -> str | None:
    if bp is None or n is None:
        return None
    return next((t for p, t in bp.head + bp.tail if p == n), None)


def text_year_ok(tres: dict, corpus_norm: str, bp: BookPages | None = None) -> bool:
    """Год из текста доказан: он стоит цифрами в цитате, а цитата есть в тексте. Короткую
    цитату вроде «1991» можно найти где угодно, поэтому её подтверждает только та
    страница, которую назвала модель."""
    ty = _int(tres.get("year"))
    quote = tres.get("year_quote")
    if not (ty and not tres.get("ocr_garbled") and year_in(quote, ty)):
        return False
    if len(normalize(quote or "")) < 12:
        page = page_text(bp, _int(tres.get("year_page")))
        return bool(page) and grounded(quote, normalize(page))
    return grounded(quote, corpus_norm)


def decide(bp: BookPages, sig: dict, tres: dict, vres: dict, corpus_norm: str) -> Decision:
    d = Decision()
    now_year = datetime.now(timezone.utc).year

    # ---- год
    if bp.year is None:
        ty, vy = _int(tres.get("year")), _int(vres.get("year"))
        tconf, vconf = _conf(tres.get("confidence")), _conf(vres.get("confidence"))
        conf = 0.0
        if text_year_ok(tres, corpus_norm, bp):
            d.year, d.year_source, conf = ty, "text", tconf
            if vy and vy != ty:
                conf = 0.0
                d.reasons.append(f"в тексте год {ty}, а на скане читается {vy}")
        elif vy:
            if ty and ty != vy:
                d.year, d.year_source, conf = vy, "scan", 0.0
                d.reasons.append(f"текст даёт {ty}, скан читается как {vy}")
            else:
                d.year, d.year_source, conf = vy, ("scan+text" if ty == vy else "scan"), vconf
        elif ty:
            d.year, d.year_source, conf = ty, "text-unverified", 0.0
            d.reasons.append("год не найден дословно в тексте страниц, а скан его не подтвердил")

        if d.year is not None and not (MIN_YEAR <= d.year <= now_year):
            d.reasons.append(f"год {d.year} вне разумных пределов")
            d.year = None

        if d.year is not None:
            y = d.year
            auto = conf >= MIN_CONF
            if not auto and conf > 0:
                d.reasons.append(f"уверенность модели {conf:.2f}")
            basis = str(tres.get("year_basis") or "")
            if d.year_source == "text" and basis not in ("title", "imprint", "copyright") and conf < STRONG_CONF:
                auto = False
                d.reasons.append(f"год взят из места «{basis or 'не указано'}», нужна проверка")
            reprint = bool(tres.get("is_reprint")) or sig.get("reprint")
            markers = sig.get("modern_markers") or []
            if y <= OPEN_UNTIL_YEAR:
                # Такой год откроет книгу целиком: только дословно из текста, подтверждённый
                # сканом титула, при дореформенном наборе и без примет современного издания.
                if d.year_source != "text":
                    auto = False
                    d.reasons.append(f"год до {OPEN_UNTIL_YEAR + 1} откроет книгу целиком, а в тексте он не найден дословно")
                elif bp.scan_pages and vy != y:
                    auto = False
                    d.reasons.append(
                        f"год до {OPEN_UNTIL_YEAR + 1} откроет книгу целиком, а скан титула его не подтвердил"
                        + (f" (читается {vy})" if vy else ""))
                if _AD_LINE.search(tres.get("year_quote") or ""):
                    auto = False
                    d.reasons.append("год стоит в строке с ценой, похоже на рекламу другой книги")
                if not sig.get("pre_reform"):
                    auto = False
                    d.reasons.append(f"год до {OPEN_UNTIL_YEAR + 1}, а книга набрана современной орфографией")
                if markers:
                    auto = False
                    d.reasons.append("год до 1918, но есть приметы современного издания: " + ", ".join(markers))
                if reprint:
                    auto = False
                    d.reasons.append("похоже на перепечатку старого издания")
            else:
                if sig.get("pre_reform"):
                    auto = False
                    d.reasons.append(f"дореформенная орфография при годе {y}: перепечатка или эмигрантское издание")
                if "isbn" in markers and y < 1970:
                    auto = False
                    d.reasons.append(f"есть ISBN, а год {y} слишком ранний")
            d.year_auto = auto

    # ---- автор
    if author_empty(bp.author):
        ta = author_ok(tres.get("author"))
        va = author_ok(vres.get("author"))
        ta = format_author(ta) if ta else None
        va = format_author(va) if va else None
        cand: str | None = None
        found, conf = False, 0.0
        if ta:
            # Автор из текста: цитата с ним должна быть в тексте страниц, и фамилия
            # должна стоять именно в этой цитате, а не где-то в книге.
            cand, d.author_source, conf = ta, "text", _conf(tres.get("confidence"))
            quote = tres.get("author_quote")
            found = grounded(quote, corpus_norm) and author_grounded(ta, normalize(quote or ""))
            conflict = bool(va) and not _same_surname(ta, va)
            if conflict:
                # Распознавание исказило фамилию («Чинов» вместо «Чиков»): предлагаем
                # прочитанное со скана, решает человек.
                d.reasons.append(f"в тексте фамилия автора «{surnames(ta)[0]}», на скане «{surnames(va)[0]}»")
                cand, d.author_source, found = va, "scan", False
        elif va:
            # Автор со скана титула: модель прочитала его с картинки, а в тексте страниц
            # должна найтись хотя бы фамилия.
            cand, d.author_source, conf = va, "scan", _conf(vres.get("confidence"))
            found, conflict = author_grounded(va, corpus_norm), False
        if cand:
            d.author = cand
            if not found and not conflict:
                first = (surnames(cand) or [cand])[0]
                d.reasons.append(f"фамилия автора «{first}» не найдена там, где модель указала автора")
            if conf < MIN_CONF:
                d.reasons.append(f"уверенность модели в авторе {conf:.2f}")
            d.author_auto = found and conf >= MIN_CONF
    return d


# ---------------------------------------------------------------- запись в базу

async def apply_meta(db, book_id: str, year: int | None, author: str | None, *,
                     by: str, finding: str | None = None) -> dict:
    """Записать год и автора, только если поле пустое. Пишет журнал для отката.
    Коммитит вызывающий."""
    row = (await db.execute(text(
        "SELECT year, author FROM books WHERE id = CAST(:b AS uuid) FOR UPDATE"), {"b": book_id})).first()
    if not row:
        return {}
    sets: dict[str, Any] = {}
    if year is not None and row.year is None:
        sets["year"] = int(year)
    if author and author_empty(row.author):
        sets["author"] = author.strip()
    if not sets:
        return {}
    assign = ", ".join(f"{k} = :{k}" for k in sets)
    await db.execute(text(f"UPDATE books SET {assign}, updated_at = now() WHERE id = CAST(:b AS uuid)"),
                     {**sets, "b": book_id})
    details = {"before": {"year": row.year, "author": row.author}, "after": sets, "by": by, "finding": finding}
    await db.execute(text(
        "INSERT INTO processing_log (id, book_id, step, status, details, created_at) "
        "VALUES (CAST(:i AS uuid), CAST(:b AS uuid), :s, 'completed', CAST(:d AS jsonb), now())"),
        {"i": str(uuid.uuid4()), "b": book_id, "s": LOG_STEP, "d": json.dumps(details, ensure_ascii=False)})
    return sets


async def revert_book_meta(by_prefix: str = "book-meta") -> dict:
    """Откатить записанное прогоном: вернуть поле к прежнему значению, если оно с тех пор
    не менялось. ``by_prefix`` выбирает записи журнала по полю ``by``."""
    reverted = skipped = 0
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT id, book_id, details FROM processing_log WHERE step = :s "
            "AND details->>'by' LIKE :p ORDER BY created_at DESC"),
            {"s": LOG_STEP, "p": by_prefix + "%"})).all()
        for r in rows:
            det = r.details or {}
            after, before = det.get("after") or {}, det.get("before") or {}
            cur = (await db.execute(text("SELECT year, author FROM books WHERE id = :b"), {"b": r.book_id})).first()
            if not cur:
                continue
            sets = {k: before.get(k) for k, v in after.items() if getattr(cur, k) == v}
            if not sets:
                skipped += 1
                continue
            assign = ", ".join(f"{k} = :{k}" for k in sets)
            await db.execute(text(f"UPDATE books SET {assign} WHERE id = :b"), {**sets, "b": r.book_id})
            await db.execute(text(
                "UPDATE processing_log SET status = 'reverted' WHERE id = :i"), {"i": r.id})
            reverted += 1
        await db.commit()
    return {"reverted": reverted, "skipped": skipped}


def _pending(bp_year: int | None, bp_author: str | None, dec: dict, applied: dict | None) -> dict:
    """Что из предложенного ещё не записано и ждёт человека."""
    applied = applied or {}
    out: dict[str, Any] = {}
    if dec.get("year") is not None and bp_year is None and "year" not in applied:
        out["year"] = dec["year"]
    if dec.get("author") and author_empty(bp_author) and "author" not in applied:
        out["author"] = dec["author"]
    return out


def _title(title: str, pending: dict, applied: dict | None, auto_left: bool,
           need_year: bool = True, need_author: bool = True) -> str:
    name = f"«{title[:90]}»"
    # Чего не нашлось из того, что было пусто: дописывается в конец заголовка.
    got_year = "year" in pending or bool(applied and applied.get("year"))
    got_author = "author" in pending or bool(applied and applied.get("author"))
    missing = [w for w, need, got in (("год", need_year, got_year), ("автор", need_author, got_author))
               if need and not got]
    miss = ""
    if missing:
        miss = " и ".join(missing) + (" не найдены" if len(missing) > 1 else " не найден")
    done = []
    if applied and applied.get("year"):
        done.append(f"год {applied['year']}")
    if applied and applied.get("author"):
        done.append(f"автор {applied['author']}")
    todo = []
    if "year" in pending:
        y = pending["year"]
        todo.append(f"год {y}" + (" (откроет книгу целиком)" if y <= OPEN_UNTIL_YEAR else ""))
    if "author" in pending:
        todo.append(f"автор {pending['author']}")
    def written(items: list[str]) -> str:
        return ("записаны " if len(items) > 1 else "записан ") + " и ".join(items)

    if todo:
        verb = ("предложены" if len(todo) > 1 else "предложен") if auto_left else "проверить"
        return (f"{name}: {verb} " + " и ".join(todo) + (f"; {written(done)}" if done else "")
                + (f"; {miss}" if miss else ""))
    if done:
        return f"{name}: {written(done)}" + (f"; {miss}" if miss else "")
    return f"{name}: {miss or 'год и автор не найдены'} на первых и последних страницах"


async def save_finding(bp: BookPages, sig: dict, tres: dict, vres: dict, dec: Decision,
                       applied: dict | None) -> str:
    """``applied`` None значит сухой прогон: ничего не записано, предложено всё."""
    dec_d = asdict(dec)
    pending = _pending(bp.year, bp.author, dec_d, applied)
    # В сухом прогоне в «Исправить» уходит всё предложенное, иначе только то, что ждёт ревью.
    if applied is not None:
        pending = {k: v for k, v in pending.items()
                   if not (k == "year" and dec.year_auto) and not (k == "author" and dec.author_auto)}
    auto_left = applied is None and (dec.year_auto or dec.author_auto)
    risky = "year" in pending and pending["year"] <= OPEN_UNTIL_YEAR
    status = "open" if pending or not (applied or dec.year or dec.author) else "fixed"
    evidence: dict[str, Any] = {
        "book": {"id": bp.id, "title": bp.title, "year_before": bp.year, "author_before": bp.author,
                 "pages_seen": [n for n, _ in bp.head + bp.tail]},
        "signals": sig,
        "text": {k: tres.get(k) for k in ("year", "year_quote", "year_page", "year_basis", "ocr_garbled",
                                          "is_reprint", "original_year", "author", "author_quote",
                                          "author_page", "confidence", "notes", "model", "error")
                 if tres.get(k) is not None},
        "scan": vres or None,
        "decision": dec_d,
    }
    if applied is not None:
        evidence["applied"] = applied
    fix = {"action": "set_book_meta", "book_id": bp.id, **pending} if pending else None
    conf = max(_conf(tres.get("confidence")), _conf(vres.get("confidence")))
    fid = str(uuid.uuid4())
    async with async_session() as db:
        row = (await db.execute(text("""
            INSERT INTO data_quality_findings
              (id, check_id, severity, entity_type, entity_id, title, evidence, suggested_fix,
               auto_fixable, status, first_seen, last_seen, resolved_by, resolved_at,
               llm_verdict, llm_confidence, llm_action, llm_reasoning, llm_model, llm_at)
            VALUES (CAST(:id AS uuid), :cid, :sev, 'book', :eid, :title, CAST(:ev AS jsonb),
                    CAST(:fix AS jsonb), :af, :st, now(), now(), :rb, :ra,
                    :lv, :lc, :la, :lr, :lm, now())
            ON CONFLICT (check_id, entity_id) DO UPDATE SET
              severity = EXCLUDED.severity, title = EXCLUDED.title, evidence = EXCLUDED.evidence,
              suggested_fix = EXCLUDED.suggested_fix, auto_fixable = EXCLUDED.auto_fixable,
              status = EXCLUDED.status, last_seen = now(), resolved_by = EXCLUDED.resolved_by,
              resolved_at = EXCLUDED.resolved_at, llm_verdict = EXCLUDED.llm_verdict,
              llm_confidence = EXCLUDED.llm_confidence, llm_action = EXCLUDED.llm_action,
              llm_reasoning = EXCLUDED.llm_reasoning, llm_model = EXCLUDED.llm_model, llm_at = now()
            RETURNING id"""), {
            "id": fid, "cid": CHECK_ID, "sev": "P1" if risky else "P2", "eid": bp.id,
            "title": _title(bp.title, pending, applied, auto_left,
                            bp.year is None, author_empty(bp.author))[:300],
            "ev": json.dumps(evidence, ensure_ascii=False, default=str),
            "fix": json.dumps(fix, ensure_ascii=False) if fix else None,
            "af": bool(fix), "st": status,
            "rb": "book-meta" if status == "fixed" else None,
            "ra": datetime.now(timezone.utc) if status == "fixed" else None,
            "lv": "real" if (dec.year or dec.author) else "uncertain", "lc": conf,
            "la": "set_book_meta" if fix else None,
            "lr": "; ".join(dec.reasons)[:1000] or (tres.get("notes") or None),
            "lm": tres.get("model") or vres.get("model"),
        })).first()
        await db.commit()
    return str(row.id) if row else fid


# ---------------------------------------------------------------- прогон

async def pending_books(force: bool) -> list[str]:
    """Книги с пустым годом или автором, у которых есть страницы. Уже разобранные
    (с находкой book.meta) пропускаются: так прогон продолжается после перезапуска."""
    async with async_session() as db:
        rows = (await db.execute(text("""
            SELECT b.id FROM books b
            WHERE (b.year IS NULL OR coalesce(trim(b.author), '') = '' OR b.author ~* '^(автор\\s+)?неизвест')
              AND EXISTS (SELECT 1 FROM book_pages p WHERE p.book_id = b.id)
              AND (CAST(:force AS boolean) OR NOT EXISTS (
                    SELECT 1 FROM data_quality_findings f
                    WHERE f.check_id = :cid AND f.entity_id = b.id::text))
            ORDER BY (b.status = 'indexed') DESC, b.title"""), {"force": force, "cid": CHECK_ID})).all()
    return [str(r.id) for r in rows]


async def process_book(book_id: str, apply: bool) -> dict:
    bp = await load_book(book_id)
    if bp is None:
        return {"skipped": True}
    sig = signals(bp)
    corpus_norm = normalize(bp.corpus())
    tres = await ask_text(bp)
    vres: dict = {}
    # Скан титула читаем у каждой книги, где он есть: он подтверждает год (обязательно,
    # если год откроет книгу целиком) и ловит фамилии, искажённые распознаванием.
    if bp.scan_pages and (bp.year is None or author_empty(bp.author)):
        vres = await ask_scan(bp)
    dec = decide(bp, sig, tres, vres, corpus_norm)
    applied: dict | None = None
    if apply:
        applied = {}
        if dec.year_auto or dec.author_auto:
            async with async_session() as db:
                applied = await apply_meta(db, bp.id, dec.year if dec.year_auto else None,
                                           dec.author if dec.author_auto else None, by="book-meta:auto")
                await db.commit()
    await save_finding(bp, sig, tres, vres, dec, applied)
    return {"decision": dec, "applied": applied, "scan": bool(vres)}


async def apply_dry_findings() -> dict:
    """После сухого прогона: записать то, что решение пометило как надёжное, не спрашивая
    модель заново. Остальное остаётся на ревью."""
    done = {"year": 0, "author": 0}
    async with async_session() as db:
        rows = (await db.execute(text(
            "SELECT id, entity_id, evidence FROM data_quality_findings "
            "WHERE check_id = :cid AND status = 'open' AND evidence->'applied' IS NULL "
            "AND evidence->'decision' IS NOT NULL"), {"cid": CHECK_ID})).all()
    for r in rows:
        ev = r.evidence or {}
        dec = ev.get("decision") or {}
        book = ev.get("book") or {}
        async with async_session() as db:
            applied = await apply_meta(db, r.entity_id, dec.get("year") if dec.get("year_auto") else None,
                                       dec.get("author") if dec.get("author_auto") else None,
                                       by="book-meta:auto", finding=str(r.id))
            cur = (await db.execute(text("SELECT year, author FROM books WHERE id = CAST(:b AS uuid)"),
                                    {"b": r.entity_id})).first()
            pending = _pending(book.get("year_before"), book.get("author_before"), dec, applied)
            pending = {k: v for k, v in pending.items()
                       if not (k == "year" and (dec.get("year_auto") or (cur and cur.year is not None)))
                       and not (k == "author" and (dec.get("author_auto") or (cur and not author_empty(cur.author))))}
            ev["applied"] = applied
            fix = {"action": "set_book_meta", "book_id": r.entity_id, **pending} if pending else None
            status = "open" if pending or not (dec.get("year") or dec.get("author")) else "fixed"
            # Один параметр нельзя ставить и в столбец varchar, и в сравнение с текстом:
            # asyncpg выводит для него разные типы. Поэтому «кем и когда закрыто» считаем здесь.
            fixed = status == "fixed"
            await db.execute(text("""
                UPDATE data_quality_findings SET evidence = CAST(:ev AS jsonb), suggested_fix = CAST(:fix AS jsonb),
                  auto_fixable = :af, status = :st, title = :title, last_seen = now(),
                  resolved_by = COALESCE(CAST(:rb AS varchar), resolved_by),
                  resolved_at = CASE WHEN CAST(:closed AS boolean) THEN now() ELSE resolved_at END
                WHERE id = :i"""), {
                "ev": json.dumps(ev, ensure_ascii=False, default=str),
                "fix": json.dumps(fix, ensure_ascii=False) if fix else None,
                "af": bool(fix), "st": status, "i": r.id,
                "rb": "book-meta" if fixed else None, "closed": fixed,
                "title": _title(book.get("title") or "", pending, applied, False,
                                book.get("year_before") is None, author_empty(book.get("author_before")))[:300]})
            await db.commit()
        done["year"] += int("year" in applied)
        done["author"] += int("author" in applied)
    return done


async def run_book_meta(*, apply: bool, limit: int = 0, force: bool = False,
                        progress: Callable[[dict], None] | None = None,
                        counters: dict | None = None) -> dict:
    keys = ("books", "scan", "year_auto", "author_auto", "year_review", "author_review",
            "nothing", "errors", "year_set", "author_set")
    c: dict[str, int] = {k: 0 for k in keys}
    for k, v in (counters or {}).items():
        if k in c and isinstance(v, (int, float)):
            c[k] = int(v)
    ids = await pending_books(force)
    if limit:
        ids = ids[:limit]
    logger.info("book meta: к разбору %d книг (apply=%s)", len(ids), apply)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(bid: str) -> None:
        async with sem:
            try:
                r = await process_book(bid, apply)
            except Exception:  # noqa: BLE001 — одна книга не должна ронять прогон
                c["errors"] += 1
                logger.exception("book meta %s: ошибка", bid)
                return
            if r.get("skipped"):
                return
            dec: Decision = r["decision"]
            c["books"] += 1
            c["scan"] += int(r["scan"])
            if dec.year is not None:
                c["year_auto" if dec.year_auto else "year_review"] += 1
            if dec.author:
                c["author_auto" if dec.author_auto else "author_review"] += 1
            if dec.year is None and not dec.author:
                c["nothing"] += 1
            applied = r.get("applied") or {}
            c["year_set"] += int("year" in applied)
            c["author_set"] += int("author" in applied)
            if progress:
                progress(dict(c))

    await asyncio.gather(*(one(b) for b in ids))
    if apply:
        tail = await apply_dry_findings()
        c["year_set"] += tail["year"]
        c["author_set"] += tail["author"]
    logger.info("book meta: готово %s", c)
    return c
