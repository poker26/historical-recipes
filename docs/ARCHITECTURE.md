# Historical Recipes - Архитектура проекта

## Обзор

Платформа для работы с коллекцией старинных книг рецептов настоек, дистиллятов и травных напитков.
Включает полный цикл: от загрузки книги до ответа пользователю через Telegram-бота.

## Ключевые задачи

1. **Управление книгами** — веб-интерфейс для загрузки, трекинга статуса и управления коллекцией
2. **Обработка текстов** — OCR, нормализация дореволюционной орфографии, конвертация мер
3. **Парсинг рецептов** — извлечение структурированных данных (ингредиенты, меры, процесс)
4. **Индексация** — гибридные векторы (BGE-M3 dense + sparse) → Qdrant
5. **Поиск** — RRF hybrid search с приоритетом точных рецептов
6. **Бот** — Telegram-бот (в n8n) для взаимодействия с пользователями

## Стек технологий

| Компонент | Технология | Назначение |
|-----------|-----------|------------|
| **Frontend** | Next.js (React) | Веб-морда управления книгами |
| **Backend API** | FastAPI (Python) | REST API для всех сервисов |
| **Оркестрация** | n8n | Пайплайны обработки, TG-бот |
| **Векторная БД** | Qdrant | Гибридный поиск рецептов |
| **Реляционная БД** | Supabase (PostgreSQL) | Метаданные книг, словари, пользователи |
| **Хранилище файлов** | MinIO (S3-compatible) | PDF, изображения, промежуточные файлы |
| **Embeddings** | BGE-M3 (self-hosted) | Dense (1024d) + Sparse векторы |
| **OCR** | Tesseract + Gemini/Qwen | Распознавание сканов |
| **Деплой** | Docker Compose | Оркестрация контейнеров на VDS |

## Архитектура системы

```
┌─────────────────────────────────────────────────────────────────┐
│                        VDS Server                                │
│                                                                  │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     │
│  │  Next.js      │     │  FastAPI      │     │  n8n          │   │
│  │  (Frontend)   │────▶│  (Backend)    │◀────│  (Workflows)  │   │
│  │  :3000        │     │  :8000        │     │  :5678        │   │
│  └──────────────┘     └──────┬───────┘     └──────┬───────┘   │
│                              │                     │            │
│         ┌────────────────────┼─────────────────────┤            │
│         │                    │                     │            │
│  ┌──────▼──────┐  ┌─────────▼────┐  ┌─────────────▼────┐      │
│  │  Supabase    │  │  Qdrant       │  │  MinIO            │     │
│  │  (PostgreSQL)│  │  (Vectors)    │  │  (File Storage)   │     │
│  │  :5432       │  │  :6333        │  │  :9000            │     │
│  └─────────────┘  └──────────────┘  └──────────────────┘      │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐                             │
│  │  BGE-M3       │  │  Nginx        │                            │
│  │  (Embeddings) │  │  (Proxy+SSL)  │                            │
│  │  :8100        │  │  :80/:443     │                            │
│  └──────────────┘  └──────────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

## Модули FastAPI (backend)

### 1. `books` — Управление книгами

Центральный модуль. Трекинг жизненного цикла каждой книги.

**Статусы книги:**
```
uploaded → ocr_pending → ocr_done → normalized → parsed → indexed → verified
```

**API:**
- `POST /api/books/upload` — загрузка PDF (→ MinIO)
- `GET /api/books` — список книг с фильтрами по статусу
- `GET /api/books/{id}` — детали книги + история обработки
- `POST /api/books/{id}/process` — запуск обработки (триггер n8n workflow)
- `GET /api/books/{id}/chunks` — просмотр распознанных фрагментов
- `PATCH /api/books/{id}/chunks/{chunk_id}` — ручное редактирование чанка

**Таблицы (Supabase):**
```sql
books (
  id UUID PRIMARY KEY,
  title TEXT,
  author TEXT,
  year INTEGER,
  language TEXT,          -- 'modern_ru', 'pre_reform_ru'
  pdf_type TEXT,          -- 'image', 'text', 'mixed'
  file_path TEXT,         -- путь в MinIO
  status TEXT,            -- текущий статус
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)

book_chunks (
  id UUID PRIMARY KEY,
  book_id UUID REFERENCES books,
  chunk_index INTEGER,
  raw_text TEXT,           -- исходный текст после OCR
  normalized_text TEXT,    -- нормализованный текст
  chunk_type TEXT,         -- 'recipe', 'intro', 'chapter_header', 'other'
  page_start INTEGER,
  page_end INTEGER,
  status TEXT              -- 'raw', 'normalized', 'reviewed'
)

processing_log (
  id UUID PRIMARY KEY,
  book_id UUID REFERENCES books,
  step TEXT,               -- 'ocr', 'normalize', 'parse', 'index'
  status TEXT,             -- 'started', 'completed', 'failed'
  details JSONB,
  created_at TIMESTAMPTZ
)
```

### 2. `recipes` — Парсинг и хранение рецептов

Структурированные рецепты, извлечённые из книг.

**API:**
- `GET /api/recipes` — список рецептов с поиском
- `GET /api/recipes/{id}` — детали рецепта
- `POST /api/recipes/parse` — запуск парсинга чанков книги
- `PUT /api/recipes/{id}` — ручное редактирование рецепта

**Таблицы:**
```sql
recipes (
  id UUID PRIMARY KEY,
  book_id UUID REFERENCES books,
  chunk_id UUID REFERENCES book_chunks,
  name TEXT,
  category TEXT,            -- 'водка', 'ликёр', 'настойка', 'бальзам', 'масло', 'вода'
  original_text TEXT,       -- текст как в книге
  normalized_text TEXT,     -- нормализованный текст
  year INTEGER,
  quality TEXT,             -- оценка качества из книги
  qdrant_point_id TEXT,     -- ID вектора в Qdrant
  indexed_at TIMESTAMPTZ
)

recipe_ingredients (
  id UUID PRIMARY KEY,
  recipe_id UUID REFERENCES recipes,
  name TEXT,                -- современное название
  original_name TEXT,       -- название из книги
  amount TEXT,              -- исходное количество ("2 золотника")
  amount_modern TEXT,       -- в современных мерах ("8.5 г")
  unit TEXT,
  unit_modern TEXT
)
```

### 3. `dictionaries` — Словари

Словари для нормализации старинных текстов. Накапливаются по мере обработки книг.

**API:**
- `GET /api/dictionaries` — список словарей по категориям
- `POST /api/dictionaries` — добавление термина
- `GET /api/dictionaries/lookup?term=...` — поиск по термину

**Таблицы:**
```sql
dictionary_terms (
  id UUID PRIMARY KEY,
  category TEXT,         -- 'measurements', 'plants', 'orthography', 'techniques'
  term_old TEXT,         -- старое написание / мера
  term_modern TEXT,      -- современный эквивалент
  context TEXT,          -- пояснение
  source_book_id UUID
)
```

**Категории словарей:**
- `measurements` — золотник → 4.27г, ведро → 12.3л, штоф → 1.23л
- `plants` — старинные названия растений → современные (+ латынь)
- `orthography` — дореволюционные написания → современные (ѣ→е, i→и, ъ в конце слов)
- `techniques` — старинные техники → современные описания

### 4. `search` — Поиск

Обёртка над Qdrant для стандартизированного гибридного поиска.

**API:**
- `POST /api/search` — гибридный поиск (dense + sparse + RRF)
- `POST /api/search/test` — тестовый поиск с метриками качества
- `GET /api/search/collections` — список коллекций Qdrant

**Логика (из текущего n8n workflow):**
1. Получить запрос пользователя
2. Сгенерировать dense + sparse векторы через BGE-M3
3. Выполнить prefetch: 2 запроса (dense limit=20, sparse limit=20)
4. RRF fusion → top 10 результатов
5. Если найден точный рецепт (score > threshold) → вернуть как есть
6. Иначе → вернуть контекст для LLM

### 5. `indexing` — Индексация в Qdrant

**API:**
- `POST /api/indexing/embed` — создание векторов для текста
- `POST /api/indexing/upsert` — добавление/обновление точек в Qdrant
- `POST /api/indexing/book/{id}` — индексация всех рецептов книги
- `DELETE /api/indexing/book/{id}` — удаление векторов книги из Qdrant

## Frontend (Next.js)

### Страницы:

**1. Dashboard** (`/`)
- Общая статистика: книг всего / обработано / в процессе
- Рецептов в базе, покрытие по категориям
- Последние действия (лог)

**2. Библиотека книг** (`/books`)
- Таблица книг с фильтрами по статусу
- Drag-n-drop загрузка новых PDF
- Цветовые индикаторы статуса обработки
- Кнопки действий: OCR, нормализация, парсинг, индексация

**3. Детали книги** (`/books/[id]`)
- Метаданные книги
- Просмотр чанков (raw → normalized, side by side)
- Извлечённые рецепты
- Лог обработки (timeline)
- Ручное редактирование чанков

**4. Рецепты** (`/recipes`)
- Каталог всех рецептов с поиском и фильтрами
- Группировка по категориям, книгам, ингредиентам
- Карточка рецепта с оригиналом и нормализованной версией

**5. Словари** (`/dictionaries`)
- CRUD для словарных терминов
- Группировка по категориям (меры, растения, орфография, техники)
- Импорт/экспорт

**6. Поиск (тестовый стенд)** (`/search`)
- Интерфейс тестирования поиска (аналог qdrant-search-tester)
- Сравнение dense / sparse / hybrid
- Управление тестами

## Взаимодействие n8n ↔ FastAPI

n8n остаётся оркестратором пайплайнов. Тяжёлая логика выносится в FastAPI-сервисы, которые n8n вызывает через HTTP Request.

### Workflow в n8n (рефакторинг):

**1. `book-processing-pipeline`** (новый, единый)
```
Webhook trigger (от FastAPI)
  → Скачать PDF из MinIO
  → OCR (Tesseract / Gemini для сканов)
  → Нормализация текста (FastAPI: /api/normalize)
  → Парсинг рецептов (FastAPI: /api/recipes/parse)
  → Индексация (FastAPI: /api/indexing/book/{id})
  → Обновить статус книги (FastAPI: PATCH /api/books/{id})
```

**2. `hybrid-search` (subroutine)** — существующий, без изменений
```
Trigger from workflow → BGE-M3 → Qdrant prefetch → RRF → результаты
```

**3. `telegram-bot`** — существующий, подключается к FastAPI для поиска
```
TG Trigger → Set vars → AI Agent (с tool: FastAPI /api/search) → TG Response
```

### Принцип разделения:
- **n8n**: оркестрация, триггеры, Telegram, AI Agent, визуальные пайплайны
- **FastAPI**: бизнес-логика, парсеры, словари, работа с БД, индексация

## Обработка дореволюционных текстов

### Пайплайн нормализации:

```
Исходный текст (до 1917)
  │
  ├─ 1. Орфографическая нормализация (dictionary: orthography)
  │     ѣ → е, i → и, ъ (конец слов) → удалить, ѳ → ф
  │
  ├─ 2. Лексическая нормализация (dictionary: plants, techniques)
  │     Старинные названия → современные
  │
  ├─ 3. Конвертация мер (dictionary: measurements)
  │     золотник → 4.27г, штоф → 1.23л, ведро → 12.3л
  │     градусы Реомюра → Цельсий (×1.25)
  │
  └─ 4. Структурное извлечение (recipe parser)
        Ингредиенты, количества, шаги процесса
```

### Извлечение структурированных данных:

Парсер рецептов определяет:
- **Название** рецепта
- **Категорию** (водка, настойка, ликёр, бальзам, масло, вода)
- **Список ингредиентов** с количествами (old + modern)
- **Процесс приготовления** (шаги)
- **Время настаивания**, крепость, объём (где указано)

## Структура репозитория

```
historical-recipes/
├── docs/
│   ├── ARCHITECTURE.md        # этот документ
│   └── n8n-workflows/         # оригинальные workflow (справочно)
│
├── backend/                   # FastAPI
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── models/            # SQLAlchemy / Pydantic модели
│   │   │   ├── book.py
│   │   │   ├── recipe.py
│   │   │   └── dictionary.py
│   │   ├── routers/           # API endpoints
│   │   │   ├── books.py
│   │   │   ├── recipes.py
│   │   │   ├── dictionaries.py
│   │   │   ├── search.py
│   │   │   └── indexing.py
│   │   ├── services/          # Бизнес-логика
│   │   │   ├── ocr.py
│   │   │   ├── normalizer.py  # нормализация текстов
│   │   │   ├── parser.py      # парсинг рецептов (из n8n Code nodes)
│   │   │   ├── embedder.py    # BGE-M3 клиент
│   │   │   ├── qdrant.py      # Qdrant клиент
│   │   │   └── minio.py       # MinIO клиент
│   │   └── utils/
│   │       ├── text.py        # утилиты для текста
│   │       └── measures.py    # конвертация мер
│   ├── tests/
│   ├── requirements.txt
│   ├── Dockerfile
│   └── alembic/               # миграции БД
│
├── frontend/                  # Next.js
│   ├── src/
│   │   ├── app/               # App Router
│   │   │   ├── page.tsx       # Dashboard
│   │   │   ├── books/
│   │   │   ├── recipes/
│   │   │   ├── dictionaries/
│   │   │   └── search/
│   │   ├── components/
│   │   └── lib/
│   │       └── api.ts         # FastAPI client
│   ├── package.json
│   └── Dockerfile
│
├── docker-compose.yml         # всё вместе
├── .env.example
├── README.md
└── .gitignore
```

## Docker Compose (сервисы)

```yaml
services:
  frontend:     # Next.js :3000
  backend:      # FastAPI :8000
  # Внешние (уже развёрнуты на VDS):
  # n8n:        :5678
  # supabase:   :5432
  # qdrant:     :6333
  # minio:      :9000
  # bge-m3:     :8100
  # nginx:      :80/:443
```

## Приоритеты реализации

### Фаза 1 — Фундамент
- [ ] Структура репо, Docker Compose, базовые конфиги
- [ ] FastAPI: модели, миграции, CRUD для книг
- [ ] MinIO: загрузка/хранение PDF
- [ ] Next.js: страница библиотеки книг (upload, list, status)

### Фаза 2 — Пайплайн обработки
- [ ] Вынос парсеров из n8n Code nodes → FastAPI services
- [ ] Нормализатор текста (орфография, меры)
- [ ] Словари (CRUD + lookup API)
- [ ] n8n: единый workflow обработки книг

### Фаза 3 — Поиск и индексация
- [ ] FastAPI: embedder (BGE-M3 клиент)
- [ ] FastAPI: Qdrant клиент (индексация + поиск)
- [ ] Next.js: тестовый стенд поиска (замена Streamlit)
- [ ] Интеграция с TG-ботом

### Фаза 4 — Улучшения
- [ ] Авто-определение формата книги (PDF image vs text)
- [ ] ML-парсер для структурированных рецептов
- [ ] Рекомендательная система (совместимость ингредиентов)
- [ ] Аналитика использования бота
