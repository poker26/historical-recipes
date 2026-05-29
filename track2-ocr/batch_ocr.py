"""Batch re-OCR of the PDF corpus through a PaddleOCR-VL vLLM server.

Track 2: run on a rented GPU VDS (NVIDIA A4000, CC 8.6). The heavy VLM
recognition runs on the GPU inside the `vllm-server` container; this script
runs the PaddleOCR-VL *pipeline* (layout detection + orchestration) and sends
each region to that server, then writes one Markdown file per input PDF.

Design notes:
- Input PDFs are passed straight to the pipeline (it renders pages itself at a
  tuned DPI), so we don't hand-roll page rasterization.
- Resumable: a PDF whose output .md already exists is skipped unless --force.
- Fault-tolerant: one bad PDF logs an error and the batch continues; a partial
  page failure inside a PDF is tolerated where possible.
- The pipeline's Markdown across pages is stitched with the pipeline's own
  concatenate helper when available, with a manual fallback.
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch_ocr")

DEFAULT_SERVER_URL = os.environ.get("VL_SERVER_URL", "http://vllm-server:8118/v1")
DEFAULT_MODEL_NAME = os.environ.get("VL_MODEL_NAME", "PaddleOCR-VL-1.5-0.9B")
DEFAULT_INPUT = os.environ.get("INPUT_DIR", "/data/pdfs")
DEFAULT_OUTPUT = os.environ.get("OUTPUT_DIR", "/data/output")


def wait_for_server(server_url: str, timeout_s: int = 1200) -> None:
    """Block until the vLLM OpenAI-compatible server answers, or time out."""
    base = server_url.rstrip("/")
    models_url = base + "/models" if not base.endswith("/models") else base
    health_url = base[:-3].rstrip("/") + "/health" if base.endswith("/v1") else base + "/health"
    deadline = time.time() + timeout_s
    log.info("Waiting for vLLM server at %s ...", server_url)
    while time.time() < deadline:
        for url in (models_url, health_url):
            try:
                r = httpx.get(url, timeout=5.0)
                if r.status_code < 500:
                    log.info("Server is up (%s -> %s)", url, r.status_code)
                    return
            except Exception:
                pass
        time.sleep(5)
    raise SystemExit(f"vLLM server did not become ready within {timeout_s}s")


def build_pipeline(server_url: str, model_name: str):
    """Construct the PaddleOCR-VL pipeline bound to the remote vLLM server."""
    from paddleocr import PaddleOCRVL  # imported late so --help works without deps

    log.info("Building PaddleOCRVL pipeline (server=%s, model=%s)", server_url, model_name)
    return PaddleOCRVL(
        vl_rec_server_url=server_url,
        vl_rec_api_model_name=model_name,
    )


def _page_markdown(res) -> object:
    """Best-effort extraction of a page result's markdown payload."""
    return getattr(res, "markdown", None)


def _stitch_markdown(pipeline, md_pages: list) -> str:
    """Combine per-page markdown into one document, defensively."""
    # Preferred: pipeline's own cross-page concatenation (handles tables/lists
    # split across page breaks).
    concat = getattr(pipeline, "concatenate_markdown_pages", None)
    if callable(concat):
        try:
            out = concat(md_pages)
            if isinstance(out, dict):
                out = out.get("markdown_texts") or out.get("markdown") or ""
            if isinstance(out, str) and out.strip():
                return out
        except Exception as e:
            log.warning("concatenate_markdown_pages failed (%s); falling back", e)

    parts: list[str] = []
    for md in md_pages:
        if isinstance(md, dict):
            parts.append(md.get("markdown_texts") or md.get("markdown") or "")
        elif isinstance(md, str):
            parts.append(md)
    return "\n\n".join(p for p in parts if p)


def process_pdf(pipeline, pdf_path: Path, out_path: Path) -> int:
    """OCR one PDF -> Markdown file. Returns number of pages processed."""
    output = pipeline.predict(str(pdf_path))
    md_pages: list = []
    pages = 0
    for res in output:
        pages += 1
        md_pages.append(_page_markdown(res))
        if pages % 10 == 0:
            log.info("  ... %s pages", pages)
    markdown = _stitch_markdown(pipeline, md_pages)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")
    return pages


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch PaddleOCR-VL over a PDF corpus")
    ap.add_argument("--input", default=DEFAULT_INPUT, help="dir with input PDFs (recursive)")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="dir for output .md files")
    ap.add_argument("--server-url", default=DEFAULT_SERVER_URL, help="vLLM OpenAI base URL")
    ap.add_argument("--model-name", default=DEFAULT_MODEL_NAME, help="served model name")
    ap.add_argument("--force", action="store_true", help="re-OCR even if output exists")
    ap.add_argument("--no-wait", action="store_true", help="don't wait for the server")
    args = ap.parse_args()

    in_dir = Path(args.input)
    out_dir = Path(args.output)
    if not in_dir.is_dir():
        log.error("Input dir not found: %s", in_dir)
        return 2

    pdfs = sorted(p for p in in_dir.rglob("*.pdf"))
    if not pdfs:
        log.error("No PDFs under %s", in_dir)
        return 2
    log.info("Found %d PDFs under %s", len(pdfs), in_dir)

    if not args.no_wait:
        wait_for_server(args.server_url)

    pipeline = build_pipeline(args.server_url, args.model_name)

    ok = skipped = failed = 0
    t_start = time.time()
    for i, pdf in enumerate(pdfs, 1):
        rel = pdf.relative_to(in_dir).with_suffix(".md")
        out_path = out_dir / rel
        if out_path.exists() and not args.force:
            log.info("[%d/%d] SKIP (exists) %s", i, len(pdfs), rel)
            skipped += 1
            continue
        log.info("[%d/%d] OCR %s", i, len(pdfs), pdf.name)
        t0 = time.time()
        try:
            pages = process_pdf(pipeline, pdf, out_path)
            ok += 1
            log.info("[%d/%d] DONE %s (%d pages, %.1fs)", i, len(pdfs), rel, pages, time.time() - t0)
        except Exception as e:
            failed += 1
            log.exception("[%d/%d] FAILED %s: %s", i, len(pdfs), pdf.name, e)

    log.info(
        "Batch complete: %d ok, %d skipped, %d failed in %.0fs",
        ok, skipped, failed, time.time() - t_start,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
