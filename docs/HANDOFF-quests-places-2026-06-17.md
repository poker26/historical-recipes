# HANDOFF → backend: quests around PLACES (fast, no live iNat)

**От:** мобильный агент «Что растёт». **Дата:** 2026-06-17.
Контекст: редизайн клиента — квесты строятся вокруг **мест** (вкладка с картой/списком),
а не вокруг медленного live-walk. Список видов места **уже предрассчитан**
(`quest_place_sets`) → его надо отдавать из БД мгновенно. Нужны 2 эндпоинта.

## 1. `GET /api/quests/places/near?lat=&lng=&device_key=&radius_km=25&limit=20`
Места поблизости с открытым набором — для списка и пинов на карте.
```jsonc
{ "places": [ {
    "id": "uuid", "name": "Битцевский парк", "kind": "reserve",
    "lat": 55.58, "lng": 37.55,            // центроид (ST_Centroid) — для пина
    "distance_km": 0.4,                     // от точки юзера
    "window": "second-half-06",
    "set_size": 30, "target": 15,
    "matched": 5,                            // прогресс этого device_key (как в badge/progress)
    "badge_issued": false                    // уже выдан ли бейдж этому устройству
} ] }                                        // отсортировано по distance_km
```
- Только места, у которых ЕСТЬ `quest_place_sets` для текущего окна (иначе квеста нет).
- Радиус-фильтр по центроиду (ST_DWithin / bbox). `device_key` опционален (без него
  `matched=0/badge_issued=false`).

## 2. `GET /api/quests/place/{id}/set?window=&device_key=`
«Что искать здесь» — карточки видов из СОХРАНённого набора, **без live iNat**.
```jsonc
{ "place": {"id","name","window","set_size","target","matched","badge_issued"},
  "items": [ {
     "latin_key": "pulmonaria_obscura",
     "name": "Медуница неясная", "latin": "Pulmonaria obscura",
     "inat_photo": "https://…",      // можно закэшить URL при compute_species_set
     "plant_id": "uuid|null",         // мост в корпус (resolve_latin_to_plants)
     "found": true                    // этот device определял этот вид в полигоне×окне
} ] }
```
- Источник — `quest_place_sets.species_set` (latin_keys). Имя/латынь/фото: при
  `compute_species_set` мы уже тянем taxon из iNat — **сохраните name/photo в наборе**
  (иначе тут снова iNat). `plant_id` через `resolve_latin_to_plants`. `found` — как в
  badge_progress (определения device внутри полигона×окна).
- Это ЗАМЕНА live-walk для квест-мест: быстро, детерминированно.

## Почему это важно
Текущий `/walk` (live iNat, адаптивный радиус) даёт паузу 5-30с и часто `items:[]`
(429 от чистки) — убивает первый экран. Места + сохранённый набор = мгновенно из БД.
`/walk` оставляем только для «что прямо вокруг меня в произвольной точке» (вторично,
лениво), либо позже выпиливаем.

## Просьба к compute_species_set (если ещё не так)
Сохранять в `quest_place_sets` не только `latin_key`, но и **name_ru + inat_photo** на
вид (и, если дёшево, `plant_id`), чтобы `place/{id}/set` не ходил в iNat. Сейчас в наборе
только latin_keys — добавьте параллельные поля или JSONB со {key,name,photo}.

## Инфра
`/quests/*` уже в nginx-whitelist flora → новые роуты доступны без правок.
Связано: `HANDOFF-identify-device-key` (device_key в identify — без него matched/found=0).
