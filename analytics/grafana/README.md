# «Что растёт» — аналитика в Grafana

Дашборд по СОБСТВЕННЫМ данным приложения (не ждём статов из сторов). Источник — наш Postgres
(Supabase), Grafana ходит в него напрямую read-only. Никакого ETL.

## Архитектура
- **БД:** Supabase Postgres на server 3 (`45.12.72.157`, pg `:5433`).
- **Grafana:** уже работает там же — `45.12.72.157:3100` (см. [[vds_fleet]]).
- **Доступ:** отдельная read-only роль `grafana_ro` → SELECT только на `quest_devices`,
  `identifications`, `plants`. Никаких прав на запись.

Данные, которые есть (без доработок бэкенда):
- `quest_devices` — регистрации (`created_at`, `last_seen`, handle/avatar).
- `identifications` — каждое определение по фото: `created_at`, `device_key`, гео (`lat`/`lng`),
  `device_model`/`device_manufacturer`/`os_sdk`/`os_version`/`app_version`, `engine`, `organ`,
  `top_latin`, `top_score`, `matched_plant_id` (в корпусе ли вид), `candidates`.
- **Платформа** выводится из данных (явной колонки нет): Apple/`iphone` → iOS; иначе `os_sdk>0` /
  есть manufacturer → Android.

## Панели дашборда (`dashboard-chto-rastet.json`)
1. Всего устройств / Всего определений / Активные за 7 дней / % снимков «в корпусе» (stat+gauge).
2. Новые устройства в день · Определения в день (bars).
3. DAU в день · Версии приложения в день (видно раскатку 1.5).
4. Платформа (донат iOS/Android) · Топ определяемых видов (таблица + сколько в корпусе).
5. Карта определений (geomap по `lat`/`lng`).

## Развёртывание (один раз)
1. **Роль БД** — на server 3, как `supabase_admin` (роль `postgres` урезана):
   ```
   psql "postgresql://supabase_admin:<pw>@localhost:5433/postgres" -f grafana_readonly_role.sql
   ```
   Заменить `CHANGE_ME_STRONG_PASSWORD` на реальный пароль (в файле или `ALTER ROLE grafana_ro PASSWORD '...'`).
2. **Datasource в Grafana** (`:3100` → Connections → Data sources → Add → PostgreSQL):
   - Host: `localhost:5433` (Grafana и БД на одном хосте; если Grafana в docker — host = адрес БД-контейнера/`host.docker.internal`).
   - Database: `postgres` · User: `grafana_ro` · Password: <из шага 1> · TLS/SSL Mode: `disable` (локальный хост) · Version: 15+.
   - Save & test.
3. **Импорт дашборда** (Dashboards → New → Import → Upload `dashboard-chto-rastet.json`) →
   выбрать созданный datasource в поле `DS_POSTGRES` → Import.
4. Диапазон времени сверху (по умолчанию `now-90d`), авто-обновление 30 мин.

## Заметки
- Гео есть только когда юзер дал разрешение на локацию — карта показывает подмножество.
- Плагин дашборда read-only; писать в БД роль не может (проверено REVOKE).
- Хотим больше (воронка «снял → открыл монограф → получил значок», retention-когорты) —
  это уже потребует доп. событий с клиента; текущий набор строится на том, что уже пишется.
