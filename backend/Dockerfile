FROM python:3.12-slim

# Tesseract OCR (+ Russian) and djvulibre (ddjvu: DjVu -> PDF conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-rus \
    djvulibre-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Веса Indra OCR закреплённой ревизии; суммы сверяет сам скрипт.
COPY scripts/fetch_indra_weights.py /tmp/fetch_indra_weights.py
RUN python /tmp/fetch_indra_weights.py /opt/indra-ocr && rm /tmp/fetch_indra_weights.py

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
