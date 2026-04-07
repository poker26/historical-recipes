# Развёртывание на сервере

## Предусловия

На отдельных серверах уже развёрнуты и работают:
- **Supabase** (PostgreSQL) — `sup.begemot26.ru`
- **Qdrant** (векторная БД)
- **MinIO** (S3 хранилище)
- **BGE-M3** (эмбеддинги)
- **n8n** (оркестрация, TG-бот)

Приложение (backend + frontend + nginx) подключается к ним по сети через реальные адреса.

## 1. Клонирование

```bash
git clone https://github.com/poker26/historical-recipes.git
cd historical-recipes
git checkout claude/restructure-project-dLnyG
```

## 2. Настройка .env

```bash
cp .env.example .env
nano .env
```

Заполнить реальные адреса и credentials ваших сервисов:

```env
# Supabase PostgreSQL
DATABASE_URL=postgresql+asyncpg://postgres.tenant-id:PASSWORD@sup.begemot26.ru:6543/postgres

# MinIO
MINIO_ENDPOINT=minio-host:9000
MINIO_ACCESS_KEY=...
MINIO_SECRET_KEY=...

# Qdrant
QDRANT_URL=http://qdrant-host:6333

# BGE-M3
BGE_M3_URL=http://bge-host
BGE_M3_PORT=8100

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...

# n8n
N8N_BASE_URL=http://n8n-host:5678

# CORS — домен этого приложения
CORS_ORIGINS=["http://your-domain.com"]

# Порт (если 80 занят)
NGINX_HTTP_PORT=80
```

Все адреса — реальные домены/IP ваших серверов с сервисами.

## 3. Сборка и запуск

```bash
docker compose up -d --build
```

Будут собраны и запущены 3 контейнера:
- **nginx** — reverse proxy (порт 80)
- **backend** — FastAPI + Tesseract OCR
- **frontend** — Next.js

## 4. Миграция базы данных

```bash
docker compose exec backend alembic upgrade head
```

Это создаст все таблицы в вашей Supabase PostgreSQL.

## 5. Проверка

```bash
# Health check
curl http://localhost/health

# API
curl http://localhost/api/books/

# Подключение к Qdrant
curl http://localhost/api/search/ -X POST \
  -H "Content-Type: application/json" \
  -d '{"query": "тест", "limit": 1}'
```

Откройте `http://your-server` в браузере — должен показаться Dashboard.

## 6. Создание бакета MinIO

Если бакет `historical-recipes` ещё не создан — backend создаст его автоматически при первом аплоаде. Либо создайте вручную через MinIO Console.

## 7. Импорт воркфлоу n8n

Файлы воркфлоу находятся в `docs/n8n-workflows/`. Импортируйте через UI n8n (Settings → Import).

В воркфлоу обновите:
- URL бэкенда: `http://host-ip:80/api` или внутренний адрес
- Токен Telegram-бота
- Ключ OpenRouter

---

## Управление

```bash
# Логи
docker compose logs -f
docker compose logs -f backend

# Перезапуск
docker compose restart backend

# Пересборка после обновления кода
git pull
docker compose up -d --build

# Остановка
docker compose down
```

## Обновление

```bash
cd historical-recipes
git pull origin claude/restructure-project-dLnyG
docker compose up -d --build
docker compose exec backend alembic upgrade head
```

## HTTPS

### Вариант A: Cloudflare Proxy
Включить проксирование (оранжевое облако) — Cloudflare обеспечит HTTPS автоматически.

### Вариант B: Certbot
```bash
sudo apt install certbot -y
sudo certbot certonly --standalone -d your-domain.com
```
Затем добавить SSL server block в `nginx/nginx.conf` и смонтировать сертификаты.

## Решение проблем

| Проблема | Решение |
|----------|---------|
| Backend не стартует | `docker compose logs backend` — проверить строку подключения к БД |
| Connection refused к сервису | Проверить доступность: `curl http://qdrant-host:6333`, файрвол, порты |
| MinIO 403 | Проверить MINIO_ACCESS_KEY/SECRET_KEY |
| OCR медленный | `docker compose exec backend tesseract --version` — проверить установку |
| BGE-M3 timeout | Увеличить `BGE_M3_TIMEOUT`, проверить: `curl http://host:8100/health` |
| Frontend белый экран | F12 → Console, проверить что `/api/` отвечает |
