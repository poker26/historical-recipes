# Развёртывание на сервере

## Требования

- **VDS/VPS**: Ubuntu 22.04+, минимум 4 GB RAM, 40 GB SSD
- **Docker**: 24.0+ и Docker Compose v2
- **Домен** (опционально): для HTTPS через Let's Encrypt

## 1. Подготовка сервера

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Docker (если не установлен)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

# Docker Compose plugin (если не установлен)
sudo apt install docker-compose-plugin -y

# Проверка
docker --version
docker compose version
```

## 2. Клонирование и настройка

```bash
# Клонирование
git clone https://github.com/poker26/historical-recipes.git
cd historical-recipes
git checkout claude/restructure-project-dLnyG

# Создание .env из примера
cp .env.example .env
```

## 3. Настройка .env

Обязательно измените следующие значения:

```bash
nano .env
```

```env
# ОБЯЗАТЕЛЬНО — сменить пароли
POSTGRES_PASSWORD=<сгенерировать: openssl rand -hex 24>
MINIO_ACCESS_KEY=<сгенерировать: openssl rand -hex 16>
MINIO_SECRET_KEY=<сгенерировать: openssl rand -hex 24>

# ОБЯЗАТЕЛЬНО — ваш ключ OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...

# BGE-M3 — адрес вашего сервиса эмбеддингов
BGE_M3_URL=http://172.17.0.1
BGE_M3_PORT=8100

# CORS — домен вашего сервера
CORS_ORIGINS=["http://your-domain.com"]

# Frontend — URL API для браузера
NEXT_PUBLIC_API_URL=/api

# Порт nginx (по умолчанию 80)
NGINX_HTTP_PORT=80
```

## 4. BGE-M3 (эмбеддинги)

BGE-M3 запускается отдельно, т.к. требует GPU или много RAM:

```bash
# Вариант 1: GPU (рекомендуется)
docker run -d --name bge-m3 \
  --gpus all \
  -p 8100:8100 \
  ghcr.io/huggingface/text-embeddings-inference:1.5 \
  --model-id BAAI/bge-m3 \
  --port 8100

# Вариант 2: CPU (медленнее, но работает)
docker run -d --name bge-m3 \
  -p 8100:8100 \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.5 \
  --model-id BAAI/bge-m3 \
  --port 8100
```

Проверка:
```bash
curl http://localhost:8100/embed \
  -X POST -H "Content-Type: application/json" \
  -d '{"inputs": "тест"}'
```

## 5. Запуск

```bash
# Сборка и запуск всех сервисов
docker compose up -d --build

# Просмотр логов
docker compose logs -f

# Только backend логи
docker compose logs -f backend
```

## 6. Миграция базы данных

```bash
# Выполнить миграции Alembic
docker compose exec backend alembic upgrade head
```

## 7. Проверка

```bash
# Health check
curl http://localhost/health

# API доступен
curl http://localhost/api/books/

# Qdrant
curl http://localhost:6333/collections

# MinIO (через nginx)
# Открыть http://your-server/minio/ в браузере
```

## 8. HTTPS (опционально, рекомендуется)

### Вариант A: Certbot + nginx на хосте

```bash
# Установка certbot
sudo apt install certbot -y

# Получение сертификата (nginx должен быть остановлен или на другом порту)
sudo certbot certonly --standalone -d your-domain.com

# Добавить в nginx/nginx.conf SSL server block
# и смонтировать сертификаты в docker-compose.yml
```

### Вариант B: Cloudflare Proxy

Если домен на Cloudflare — включить проксирование (оранжевое облако), 
Cloudflare обеспечит HTTPS автоматически. Сервер работает на HTTP.

### Вариант C: Traefik вместо nginx

Для автоматического HTTPS можно заменить nginx на Traefik с Let's Encrypt.
Это более сложная настройка, но полностью автоматизирует сертификаты.

## 9. n8n (Telegram-бот и пайплайны)

n8n запускается отдельно (у него своя экосистема):

```bash
docker run -d --name n8n \
  -p 5678:5678 \
  -v n8n_data:/home/node/.n8n \
  -e N8N_BASIC_AUTH_ACTIVE=true \
  -e N8N_BASIC_AUTH_USER=admin \
  -e N8N_BASIC_AUTH_PASSWORD=<ваш-пароль> \
  -e WEBHOOK_URL=https://your-domain.com/n8n/ \
  n8nio/n8n:latest
```

Импорт воркфлоу:
```bash
# Файлы воркфлоу находятся в docs/n8n-workflows/
# Импортировать через UI n8n: Settings → Import from file
```

В воркфлоу обновить:
- URL API бэкенда: `http://172.17.0.1:8000` (или внутренний адрес)
- Токен Telegram-бота
- Ключ OpenRouter

## Управление

```bash
# Остановка
docker compose down

# Остановка с удалением данных (ОСТОРОЖНО!)
docker compose down -v

# Перезапуск одного сервиса
docker compose restart backend

# Пересборка после изменений кода
docker compose up -d --build backend frontend

# Обновление образов
docker compose pull
docker compose up -d
```

## Бэкапы

```bash
# PostgreSQL
docker compose exec supabase-db pg_dump -U postgres postgres > backup_$(date +%Y%m%d).sql

# Восстановление
cat backup_20240101.sql | docker compose exec -T supabase-db psql -U postgres postgres

# MinIO (файлы)
docker run --rm -v minio-data:/data -v $(pwd)/backups:/backup \
  alpine tar czf /backup/minio_$(date +%Y%m%d).tar.gz /data

# Qdrant (снапшоты)
curl -X POST http://localhost:6333/collections/recipes/snapshots
curl -X POST http://localhost:6333/collections/herbalism/snapshots
```

## Мониторинг

```bash
# Статус контейнеров
docker compose ps

# Использование ресурсов
docker stats

# Диск
df -h
docker system df
```

## Решение проблем

| Проблема | Решение |
|----------|---------|
| Backend не стартует | `docker compose logs backend` — проверить подключение к БД |
| Qdrant OOM | Увеличить RAM или добавить `--storage-snapshot-path` для offload |
| MinIO 403 | Проверить MINIO_ACCESS_KEY/SECRET_KEY в .env |
| OCR медленный | Проверить что Tesseract установлен в контейнере: `docker compose exec backend tesseract --version` |
| BGE-M3 timeout | Увеличить BGE_M3_TIMEOUT, проверить доступность: `curl http://172.17.0.1:8100/health` |
| Frontend белый экран | Проверить NEXT_PUBLIC_API_URL, посмотреть консоль браузера |
