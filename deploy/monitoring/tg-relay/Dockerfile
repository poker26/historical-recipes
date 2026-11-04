FROM python:3.12-slim
RUN pip install --no-cache-dir httpx==0.27.2
COPY relay.py /app/relay.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/relay.py"]
