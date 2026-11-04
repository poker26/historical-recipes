# Движок определения грибов и мхов «Что растёт»: BioCLIP-2 + kNN по индексам из /work.
# Версии те же, что у сборщика индексов на dev (/opt/fungi-build/venv): векторы снимка
# и векторы индекса должны считаться одной и той же моделью и одним препроцессингом.
FROM python:3.10-slim
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
        torch==2.5.1+cpu torchvision==0.20.1+cpu \
    && pip install --no-cache-dir open_clip_torch==2.29.0 numpy==1.26.4 pillow==10.4.0 \
        timm==1.0.31 huggingface_hub==2.4.0
COPY fungi_serve.py /app/fungi_serve.py
# Веса BioCLIP-2 скачиваются при первом запуске в том /hf и дальше берутся оттуда.
ENV HF_HOME=/hf PYTHONUNBUFFERED=1
EXPOSE 8977
CMD ["python", "/app/fungi_serve.py"]
