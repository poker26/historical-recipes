"""OCR service: Tesseract primary + vision-LLM fallback via OpenRouter.

Tesseract handles most pages. For low-confidence pages we fall back to a
multimodal LLM (Qwen3-VL) which handles unusual fonts and degraded scans
better. The fallback is best-effort: if the vision call fails it degrades to
the Tesseract text rather than aborting extraction.

Indra OCR is an optional first engine (``engine="indra"``): about ten times
faster than Tesseract on our book scans and it keeps Latin plant names that
the Russian-only Tesseract turns into Cyrillic noise. It has no ѣ/ѳ/ѵ, so
pre-reform books stay on Tesseract.
"""

import base64
import io
import logging
import re
import tempfile
import threading

import pytesseract
from PIL import Image

# Trusted high-DPI book scans can exceed PIL's decompression-bomb limit (~178M px)
# and crash extract with DecompressionBombError, wedging the book in retries.
# These are our own scans, not attacks → lift the cap.
Image.MAX_IMAGE_PIXELS = None

from app.config import settings
from app.services.llm import chat_completion

logger = logging.getLogger(__name__)


def ocr_page(image_bytes: bytes) -> tuple[str, float]:
    """OCR a single page image with Tesseract.

    Returns (text, avg_confidence).
    Confidence is 0-100, where 100 is perfect.
    """
    image = Image.open(io.BytesIO(image_bytes))

    # Get detailed data with confidence per word
    data = pytesseract.image_to_data(
        image, lang=settings.tesseract_lang, output_type=pytesseract.Output.DICT
    )

    # Calculate average confidence (exclude -1 which means no text detected)
    confidences = [c for c in data["conf"] if c > 0]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Get full text
    text = pytesseract.image_to_string(image, lang=settings.tesseract_lang)

    return text.strip(), avg_confidence


# ----------------------------------------------------------------- Indra OCR

# Indra читает оба алфавита сразу и поэтому иногда ставит в слово букву-двойник
# из чужого алфавита: «A. сrassinervium» с кириллической «с», «Mадагаскаре» с
# латинской «M». Дословная сверка цитат на таком слове проваливается, а латынь не
# находит карточку. Слово приводим к алфавиту большинства его букв. Таблицы
# собраны по тому, что движок путает на деле (проба 19.10.2026), а не только по
# начертанию: «u» встаёт на место «и» («сегуuна»).
_CYR_TO_LAT = str.maketrans({
    "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "n", "о": "o", "р": "p",
    "с": "c", "т": "t", "у": "y", "х": "x", "і": "i",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "У": "Y", "Х": "X", "І": "I",
})
_LAT_TO_CYR = str.maketrans({
    "a": "а", "e": "е", "k": "к", "m": "м", "o": "о", "p": "р", "c": "с", "x": "х",
    "y": "у", "u": "и", "n": "п", "r": "г",
    "A": "А", "B": "В", "E": "Е", "K": "К", "M": "М", "H": "Н", "O": "О", "P": "Р",
    "C": "С", "T": "Т", "X": "Х", "Y": "У",
})
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁёІі]+")


def fold_mixed_scripts(text: str) -> str:
    """Приводит каждое слово со смешанными алфавитами к алфавиту большинства."""
    def fix(match: re.Match) -> str:
        word = match.group(0)
        lat = sum(1 for c in word if c.isascii())
        cyr = len(word) - lat
        if not lat or not cyr or lat == cyr:
            return word
        return word.translate(_CYR_TO_LAT if lat > cyr else _LAT_TO_CYR)
    return _WORD_RE.sub(fix, text)


_indra = None
_indra_lock = threading.Lock()


def _indra_converter():
    global _indra
    if _indra is None:
        import indra_ocr
        _indra = indra_ocr.load(settings.indra_model_dir, threads=settings.indra_threads,
                                mode="ocr", furniture=True)
    return _indra


def ocr_page_indra(image_bytes: bytes) -> tuple[str, float]:
    """Распознаёт страницу Indra. Возвращает текст и среднюю уверенность строк 0–100.

    Модель грузится один раз на процесс. Конвертер общий, поэтому вызовы
    идут по одному: параллельные активности воркера ждут друг друга.
    """
    with _indra_lock, tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        tmp.write(image_bytes)
        tmp.flush()
        pages = list(_indra_converter().convert(tmp.name))
    if not pages:
        return "", 0.0
    _, page = pages[0]
    confs = [line.conf for line in page.lines if line.text.strip()]
    confidence = 100 * sum(confs) / len(confs) if confs else 0.0
    return fold_mixed_scripts(body_text(page.blocks) or page.text or ""), confidence


# Блоки Indra, которые не относятся к связному тексту страницы.
_SKIP_BLOCKS = {"Page-Header", "Page-Footer", "Table", "Picture", "Figure", "Caption", "Formula"}
_CAPTION_START_RE = re.compile(r"^\s*(Рис\.|Рисунок|Табл\.|Таблица|Fig\.)", re.I)
# Пункт подписи к рисунку: «13 — стерильный цветок». В связном тексте числа
# стоят по обе стороны тире («4—5 плодолистиков») и сюда не попадают.
_LEGEND_ITEM_RE = re.compile(r"(?<![\d—–-])\d{1,2}\s+[—–-]\s+[а-яёa-z]")


def is_caption(text: str) -> bool:
    """Похож ли блок на подпись к рисунку или его кусок."""
    if _CAPTION_START_RE.match(text):
        return True
    items = len(_LEGEND_ITEM_RE.findall(text))
    return items >= 3 or (items >= 1 and len(text) < 300)


def body_text(blocks: list) -> str:
    """Связный текст страницы из блоков Indra: без колонтитулов, таблиц и подписей.

    Подпись к рисунку на странице «Жизни растений» состоит из десятков пунктов
    («1 — початок в мужской стадии…»), и модель, разбирающая факты, принимала
    из-за неё всю страницу за подпись и возвращала пустой ответ (19.10.2026).
    """
    parts = []
    for block in blocks or []:
        if not isinstance(block, dict) or block.get("label") in _SKIP_BLOCKS:
            continue
        text = (block.get("text") or "").strip()
        if text and not is_caption(text):
            parts.append(text)
    return "\n\n".join(parts)


async def ocr_page_llm(image_bytes: bytes, task: str = "ocr_fallback") -> str:
    """OCR a page using multimodal LLM via OpenRouter.

    Used as fallback for pages where Tesseract confidence is too low.
    task='ocr_fallback' uses Gemini Flash (fast, cheap).
    task='ocr_hard' uses Gemini Pro (better for tables, handwriting).
    """
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Распознай текст на этом изображении. Это страница из старинной русской книги. "
                        "Верни только распознанный текст, без комментариев. "
                        "Сохрани оригинальную орфографию (ѣ, i, ъ и т.д.) если она присутствует."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_image}"},
                },
            ],
        }
    ]

    return await chat_completion(messages, task=task, temperature=0.1, max_tokens=8192)


async def ocr_page_with_fallback(image_bytes: bytes, engine: str | None = None) -> tuple[str, float, str]:
    """OCR with automatic fallback to LLM for low-confidence pages.

    Returns (text, confidence, method) where method is 'tesseract', 'indra',
    'llm_fallback', 'llm_hard', 'tesseract_fallback' or 'indra_fallback'.

    ``engine`` выбирает первый движок: ``"indra"`` или ``"tesseract"``; по
    умолчанию берётся ``settings.ocr_engine``. Если Indra не установлена или
    упала, страница молча уходит на Tesseract.
    """
    engine = engine or settings.ocr_engine
    if engine == "indra":
        try:
            text, confidence = ocr_page_indra(image_bytes)
        except Exception as e:
            logger.warning(f"Indra OCR failed: {e}; falling back to Tesseract")
            engine = "tesseract"
        else:
            if confidence >= settings.indra_confidence_auto:
                return text, confidence, "indra"
            # Indra не уверена в странице: её читает зрячая модель, а текст Indra
            # остаётся запасным, если модель не ответит.
            task = "ocr_fallback" if confidence >= settings.ocr_confidence_auto else "ocr_hard"
            try:
                return await ocr_page_llm(image_bytes, task=task), confidence, task.replace("ocr_", "llm_")
            except Exception as e:
                logger.warning(f"LLM OCR ({task}) failed: {e}; keeping Indra text")
                return text, confidence, "indra_fallback"

    text, confidence = ocr_page(image_bytes)

    if confidence >= settings.ocr_confidence_auto:
        return text, confidence, "tesseract"

    # Low/medium confidence: escalate to a vision LLM. If that call fails
    # (rate limit, provider ToS 403, transient error), degrade gracefully to
    # the Tesseract text we already have rather than aborting the whole book.
    if confidence >= settings.ocr_confidence_review:
        task, method = "ocr_fallback", "llm_fallback"
    else:
        task, method = "ocr_hard", "llm_hard"

    try:
        llm_text = await ocr_page_llm(image_bytes, task=task)
        return llm_text, confidence, method
    except Exception as e:
        logger.warning(f"LLM OCR ({task}) failed: {e}; keeping Tesseract text")
        return text, confidence, "tesseract_fallback"
