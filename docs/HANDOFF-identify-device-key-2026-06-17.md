# HANDOFF → backend: `/identify` must store `device_key` (badges count 0 without it)

**От:** мобильный агент «Что растёт». **Дата:** 2026-06-17.

## Проблема (сквозная, блокирует ВСЕ бейджи)
Прогресс бейджа (`quests.badge_progress`) считается из `identifications` с фильтром
`WHERE device_key = CAST(:dk AS uuid)`. Но эндпоинт **`POST /api/identify/` не принимает
и не пишет `device_key`** — в `Identification` archive (`_archive` в
`backend/app/routers/identify.py`) есть lat/lng/captured_at/device_model/…, а `device_key`
не передаётся. Итог: `identifications.device_key` всегда `NULL` → прогресс бейджа `0/N`
**у всех**, независимо от места/окна/режима. Колонка `device_key` есть (alembic 017), но не
заполняется.

Это НЕ про «режим прогулок»: обычное определение и камера прогулки идут через один и тот
же `/identify`. Чинить надо одну точку.

## Клиент уже сделал (chto-rastet-android)
`Api.identify` теперь шлёт form-поле **`device_key`** (UUID, тот же, что в
`/devices/register`) при КАЖДОМ определении. Закоммичено.

## Нужно на бэкенде (2 строки)
В `backend/app/routers/identify.py`:
1. Принять Form-параметр в `identify_plant(...)`:
   ```python
   device_key: str | None = Form(None),
   ```
   и прокинуть в `_archive(...)`.
2. В `_archive(...)` записать его в модель:
   ```python
   db.add(Identification(..., device_key=device_key, ...))
   ```
   (валидировать как UUID по желанию; `None` оставлять как есть.)

После этого geo-определения начнут накапливать прогресс бейджа автоматически (внутри
полигона места × окна сезона, виды из набора). Проверка: определить вид из набора Битцы с
гео внутри парка в окне `second-half-06` → `GET /api/quests/badge/progress` должен показать
`matched ≥ 1`.

## Заметка про гео
Прогресс требует lat/lng у определения (ST_Contains по полигону). Клиент шлёт гео только
при выданном разрешении на локацию — без него определение не попадёт в бейдж (ожидаемо).
