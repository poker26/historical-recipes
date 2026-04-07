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
| **Пре/пост-OCR** | OpenCV (Pillow) | Подготовка изображений, очистка результатов |
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
uploaded → preprocessing → ocr_pending → ocr_done → postprocessing → normalized → parsed → indexed → verified
```

**API:**
- `POST /api/books/upload` — загрузка PDF (→ MinIO)
- `GET /api/books` — список книг с фильтрами по статусу
- `GET /api/books/{id}` — детали книги + история обработки
- `POST /api/books/{id}/process` — запуск обработки (триггер n8n workflow)
- `POST /api/books/{id}/preprocess` — запуск пре-OCR обработки изображений
- `GET /api/books/{id}/pages` — просмотр страниц с OCR confidence
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
  domain TEXT,            -- 'recipes' | 'herbalism' (см. ниже)
  file_path TEXT,         -- путь в MinIO
  status TEXT,            -- текущий статус
  tags TEXT[],            -- доп. теги: 'дистилляция', 'ликёры', 'травник', 'аптека'
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ
)

book_pages (
  id UUID PRIMARY KEY,
  book_id UUID REFERENCES books,
  page_number INTEGER,
  image_path TEXT,          -- путь к странице в MinIO (после split PDF)
  preprocessed_path TEXT,   -- путь к обработанному изображению
  raw_text TEXT,            -- текст после OCR
  ocr_confidence FLOAT,    -- средняя уверенность Tesseract (0-100)
  needs_review BOOLEAN,    -- true если confidence < порога
  dpi INTEGER,             -- разрешение изображения
  status TEXT              -- 'uploaded', 'preprocessed', 'ocr_done', 'reviewed'
)

book_chunks (
  id UUID PRIMARY KEY,
  book_id UUID REFERENCES books,
  chunk_index INTEGER,
  raw_text TEXT,           -- исходный текст после OCR
  cleaned_text TEXT,       -- текст после пост-OCR очистки
  normalized_text TEXT,    -- нормализованный текст
  chunk_type TEXT,         -- 'recipe', 'intro', 'chapter_header', 'other'
  page_start INTEGER,
  page_end INTEGER,
  layout_zone TEXT,        -- 'body', 'header', 'footnote', 'margin_note'
  status TEXT              -- 'raw', 'cleaned', 'normalized', 'reviewed'
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

### 2. `recipes` — Рецепты (домен: recipes)

Структурированные рецепты настоек, дистиллятов, ликёров и т.п.

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
  qdrant_collection TEXT,   -- коллекция в Qdrant
  indexed_at TIMESTAMPTZ
)

recipe_ingredients (
  id UUID PRIMARY KEY,
  recipe_id UUID REFERENCES recipes,
  plant_id UUID REFERENCES plants,  -- связь с каталогом растений
  name TEXT,                -- современное название
  original_name TEXT,       -- название из книги
  amount TEXT,              -- исходное количество ("2 золотника")
  amount_modern TEXT,       -- в современных мерах ("8.5 г")
  unit TEXT,
  unit_modern TEXT
)
```

### 3. `herbalism` — Травник (домен: herbalism)

Знания о растениях: лекарственные свойства, совместимость, применение.
Извлекаются из справочников и травников — отдельный тип книг.

**API:**
- `GET /api/plants` — каталог растений с поиском
- `GET /api/plants/{id}` — карточка растения (свойства, рецепты где используется)
- `POST /api/plants` — добавление растения
- `PUT /api/plants/{id}` — редактирование
- `GET /api/plants/{id}/recipes` — рецепты, содержащие это растение
- `GET /api/plants/{id}/compatible` — совместимые растения
- `POST /api/plants/parse` — извлечение из чанков книги-травника

**Таблицы:**
```sql
plants (
  id UUID PRIMARY KEY,
  name TEXT,                  -- современное название
  name_latin TEXT,            -- латинское название
  names_historical TEXT[],    -- массив старинных названий из разных книг
  family TEXT,                -- семейство (зонтичные, губоцветные и т.д.)
  parts_used TEXT[],          -- используемые части: 'корень', 'лист', 'цветок', 'семя'
  qdrant_point_id TEXT,
  qdrant_collection TEXT
)

plant_properties (
  id UUID PRIMARY KEY,
  plant_id UUID REFERENCES plants,
  property_type TEXT,         -- 'medicinal', 'flavor', 'aroma', 'color'
  property TEXT,              -- 'противовоспалительное', 'горький', 'пряный'
  description TEXT,           -- подробности из книги
  source_book_id UUID REFERENCES books
)

plant_compatibility (
  id UUID PRIMARY KEY,
  plant_a_id UUID REFERENCES plants,
  plant_b_id UUID REFERENCES plants,
  compatibility TEXT,         -- 'synergy', 'neutral', 'conflict'
  context TEXT,               -- 'вкус', 'лечебное действие', 'аромат'
  description TEXT,
  source_book_id UUID REFERENCES books
)

plant_book_mentions (
  id UUID PRIMARY KEY,
  plant_id UUID REFERENCES plants,
  book_id UUID REFERENCES books,
  chunk_id UUID REFERENCES book_chunks,
  original_name TEXT,         -- как называется в этой книге
  original_text TEXT,         -- цитата из книги
  page_number INTEGER
)
```

### Связь доменов

```
┌─────────────────────┐          ┌─────────────────────┐
│   ДОМЕН: RECIPES    │          │  ДОМЕН: HERBALISM   │
│                     │          │                     │
│  Книги рецептов     │          │  Травники,          │
│  настоек, ликёров,  │          │  справочники,       │
│  дистиллятов        │          │  аптекарские книги  │
│                     │          │                     │
│  recipes            │          │  plants             │
│  recipe_ingredients─┼──────────┼─►plant_properties   │
│                     │  plant_id│  plant_compatibility│
│                     │          │  plant_book_mentions│
└─────────────────────┘          └─────────────────────┘
         │                                │
         └──────────┬─────────────────────┘
                    │
              ┌─────▼─────┐
              │  Qdrant    │
              │            │
              │ collection:│
              │  recipes   │ ← рецепты (точный + семантический поиск)
              │  herbalism │ ← свойства растений (семантический поиск)
              └────────────┘
```

**Зачем два домена:**
- Рецепт говорит: "возьми 2 золотника зверобоя" — это **recipes**
- Травник говорит: "зверобой обладает противовоспалительным действием,
  хорошо сочетается с мятой и ромашкой" — это **herbalism**
- При ответе бот может обогатить рецепт информацией из травника:
  почему именно эти ингредиенты, чем заменить, какой эффект

**Две коллекции в Qdrant:**
- `recipes` — поиск рецептов (гибридный: dense + sparse)
- `herbalism` — поиск по свойствам растений (dense)

Бот при ответе может искать в обеих коллекциях для полного ответа.

### 4. `dictionaries` — Словари

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
2. Определить intent: рецепт? свойства растения? общий вопрос?
3. Сгенерировать dense + sparse векторы через BGE-M3
4. Поиск по коллекциям:
   - `recipes`: prefetch dense(20) + sparse(20) → RRF → top 10
   - `herbalism`: dense search → top 5 (если запрос про свойства/растения)
5. Если найден точный рецепт (score > threshold) → вернуть как есть
6. Обогатить ответ данными из herbalism (свойства ингредиентов, совместимость)
7. Вернуть контекст для LLM

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
- Таблица книг с фильтрами по статусу и домену (recipes / herbalism)
- Drag-n-drop загрузка новых PDF с выбором домена
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

**5. Травник** (`/plants`)
- Каталог растений с поиском
- Карточка растения: названия (совр. + старинные + латынь), свойства, совместимость
- Связь с рецептами: в каких рецептах используется
- Граф совместимости ингредиентов (визуализация)

**6. Словари** (`/dictionaries`)
- CRUD для словарных терминов
- Группировка по категориям (меры, растения, орфография, техники)
- Импорт/экспорт

**7. Поиск (тестовый стенд)** (`/search`)
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
  → Пре-OCR (FastAPI: /api/books/{id}/preprocess)
      → Split PDF → страницы
      → OpenCV: binarize, deskew, denoise, scale to 300 DPI
      → Сохранить обработанные изображения в MinIO
  → OCR (FastAPI: /api/books/{id}/ocr)
      → Tesseract (rus) → текст + confidence
      → Страницы с confidence < 60% → Gemini/Qwen fallback
  → Пост-OCR (FastAPI: /api/books/{id}/postprocess)
      → Очистка артефактов, склейка слов
      → Спеллчек по словарям
      → Layout detection, разбиение на чанки
      → Confidence routing (auto / review / manual)
  → Нормализация текста (FastAPI: /api/books/{id}/normalize)
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

## OCR-пайплайн

### Пре-OCR (подготовка изображений)

Критично для старых сканов. Без этого Tesseract выдаёт мусор на пожелтевшей бумаге.

```
PDF страница (изображение)
  │
  ├─ 1. Определение DPI → масштабирование до 300 DPI
  │     Tesseract оптимален именно на этом разрешении
  │
  ├─ 2. Бинаризация (adaptive threshold)
  │     Жёлтый/серый фон старых сканов → чистое ч/б
  │
  ├─ 3. Deskew (выравнивание наклона)
  │     Сканы часто под углом 1-3°, это ломает OCR
  │
  ├─ 4. Удаление шума (morphological operations)
  │     Пятна, точки от старой бумаги
  │
  ├─ 5. Удаление рамок/полей
  │     Тёмные края сканов, тени от переплёта
  │
  └─ 6. Усиление контраста
        Для выцветшего текста (CLAHE)
```

**Реализация:** OpenCV + Pillow, ~50 строк кода в `services/preprocessor.py`

**Хранение:** обработанные изображения → MinIO (`books/{id}/preprocessed/`)

### OCR

```
Обработанное изображение
  │
  ├─ Tesseract (lang=rus+rus_old) → текст + confidence per word
  │
  └─ Gemini/Qwen (fallback для сложных страниц с confidence < 60%)
        Multimodal LLM лучше справляется с нестандартными шрифтами
```

### Пост-OCR (очистка текста)

```
Сырой текст после OCR
  │
  ├─ 1. Удаление артефактов
  │     Лишние |, \n, управляющие символы
  │
  ├─ 2. Склейка разорванных слов
  │     "ощ ущ аю тся" → "ощущается" (частая ошибка Tesseract)
  │
  ├─ 3. Спеллчек по словарю
  │     Сверка с dictionary_terms: "золотиикъ" → "золотникъ"
  │     Особенно важно для названий растений и мер
  │
  ├─ 4. Layout detection (определение зон)
  │     Заголовок / тело рецепта / сноска / маргиналия
  │     По шрифту, отступам, позиции на странице
  │
  ├─ 5. Определение границ рецептов
  │     По нумерации (**, §, №), заголовкам, отступам
  │     Автоматическое разбиение на чанки
  │
  └─ 6. Confidence routing
        confidence ≥ 80% → автоматический пайплайн
        60-80% → пометка для быстрой проверки
        < 60% → на ручную проверку или retry через Gemini
```

**Реализация:** `services/postprocessor.py` + `services/layout_detector.py`

## Нормализация дореволюционных текстов

### Пайплайн нормализации:

```
Очищенный текст (после пост-OCR)
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
│   │   │   ├── plant.py
│   │   │   └── dictionary.py
│   │   ├── routers/           # API endpoints
│   │   │   ├── books.py
│   │   │   ├── recipes.py
│   │   │   ├── plants.py
│   │   │   ├── dictionaries.py
│   │   │   ├── search.py
│   │   │   └── indexing.py
│   │   ├── services/          # Бизнес-логика
│   │   │   ├── preprocessor.py  # пре-OCR обработка изображений (OpenCV)
│   │   │   ├── ocr.py           # Tesseract + Gemini fallback
│   │   │   ├── postprocessor.py # пост-OCR очистка текста
│   │   │   ├── layout_detector.py # определение зон страницы
│   │   │   ├── normalizer.py    # нормализация старинных текстов
│   │   │   ├── parser.py        # парсинг рецептов (из n8n Code nodes)
│   │   │   ├── spellchecker.py  # спеллчек по словарям проекта
│   │   │   ├── embedder.py      # BGE-M3 клиент
│   │   │   ├── qdrant.py        # Qdrant клиент
│   │   │   └── minio.py         # MinIO клиент
│   │   └── utils/
│   │       ├── text.py          # утилиты для текста
│   │       └── measures.py      # конвертация мер
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
│   │   │   ├── plants/
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
