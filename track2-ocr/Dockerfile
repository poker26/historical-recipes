# Batch-runner image: runs the PaddleOCR-VL *pipeline* (layout on CPU) and
# forwards VLM recognition to the separate GPU `vllm-server` container.
# This container itself needs NO GPU.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PADDLE_PDX_CACHE_HOME=/models

# Poppler/libgl for PDF rendering + OpenCV runtime used by the pipeline.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        poppler-utils \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY batch_ocr.py .

# Layout/orientation model cache persists via the /models volume so a restart
# doesn't re-download them.
VOLUME ["/models", "/data"]

ENTRYPOINT ["python", "batch_ocr.py"]
