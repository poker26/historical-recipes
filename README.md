# Historical Recipes

Платформа для работы с коллекцией старинных книг рецептов настоек, дистиллятов, травных напитков и лекарственных растений.

## Что это

- **Библиотека книг** — загрузка, OCR, нормализация дореволюционных текстов
- **Каталог рецептов** — структурированные рецепты с ингредиентами и мерами
- **Травник** — справочник лекарственных растений, их свойств и совместимости
- **Гибридный поиск** — BGE-M3 dense + sparse vectors, RRF fusion через Qdrant
- **Telegram-бот** — интерактивный помощник дистиллятора и травника

## Архитектура

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

**Стек:** FastAPI (Python) + Next.js (React) + n8n + Qdrant + Supabase + MinIO + BGE-M3

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# edit .env with your credentials

# 2. Start services
docker compose up -d

# 3. Run migrations
docker compose exec backend alembic upgrade head

# 4. Open
# Frontend: http://localhost:3000
# API docs: http://localhost:8000/docs
# Supabase Studio: http://localhost:3001
# MinIO Console: http://localhost:9001
```

## Project Structure

```
historical-recipes/
├── backend/          # FastAPI — API, OCR, парсинг, поиск
├── frontend/         # Next.js — веб-интерфейс
├── docs/
│   ├── ARCHITECTURE.md
│   └── n8n-workflows/  # оригинальные n8n workflow (справочно)
├── docker-compose.yml
└── .env.example
```
