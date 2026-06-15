"""Pre-OCR image processing: binarization, deskew, denoise, DPI scaling.

Uses OpenCV for image manipulation. Critical for old book scans where
Tesseract fails on yellowed paper, skewed pages, and noisy backgrounds.
"""

import io
import re

import cv2
import numpy as np
from PIL import Image
import fitz  # PyMuPDF

# Trusted high-DPI book scans can exceed PIL's decompression-bomb cap (~178M px)
# → DecompressionBombError wedges the book. Our own scans, not attacks → lift it.
Image.MAX_IMAGE_PIXELS = None


# A PyMuPDF text layer can *exist* yet be corrupt: a scanned book whose baked-in
# OCR dropped almost all letters, leaving punctuation/digits noise. Such a layer
# clears the raw length gate but is unusable downstream (Николаевский 1987 «Биол.
# активность эфирных масел»: ~257 chars/page, ~90% punctuation, Cyrillic stripped
# → 0 oils extracted). Require that a real fraction of the non-space characters
# are actual Cyrillic/Latin letters; otherwise the page is re-OCR'd from its image.
_LETTER_RE = re.compile(r"[А-Яа-яЁёA-Za-z]")
_MIN_LETTER_RATIO = 0.5


def _text_layer_is_readable(text: str) -> bool:
    stripped = "".join(text.split())
    if not stripped:
        return False
    letters = len(_LETTER_RE.findall(stripped))
    return letters / len(stripped) >= _MIN_LETTER_RATIO


TARGET_DPI = 300


def split_pdf_to_pages(pdf_bytes: bytes) -> list[tuple[bytes, int]]:
    """Split PDF into individual page images.

    Returns list of (png_bytes, estimated_dpi) tuples.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        # Render at 300 DPI (default is 72)
        zoom = TARGET_DPI / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        pages.append((png_bytes, TARGET_DPI))
    doc.close()
    return pages


MIN_TEXT_LENGTH = 50  # Minimum chars to consider a page as having extractable text


def split_pdf_smart(pdf_bytes: bytes) -> list[dict]:
    """Split PDF with automatic text extraction for text pages.

    For each page returns:
    {
        "page_number": int,
        "page_type": "text" | "image",
        "text": str | None,         # extracted text (if text page)
        "image_bytes": bytes | None, # rendered PNG (if image page)
        "dpi": int,
    }

    Text pages get text extracted directly via PyMuPDF (fast, free).
    Image pages get rendered to PNG for OCR pipeline.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text().strip()

        if len(text) >= MIN_TEXT_LENGTH and _text_layer_is_readable(text):
            # Text page — extract text directly, no need for OCR
            pages.append({
                "page_number": page_num + 1,
                "page_type": "text",
                "text": text,
                "image_bytes": None,
                "dpi": 0,
            })
        else:
            # Image page — render to PNG for OCR
            zoom = TARGET_DPI / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            png_bytes = pix.tobytes("png")
            pages.append({
                "page_number": page_num + 1,
                "page_type": "image",
                "text": None,
                "image_bytes": png_bytes,
                "dpi": TARGET_DPI,
            })

    doc.close()
    return pages


def preprocess_page(image_bytes: bytes) -> bytes:
    """Full pre-OCR pipeline for a single page image.

    Steps: grayscale -> scale -> binarize -> deskew -> denoise -> enhance contrast

    Returns processed image as PNG bytes.
    """
    img = _bytes_to_cv2(image_bytes)

    # 1. Convert to grayscale
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # 2. Adaptive binarization (handles yellowed paper)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )

    # 3. Deskew
    deskewed = _deskew(binary)

    # 4. Denoise (morphological: remove small dots/spots)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    denoised = cv2.morphologyEx(deskewed, cv2.MORPH_OPEN, kernel)

    # 5. Remove dark borders
    cleaned = _remove_borders(denoised)

    # 6. Enhance contrast (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(cleaned)

    return _cv2_to_bytes(enhanced)


def _deskew(image: np.ndarray) -> np.ndarray:
    """Deskew image by detecting text angle."""
    coords = np.column_stack(np.where(image < 128))
    if len(coords) < 100:
        return image

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    # Only correct small angles (< 5 degrees)
    if abs(angle) > 5 or abs(angle) < 0.1:
        return image

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(
        image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def _remove_borders(image: np.ndarray, border_pct: float = 0.02) -> np.ndarray:
    """Remove dark borders (scan artifacts, binding shadows)."""
    h, w = image.shape[:2]
    top = int(h * border_pct)
    bottom = int(h * (1 - border_pct))
    left = int(w * border_pct)
    right = int(w * (1 - border_pct))
    cropped = image[top:bottom, left:right]
    return cropped


def _bytes_to_cv2(image_bytes: bytes) -> np.ndarray:
    """Convert image bytes to OpenCV array."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    return cv2.imdecode(nparr, cv2.IMREAD_COLOR)


def _cv2_to_bytes(image: np.ndarray) -> bytes:
    """Convert OpenCV array to PNG bytes."""
    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise RuntimeError("Failed to encode image")
    return encoded.tobytes()
