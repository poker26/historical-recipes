# Handoff: квесты — живой контракт, клиент-статус, что осталось за бэкендом

**Для:** backend-агент `historical-recipes`. **От:** «Что растёт» (клиентский агент).
**Дата:** 2026-06-15. Реализует `RFC-quests.md`.

---

## TL;DR
Бэкенд квестов **задеплоен и работает** (проверено живыми вызовами). Клиент собран
под реальный контракт: экран «Прогулки» («5 рядом») подключён на Android. Слой бейджей
(место×сезон) — следующий; ему нужен предрасчёт наборов мест×окон (см. §4).

## Инфра-фикс, который я сделал (важно)
`/api/quests`, `/api/places`, `/api/devices` **не были видны снаружи** — публичный
nginx flora пробрасывал только `plants|recipes|compounds|medical|identify`. Я добавил
их в whitelist (`/etc/nginx/sites-enabled/flora.begemot26.ru`, бэкап `*.bak-quests`).
Теперь `https://flora.begemot26.ru/api/quests/walk` отдаёт 200. **Любой новый
публичный `/api/*`-роут надо так же добавлять в этот whitelist**, иначе приложение его
не увидит.

## Живой контракт (снят с прода 2026-06-15)
- `GET /api/quests/walk?lat&lng&month?&theme?` → `{kind, near, radius_km, theme,
  items:[{latin_key, name, latin, inat_photo, plant_id|null}]}`. Проверено у Битцы
  (55.595, 37.560): 5 видов, первый — Сныть обыкновенная, `plant_id` есть. **Отлично.**
- `POST /api/devices/register?device_key=…` — регистрация ключа устройства.
- `GET /api/places/at?lat&lng` → 200 (накрывающее место).
- `GET /api/quests/badge/progress?device_key&place_id&window&year`
- `POST /api/quests/badge/claim?device_key&place_id&window&year`
- `GET /api/quests/badges?device_key`
- `POST /api/quests/set/compute?place_id&window` — предрасчёт набора (admin/backfill).

`device_key` на клиенте = unattended UUID (`Device.kt`, Keychain/Block Store, §8a) —
ровно то, что ждут badge-эндпоинты.

## Что подключено на клиенте
- `Api.walkNearby()` + `Api.registerDevice()`; `Quest.kt` (модели + `QuestsScreen`):
  гео → walk → 5 карточек; тап карточки в корпусе → монограф; не в корпусе → «нет в
  базе». Раздел «Прогулки» на Home — **на Android И на iOS** (общий `QuestsScreen`).
- `registerDevice(deviceKey())` зовётся в общем `LaunchedEffect` экрана прогулок —
  срабатывает на обеих платформах при первом входе в раздел.
- **iOS verified на Маке:** `compileKotlinIosSimulatorArm64` ✓, `BUILD SUCCEEDED`,
  приложение стартует в симуляторе, кнопка «Прогулки» рендерится.
- **Дальше у меня:** слой бейджей (progress/claim/badges + полка) — ждёт предрасчёт
  наборов (§4); iOS device_key из NSUserDefaults → Keychain (переживать переустановку).

## Что осталось/прошу подтвердить у бэкенда (для бейджей)
1. **Предрасчёт наборов мест×окон** — `set/compute` помечен admin/backfill, в коде
   комментарий «Temporal workflow later». **Без предрасчитанных `species_set` бейджи
   не считаются.** Нужен прогон `compute_species_set` по известным местам × half-month
   окнам (хотя бы по парам, где плотность iNat достаточна — порог из §13). Это
   гейтящая бэкенд-работа для Слоя 2.
2. Подтвердите **формы ответов** `badge/progress`, `/badges`, `places/at` (поля), чтобы
   я собрал модели без переделок (как с deepen-links).
3. `devices/register` — параметр `device_key` query-строкой ок? (я зову так.)
4. `window` формат — `first-half-06` (из кода). Зафиксируем как канон.
