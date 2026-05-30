"""OCR service: Tesseract primary + vision-LLM fallback via OpenRouter.

Tesseract handles most pages. For low-confidence pages we fall back to a
multimodal LLM (Qwen3-VL) which handles unusual fonts and degraded scans
better. The fallback is best-effort: if the vision call fails it degrades to
the Tesseract text rather than aborting extraction.
"""

import base64
import io
import logging

import pytesseract
from PIL import Image

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


async def ocr_page_with_fallback(image_bytes: bytes) -> tuple[str, float, str]:
    """OCR with automatic fallback to LLM for low-confidence pages.

    Returns (text, confidence, method) where method is 'tesseract' or 'llm_flash' or 'llm_pro'.
    """
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
