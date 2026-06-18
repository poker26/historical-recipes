# HANDOFF → backend/web: соц-слой Этап 1 (лендинг + публичный профиль + read-эндпоинты)

**От:** мобильный агент «Что растёт». **Дата:** 2026-06-18.
План: `~/.claude/plans/foamy-sparking-milner.md` (одобрен). Этап 1 = замкнуть виральную
петлю: есть что шарить → ссылка ведёт на красивую публичную страницу → оттуда «Скачать».
Это в основном **бэкенд + новый веб-лендинг** (клиент делает шаринг/deep-link отдельно).

## 1. Новые ПУБЛИЧНЫЕ read-эндпоинты (данные уже есть, эндпоинтов нет)
В `app/routers/quests.py` + `app/services/quests.py`:

- `GET /api/quests/profile/{device_key}` — публичный «паспорт натуралиста»:
  ```jsonc
  { "nick": "Зелёный Бегемот #1234", "level": {"n":3,"title":"знаток","species":22},
    "score": 145, "rank": 42,
    "badges": [ {"tier":2,"name":"любитель","place":"Битцевский парк","window":"second-half-06","year":2026,"ordinal":37,"points":15,"issued_at":"…"} ] }
  ```
  ⚠️ **Геоприватность:** НЕ включать lat/lng/точные точки — только имя места.
  `level` = тот же расчёт, что на клиенте (1/5/15/40/100 видов; species = distinct top_latin
  по device_key). Если хотите — отдавайте `species_count`, title клиент/веб сам сопоставит.

- `GET /api/quests/recent-badges?limit=20&place_id=<opt>` — лента соц-доказательства:
  `[{nick, tier, name, place, window, issued_at}]` из `quest_issued_badges ⋈ quest_devices`,
  по `issued_at desc`. Без гео.

- `GET /api/quests/leaderboard?scope=global|place|season&place_id=&window=&year=&limit=` —
  расширить текущий **global-only** `leaderboard` (`services/quests.py`) скоупами:
  - `place` → топ по очкам значков ЭТОГО места (все окна) или место×окно;
  - `season` → топ за окно×год.
  Формат строки прежний (`rank,nick,score,badges`) + `me`.

- Все новые роуты — в **nginx-whitelist флоры** (как и прочие `/api/*`).

## 2. Лендинг — НОВЫЙ отдельный сайт (решение юзера)
НЕ текущая Next.js-админка на flora root. Отдельный лёгкий SSR/статик-проект, свой контейнер,
nginx-vhost. **ДОМЕН РЕШЁН: `botanik.fun` → A `46.173.19.68`** (купленный отдельный домен, не
поддомен — переделывать не придётся; запись только что добавлена, ждём пропагацию .fun). Тот же
сервер 1, что и flora; добавить server-block + acme-сертификат на `botanik.fun`. Страницы:
- `/` — герой + скриншоты + кнопки **«Скачать»** (RuStore `ru.begemot.plantid` + App Store
  `ru.begemot.whatgrows`) + живой тизер лидерборда + лента `recent-badges` + текущие квесты.
- `/p/{device_key}` — публичный профиль (из `profile/{device_key}`); кнопки «Скачать/
  Присоединиться»; OG-теги (preview-картинка в мессенджерах!).
- `/leaderboard` — табы global/place/season.
- `/quest/{place_id}/{window}` — что искать + значки места + топ места + «Скачать чтобы пройти».
- **Для deep-link:** на лендинге выложить `/.well-known/assetlinks.json` (Android App Links) и
  `/.well-known/apple-app-site-association` (iOS Universal Links) — клиент пришлёт fingerprint/
  appID, когда домен будет выбран.

## 3. Домен — РЕШЁН (2026-06-18)
**`botanik.fun` → A `46.173.19.68`** (сервер 1). Отдельный купленный домен, финальный — не
менять. Лендинг + эндпоинты больше ничем не блокированы. Шар-ссылки и app-links/AASA — на
`https://botanik.fun/...` (`/p/{id}`, `/quest/{place}/{window}`, `/.well-known/assetlinks.json`,
`/.well-known/apple-app-site-association`).

## Что делает клиент (НЕ бэкенд, для контекста)
Шаринг (Android ACTION_SEND / iOS UIActivityViewController), генерация карточки-картинки
значка/профиля, deep-link/app-link хэндлинг, экран ника/аватара. Surface ника + rename через
существующий `PATCH /api/devices/{key}/nickname` — делаю сейчас.

## Дальше (Этапы 2-3, контекст — не сейчас)
Опц. аккаунт (`users`, device_keys→user_id, перенос прогресса) + друзья-по-ссылке (`follow`) +
со-оп «пошли в лес» + доски/лента в приложении. Метод входа (OAuth vs magic-link) — позже.
Реферал/«Наставник» — ИСКЛЮЧён по решению юзера.
