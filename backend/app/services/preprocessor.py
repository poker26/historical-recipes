"""Pre-OCR image processing: binarization, deskew, denoise, DPI scaling."""

# TODO: implement with OpenCV
# Key functions:
# - split_pdf_to_pages(pdf_path) -> list of page images
# - preprocess_page(image) -> processed image
#   - scale_to_300dpi
#   - adaptive_binarization
#   - deskew
#   - denoise (morphological operations)
#   - remove_borders
#   - enhance_contrast (CLAHE)
