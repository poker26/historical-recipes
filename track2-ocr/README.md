# Track 2 — GPU batch re-OCR with PaddleOCR-VL

Re-OCR the **entire PDF corpus** from page images on a short-lived rented GPU
VDS, producing clean Markdown that replaces the bad third-party text layer the
books currently ship with (the one with the `breadwine.ru` watermark baked in).

This is a **self-contained, throwaway deployment** — it does not touch the live
app. You rent the box, process the corpus, download the `.md` output, destroy
the box.

## Why

The live pipeline (Track 1) cleans/translates the *existing* OCR text with an
LLM. But for pre-reform books the embedded text layer is itself bad OCR (ять
mangled, watermark text mixed in). No amount of LLM cleanup recovers what was
never read correctly. PaddleOCR-VL 1.5 (a 0.9B document VLM, OmniDocBench v1.5
≈ 94.5%, strong on ancient/rare glyphs and 109 languages) re-reads the pages
from images and produces a far better base text.

## Target hardware

- **GPU:** NVIDIA A4000 16 GB (Ampere, compute capability **8.6** ✓ — PaddleOCR-VL
  needs CC ≥ 8.0 and CUDA ≥ 12.6).
- 8 cores / 32 GB RAM / 128 GB NVMe.
- **OS:** Ubuntu 24.04 LTS (noble) recommended for reliability — fully supported
  by the Docker and NVIDIA Container Toolkit apt repos. 22.04 (jammy) also works.

## Architecture

```
            ┌──────────────────────────────┐
  data/pdfs │  batch (our image, CPU)       │  data/output/*.md
  ─────────▶│  PaddleOCR-VL pipeline        │─────────▶
            │   • layout detection (CPU)    │
            │   • orchestration             │
            └───────────────┬──────────────┘
                            │ region images (HTTP, OpenAI-compatible)
                            ▼
            ┌──────────────────────────────┐
            │  vllm-server (official image) │
            │  PaddleOCR-VL VLM on the GPU  │  :8118
            └──────────────────────────────┘
```

- **`vllm-server`** — Baidu's official `paddleocr-genai-vllm-server` image. Runs
  the VLM on the GPU. Started once, stays up.
- **`batch`** — our small image (`Dockerfile`). Runs `batch_ocr.py`: walks
  `data/pdfs/**.pdf`, OCRs each via the pipeline, writes `data/output/<name>.md`.
  Resumable (skips PDFs whose `.md` already exists) and fault-tolerant (a bad
  PDF is logged and skipped, the batch continues).

## One-time VDS setup

```bash
sudo bash setup_vds.sh     # installs driver + Docker + NVIDIA Container Toolkit
sudo reboot                # required to load the NVIDIA driver
nvidia-smi                 # verify GPU is visible after reboot
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi  # verify Docker GPU
```

> On 24.04/22.04 the Docker and NVIDIA apt repos publish for the codename, so
> setup is straightforward. The explicit driver version in the script
> (`nvidia-driver-580-open`) is only a fallback — `ubuntu-drivers autoinstall`
> normally picks the right one.

## Run the batch

```bash
cd track2-ocr
cp .env.example .env            # confirm VL_MODEL_NAME matches the image
mkdir -p data/pdfs data/output
# upload the corpus into data/pdfs/  (rsync/scp; subfolders are fine)

docker compose up -d vllm-server
docker compose logs -f vllm-server   # wait until it reports ready / model loaded

docker compose run --rm batch        # foreground; Ctrl-C safe, re-run resumes
```

Output lands in `data/output/`, mirroring the input folder tree, one `.md` per
PDF. Download it back:

```bash
rsync -avz vds:/path/to/track2-ocr/data/output/ ./corpus-md/
```

Then destroy the VDS.

## Tuning / notes

- **Model name must match** between server (`--model_name`) and client
  (`VL_MODEL_NAME` → `vl_rec_api_model_name`). Confirm the exact tag the image
  supports: `docker run --rm <VLLM_IMAGE> paddleocr --help`. A newer
  **1.6** exists; switch by editing `VL_MODEL_NAME` in `.env`.
- First server start downloads model weights into `data/hf-cache/` (persisted).
- `batch_ocr.py` waits up to 20 min for the server before starting.
- Throughput on a single A4000 for a 0.9B VLM is the GPU bottleneck; layout
  runs on CPU in the batch container, so the two don't fight over VRAM.
- To re-process everything from scratch: `docker compose run --rm batch --force`.

## Feeding results back into the app

Each book's `data/output/<name>.md` is the new clean source text. Re-ingest it
into the app as the book's `full_text` (Markdown), then run the wizard from the
**cleanup/analyze** steps — translation (pre-reform → modern) and structure /
recipe extraction still apply, but now on a correct base.
