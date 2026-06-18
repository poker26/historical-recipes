# RFC: «Что растёт» — подписки + лента активности (Этап-2 соц-слоя)

**Статус:** спроектировано, НЕ построено. **Дата:** 2026-06-18.
Цель: дать пользователю следить за друзьями — видеть, что они определили и как проходят квесты;
подписываться по ссылке/из лидерборда. Поверх существующего соц-слоя (профиль/значки/лидерборд/
шэры/аватары — в проде, см. [[historical-recipes-deploy-gotcha]]).

## Решения (юзер, 2026-06-18)
1. **Идентичность v1 = `device_key`** (follow-by-link), аккаунт — позже. Схему заложить так, чтобы
   потом наложить `user_id`.
2. **Лента — раздел в Профиле** (не 4-я вкладка).
3. **Лидерборд кликабельный** → чужой профиль → «Подписаться» (петля роста).
4. **Тумблер «Показывать мою активность»**, дефолт ВКЛ.

## ⚠️ Безопасность: публичный `handle` vs приватный `device_key`
Сейчас `device_key` одновременно публичный (URL `/p/{device_key}`) И приватный write-токен
(им атрибутируются `identifications` и берутся значки). Светить его в лидерборде/ленте/подписках
= дать любому слать определения/клеймы от чужого имени. Раз юзеров нет — разводим СЕЙЧАС:
- **`handle`** — короткий уникальный slug (6–8 url-safe симв.), генерится на `register`, иммутабелен,
  лежит в `quest_devices.handle`. Это единственный идентификатор на ВСЕХ публичных поверхностях:
  профиль `/p/{handle}`, лидерборд, цели подписки, актёры ленты.
- **`device_key`** остаётся ПРИВАТНЫМ — только в теле запросов клиента (identify, claim, follow как
  «я»), никогда не отдаётся в публичных ответах/URL.
- Клиент узнаёт свой handle из ответа `register` (или `/quests/profile/by-device/{key}`); шэр-ссылки
  и `card.png` переезжают с `/p/{device_key}` на `/p/{handle}`. Позже `handle` станет username аккаунта.

## Модель данных
- `quest_devices` + `handle` (unique, not null), `activity_public bool default true`, `blocked bool default false`.
- `quest_follows(follower_key uuid, followee_handle text, created_at)` — unique(follower_key, followee_handle).
  follower хранится как device_key (приватно, это «я»); followee — как handle (публичная цель).
  Под будущий аккаунт: добавить nullable `follower_user_id` позже, логику не менять.

## Эндпоинты (backend, `routers/quests.py` + `services/quests.py`)
- `POST /quests/follow` `{device_key, target_handle}` → создаёт ребро (идемпотентно; нельзя на себя; 404 если handle неизвестен/blocked).
- `POST /quests/unfollow` `{device_key, target_handle}`.
- `GET /quests/following?device_key=` → `[{handle, nick, avatar, level, score}]`.
- `GET /quests/followers?device_key=` (опц.) + `GET /quests/profile/{handle}` возвращает `is_following` для запросившего (через `?viewer_device_key=`), чтобы кнопка знала состояние.
- `GET /quests/feed?device_key=&limit=&before=` → слитые по времени события тех, на кого подписан:
  - определения: `identifications` (device_key→handle, top_latin, matched_plant_id, created_at) → `{type:"id", actor, plant:{id,name,photo}|{latin}, at}`;
  - значки: `quest_issued_badges` → `{type:"badge", actor, place, tier, ordinal, at}`.
  - actor = `{handle, nick, avatar, level}`. БЕЗ координат. Исключать `blocked` и `activity_public=false`.
- **Изменить существующее:** `/quests/leaderboard` строки → добавить `handle` (для кликабельности; убрать сырой device_key, если где-то протекал). `register` → возвращать `handle`. `profile/{key}` → принимать handle (а не device_key).
- `PATCH /devices/{key}/privacy` `{activity_public}` (или объединить с nickname/avatar в один settings-PATCH).

## Приватность + блокировка
- `activity_public=false` → не попадаешь в чужие ленты (профиль/лидерборд остаются). Тумблер в Профиле.
- Геоприватность: координат нет нигде (как сейчас).
- `blocked=true` (модерация, отдельный RFC/гейт D1) → скрыт из лидерборда/профиля/ленты/целей подписки.
- Поштучная приватность находок — на будущее.

## Клиент (chto-rastet-android, commonMain где можно)
- **Чужой профиль** (новый экран): открывается диплинком `/p/{handle}`, тапом по строке лидерборда,
  по актёру ленты. Паспорт (ник, аватар+венок, уровень, значки) + кнопка **«Подписаться/Отписаться»**.
  Данные из `/quests/profile/{handle}?viewer_device_key=`.
- **«Друзья и лента»** — раздел/карточка в Профиле (не таб): список подписок + лента событий
  (`/quests/feed`), бесконечная прокрутка по `before`.
- **Лидерборд** — строки кликабельны → чужой профиль.
- **Шэр-ссылки/карточки** — перевести на `/p/{handle}` (клиент берёт handle из register/profile).
- Тумблер «Показывать мою активность» в Профиле → `PATCH …/privacy`.

## Фазы
- **P1 (backend):** handle (миграция + генерация на register + перевод profile/leaderboard/card на handle), `quest_follows`, follow/unfollow/following, feed, privacy-флаг. Смоук curl'ом.
- **P2 (client):** чужой профиль + follow-кнопка + кликабельный лидерборд; раздел «Друзья и лента» в Профиле; перевод шэр-ссылок на handle; тумблер приватности.
- **P3:** модерация ников (фильтр-на-сейве + `blocked` + страница в mTLS-админке) — гейт перед открытием публичного лидерборда реальным людям.

## Будущее (не сейчас)
- **Опц. аккаунт**: `handle` становится username; `device_key`→`user_id` связь; кросс-устройство + поиск друзей.
- **Пуш** «друг получил редкий значок / обошёл тебя» (нет пуш-инфры сейчас).
- Поштучная приватность находок; со-оп «пошли в лес» (общий прогресс по месту×сезону).
