# PLAN: бэкенд-зависимости для квестов (реализация RFC-quests)

Статус: **Design** · 2026-06-15 · бэкенд-сторона `RFC-quests.md` (клиентский RFC).

Квесты — слой ВОВЛЕЧЕНИЯ поверх ядра. Этот документ — что строит БЭКЕНД. Опирается на:
определение по фото (прод), Историю определений (`identifications`, серверная), iNat
(`inaturalist.py`), `_latin_key`-мост, читательский монограф (награда, НЕ гейт).

---

## 0. Предпосылки (без них ничего)
1. **PostGIS** — `CREATE EXTENSION postgis;` (через `supabase_admin`; сейчас НЕ установлен). Гео-полигоны мест + point-in-polygon = ядро.
2. **Идентичность устройства** — у `identifications` НЕТ device-ключа. Добавить `device_key` (см. §1), иначе прогресс не привязать к устройству.

## 1. Идентичность — silent device-key (RFC §8a)
- Таблица `devices(device_key uuid PK, created_at, nickname null, last_seen)`. Без PII.
- `POST /api/devices/register {device_key}` → upsert, идемпотентно. Клиент сам генерит UUID (Keychain/Block Store) и молча шлёт. Ника не спрашиваем.
- Добавить колонку `identifications.device_key` (text, индекс) → каждое определение атрибутируемо устройству. Старые = null.
- Вся квест-выдача ключуется device_key. Никаких аккаунтов/email/IMEI.

## 2. OSM-места (именованные границы)
- Таблица `places(id, osm_id, name, kind, area float, geom geometry(MultiPolygon,4326))`. kind = park/forest/reserve/zone.
- **Ingest**: Overpass API по региону (bbox области) → `leisure=park`, `landuse=forest`, `boundary=protected_area`, `leisure=nature_reserve` c `name` → полигоны в `places`. Сервис `services/osm.py` + разовый/периодический прогон (Temporal `OsmIngestWorkflow`, пейсинг под Overpass).
- **Точка → место**: `GET /api/places/at?lat&lng` → `SELECT id,name FROM places WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(lng,lat),4326)) ORDER BY area ASC LIMIT 1` (самое мелкое накрывающее = самое конкретное). Вложенность: вернуть всю цепочку (по area).

## 3. Движок прогулки «5 рядом» (RFC §4, Слой 1)
- `GET /api/quests/walk?lat&lng&month&theme?` (радиус НЕ параметр — адаптивный) →
  0. **Адаптивный радиус**: старт ~1–2 км, расширяем пока не наберём ~15 кандидатов, кап ~25 км (RFC §13.6).
  1. `find_observations_nearby` (iNat) + **month** (фенология) → виды по встречаемости.
  2. Фильтр: частые ∩ **узнаваемые по фото** (исключаем не-Plantae + курируемый список мхов/злаков/мелочи).
  3. Опц. тема; **безопасность**: для «съедобных» тем — только `plants.is_toxic=false`.
  4. top-5 → карточки `{latin_key, name, inat_photo, plant_id|null}` (plant_id через `_latin_key`; null = нет монографа, ок).
- Корпус НЕ обязателен (вид без карточки — всё равно цель).

## 4. Набор места×окна (цель бейджа, RFC §3a/§4/§13 — РЕШЕНО)
- **Окно = ПОЛОВИНА месяца** (1–15 / 16–конец), имя «первая/вторая половина {месяца}» (RFC §13.1).
- `place_species_sets(id, place_id, window_label, species_set text[], k int, computed_at)` — окно тут **БЕЗ года** (набор межгодовой, см. ниже).
- **Набор = МУЛЬТИГОД** (RFC §13.2): iNat-наблюдения внутри полигона (bbox + `ST_Contains`) в это полумесячное окно **за последние ~3–5 лет** (стабильная фенология, не флук года) → агрегация по таксону → частота → фильтр «узнаваемые по фото» → **топ-K**. **Порог плотности**: мало данных → место даёт ТОЛЬКО прогулки, набор/бейдж не создаём (RFC §13, граничные).
- Temporal `QuestSetBuilderWorkflow` предрассчитывает наборы для известных мест × окон (пейсинг iNat), периодически освежает.

## 5. Бейджи: ГОДОВЫЕ инстансы + выдача (RFC §3a/§5/§9/§13 — РЕШЕНО)
- **Бейдж = ГОДОВОЙ инстанс** `badge_id = {place}·{window_label}·{year}` (напр. `bitsa_first-half-may_2026`). 2026 и 2027 — РАЗНЫЕ бейджи (коллекционишь ежегодно). Набор берётся из `place_species_sets` (межгодовой), а инстанс/прогресс/окно — за конкретный год.
- **Цель = `round(0.6 × |species_set|)`, зажата в `[5, 15]`** (RFC §13.3). Показ — конкретное «**X / N**», НЕ процент. (Не «доля 0.6», а абсолютное число целей.)
- **Прогресс — серверный, верифицируемый из `identifications`** (не доверяем клиенту): для device — `matched` = различные `latin_key` из набора, определённые этим device **внутри полигона** (`ST_Contains` по `identifications.lat/lng`) **в окне ЭТОГО года** (`captured_at` ∈ [year-window_from, year-window_to]). Снимает читерство без тяжёлого античита.
- `issued_badges(id, badge_id, device_key, issued_at, ordinal int, window_closed bool, UNIQUE(badge_id,device_key))`.
- `POST /api/quests/badge/claim {device_key, badge_id}` → (1) окно года ОТКРЫТО (now ≤ year-window_to)? (2) пересчитать matched из identifications; (3) `matched ≥ target` → выдать: `ordinal = count(issued for badge_id)+1` («ты #N»); иначе вернуть `X/N`. После закрытия окна года → `window_closed`, новых не выдаём (**дефицит**).
- `GET /api/quests/badges?device_key` → полка. `GET /api/quests/badge/{id}/progress?device_key` → `{matched: X, target: N}`.
- **Только самое конкретное место** (RFC §13.5); широкие зоны = мета-достижение позже.

## 7. Field-first / офлайн (RFC §7)
- Прогулка + badge-targets + монографы **предзагружаются** клиентом. Прогресс считается локально из Истории; серверная выдача — при синке. Переиспользуем офлайн-механику Истории (`retryPending`).

## 8. Новые таблицы (сводка)
`devices` · `places`(PostGIS) · `place_species_sets` · `issued_badges` · + `identifications.device_key`.

## 9. Фазы сборки
1. **PostGIS + идентичность** (`devices` + `device_key` на identifications + register-эндпоинт) — фундамент.
2. **OSM-места** (ingest + point→place).
3. **Движок прогулки** (find_observations + month + узнаваемость + safety).
4. **Набор места×окна** (Temporal-предрасчёт) + `badge_targets`.
5. **Прогресс из identifications + claim/выдача + полка**.
6. **Окна-тюнинг + офлайн-предзагрузка**.

## 10. Зависимости от очистки данных
- **`_latin_key` / идентичность растений** (Фаза 1 очистки) — мост карточек прогулки к монографам. Чистая латынь → корректные plant_id.
- **reader-монограф** (Слой 2) — приятная награда (но не гейт).
- `find_observations_nearby` (прод) — расширить month/полигоном.
- **Параллелизуемо** с очисткой данных (разные части кода); продуктовая ценность созревает по мере чистоты идентичности + монографа.
