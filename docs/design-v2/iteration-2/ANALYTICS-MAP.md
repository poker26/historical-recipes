# Аналитическая карта редизайна v2.0

Цель: измерять пути, активное время, востребованность секций и проблемные места интерфейса без записи фото, точных координат и персональных социальных идентификаторов.

Базовая модель и privacy-ограничения заданы в [RFC](../../RFC-mobile-product-social-walks-analytics.md). Этот документ связывает их с конкретными компонентами макетов.

## 1. Стабильные screen_id

| Экран | `screen_id` | Корневой/детальный |
|---|---|---|
| Home «Определить» | `identify.home` | root |
| Результат | `identify.result` | detail |
| Снимок сохранён | `identify.offline` | detail/state |
| Карточка растения | `plant.detail` | detail |
| Прогулки | `walk.hub` | root |
| Что растёт рядом | `walk.nearby` | detail |
| Конструктор | `walk.custom.builder` | detail |
| Активная прогулка | `walk.active` | detail |
| Коллекция | `collection.home` | root |
| Профиль пользователя | `profile.own` | detail |
| Чужой профиль | `profile.other` | detail |

Не кодировать ID растения, места или пользователя в `screen_id`. Контекст передавать только безопасными категориями в properties.

## 2. Базовый контракт времени

- `screen_viewed`: экран стал главным, с `previous_screen_id` и `entry_point`.
- `screen_left`: экран перестал быть главным, с `active_ms` и `exit_type`.
- `app_backgrounded`: приложение потеряло foreground, текущий интервал закрыт.
- Для долгих экранов `walk.active` и `walk.custom.builder` нужен heartbeat каждые 60–120 секунд.
- После heartbeat новый интервал начинается с нуля; лимит применяется к интервалу, а не ко всему пребыванию на прогулке.
- Системный permission dialog и внешний share sheet не считаются активным временем основного экрана после согласованного timeout.

### Найденный разрыв текущей реализации

Текущий `Analytics.kt` уже создаёт `screen_viewed`, `screen_left`, `app_backgrounded`, хранит bounded offline queue и ограничивает ошибочно длинные интервалы. Однако:

- `LAYOUT_VERSION` пока равен `1`;
- `screen_left` не содержит `exit_type`;
- heartbeat отсутствует;
- 10-минутный cap обрезает длительную активную прогулку целиком;
- текущий root ID третьего таба — `tab.profile`, хотя v2 требует `collection.home` и отдельный `profile.own`.

Эти пункты являются требованиями к реализации, а не утверждением, что они уже исправлены.

## 3. Общие element_id

| Элемент | `element_id` |
|---|---|
| Камера Home | `identify.camera` |
| Галерея Home | `identify.gallery` |
| Аватар профиля | `global.profile_avatar` |
| Таб Определить | `tab.identify` |
| Таб Прогулки | `tab.walks` |
| Таб Коллекция | `tab.collection` |
| Back | `global.back` |
| Грибное предупреждение | `safety.mushroom` |
| Предупреждение о ядовитости | `plant.safety` |

Любой `ui_element_tapped` содержит `element_id` и текущий `screen_id`. Видимый, но не нажатый элемент не создаёт tap event.

## 4. События по экранам

### Home

| Действие | Событие / свойства |
|---|---|
| Камера | `identify_capture_started`, `source=camera` |
| Галерея | `identify_capture_started`, `source=gallery` |
| Полка сезона видима | `section_impression`, `section_id=identify.seasonal` |
| Открыта карточка полки | `ui_element_tapped`, `element_id=identify.seasonal.plant` |
| Награда видима | `section_impression`, `section_id=identify.reward` |
| Значок забран | `biotope_tier_claimed`, tier category only |

### Результат и offline

| Действие | Событие / свойства |
|---|---|
| Результат получен | `identify_completed`: duration_ms, candidate_count, in_corpus, confidence_band |
| Альтернатива | `identify_alternative_selected`: rank |
| Атлас | `ui_element_tapped`: `identify.open_atlas` |
| Ошибка сети | `identify_failed`: stage, error_code, network_state |
| Pending сохранён | `finding_saved`: `automatic`, `pending=true` |
| Retry | `retry_tapped`: error_code |
| Возврат к камере | `ui_element_tapped`: `identify.offline.camera` |

Не передавать photo id, байты изображения, точный plant id или пользовательский текст.

### Карточка растения

- `plant_detail_opened`: entry_point, in_walk.
- `plant_section_viewed`: `safety`, `verdict`, `uses`, `compounds`, `harvest`, `recipes`, `sources`.
- `plant_recipe_opened`: позиция карточки, без копирования текста рецепта.
- `plant_source_opened`: тип источника и век/диапазон года при необходимости агрегации.
- `plant_returned_to_walk`.
- `scroll_depth_reached`: 25/50/75/100.

`plant_section_viewed` отправляется один раз за screen visit после ≥50% видимости секции в течение ≥800 мс.

### Коллекция

| Элемент | Событие |
|---|---|
| Поиск открыт | `collection_search_started` |
| Результат поиска | `collection_search_completed`: result_count_band; без query |
| Фильтр | `collection_filter_changed`: all/species/places/biotopes |
| Находка | `collection_item_opened`: status=identified/pending |
| Empty CTA | `ui_element_tapped`: `collection.empty.identify` |

### Профиль

- `profile_opened`: own/other/source.
- `ui_element_tapped`: `profile.badges.all`, `profile.friends`, `profile.rating`.
- `privacy_setting_opened`: setting kind only.
- `privacy_setting_changed`: setting kind + from/to enum; не отправлять аудиторию или handle как строки пользователя.

### Прогулки и маршрут

| Действие | Событие |
|---|---|
| Хаб | `walk_hub_opened` |
| Рядом | `nearby_opened` |
| Продолжить | `walk_resumed` |
| Позвать | `walk_invite_started`, channel kind only after share completion |
| Конструктор открыт | `custom_builder_opened` |
| Radius drag | `custom_radius_changed`: from/to bucket, `interaction=drag` |
| Вид движения | `custom_movement_changed`: walk/bike |
| Estimate | `custom_estimate_requested` / `custom_estimate_received` |
| Мало данных | `custom_expand_suggested`: current/min/suggested bucket |
| Расширить | `custom_expand_accepted` |
| Свободно | `custom_expand_rejected`: `alternative=free_walk` |
| Сборка | `custom_generation_started` / completed / failed |

Для карты не отправляются координаты tap heatmap. Разрешены семантические события `map_pan`, `map_zoom`, `custom_center_changed` с coarse region/bucket без GPS-точки.

### Активная прогулка

- `walk_started` / `walk_resumed`.
- `walk_target_map_opened`: remaining_distance_band.
- `walk_camera_opened`.
- `walk_target_attempted`: target position, без species id.
- `walk_participants_viewed`: participant_count_band.
- `walk_targets_expanded`.
- `walk_completed`: duration_band, completion_band, participant_count.
- `walk_abandon_confirmed`: completion_band.

## 5. Карты неудобства

### Карта путей

Строить рёбра по `previous_screen_id → screen_id`. Основные воронки:

1. `identify.home → identify.result → plant.detail`.
2. `identify.home → identify.offline → identify.result`.
3. `walk.hub → walk.custom.builder → walk.active`.
4. `walk.hub → walk.custom.builder → walk.nearby` после low-data.
5. `collection.home → plant.detail`.

Искать неожиданные возвраты, повторные retry, builder без запуска и active walk без камеры.

### Семантическая тепловая карта

Основной вариант для Compose: частота и conversion для стабильных `element_id`. Она сравнима между Android/iOS и не ломается из-за разных размеров экрана.

### Карта внимания

Строить по section impression, active time и scroll depth. Это оценка фактической видимости, не eye tracking.

### Ограниченная координатная tap map

Допустима только для статичных информационных областей после отдельного privacy-решения. Исключить:

- camera hero и изображение камеры;
- карты прогулки и route builder;
- privacy settings;
- пользовательские фото.

## 6. Минимальные отчёты после запуска

1. Доля Home-сессий, закончившихся camera/gallery start.
2. Успех `identify_completed` и восстановление после offline/retry.
3. Открытие plant sections и глубина до recipes/sources.
4. Использование фильтров коллекции и доля pending-находок.
5. Builder: изменение радиуса → viable estimate → started walk.
6. Low-data: expand против free walk и последующий успех.
7. Active walk: время, камера, завершение и выход до первой находки.
8. Переходы к профилю и изменение privacy settings.
9. Сравнение `layout_version=1` и `layout_version=2` на одинаковых метриках.

## 7. Privacy-инварианты

- Нет фото и URL фото.
- Нет точных GPS-координат.
- Нет `device_key`, handle, ника или имени друга.
- Нет поискового текста.
- Нет точного plant id в общей продуктовой аналитике.
- Сбой аналитики не блокирует определение, сохранение или прогулку.
- Очередь ограничена размером и TTL; события имеют UUID для идемпотентности.
