"""OCR service: Tesseract primary + LLM fallback via OpenRouter."""

# TODO: implement
# Key functions:
# - ocr_page(image_path) -> (text, confidence)
#   - Run Tesseract with rus lang
#   - Calculate per-word confidence
#   - If confidence < threshold -> LLM fallback (Gemini Flash/Pro)
# - ocr_page_llm(image_path, task="ocr_fallback") -> text
#   - Send image to OpenRouter multimodal model
