# Арт-пакет: аватары-растения + венки уровней («Что растёт»)

Сгенерируй по этим промптам, пришли PNG — я встрою (лендинг `public/avatars` + `public/wreaths`,
бандл в приложении, композиция в карточке `/p/{id}` и в шэр-картинке).

## Технические требования (ВАЖНО — иначе не встанет чисто)
- **Формат:** PNG с **прозрачным фоном** (alpha). Для венков прозрачность обязательна
  (накладываются поверх аватара). Если модель не умеет alpha — для аватаров можно плоский
  однотонный фон (обрежу в круг), но **венки нужны строго на прозрачном**.
- **Аватары:** 512×512, объект по центру, поля ~8%, без текста.
- **Венки:** 640×640, кольцом по краю, **центр ~380px пустой/прозрачный** (туда ляжет аватар),
  без текста.
- **Единый стиль** у всего сета (один и тот же стиль-преамбл ниже).
- **Имена файлов:** аватары `av-<slug>.png`, венки `wreath-1.png` … `wreath-5.png`.

## Стиль-преамбл (добавляй к КАЖДОМУ промпту аватара)
> Flat vector mascot illustration, cute kawaii style, simple cheerful face (dot eyes + small
> smile), bold clean rounded outlines, soft natural palette (fresh greens with warm accents),
> centered single character, sticker style, plain transparent background, no text, no shadow on
> background. Square.

## Аватары (16 шт., `av-<slug>.png`)
1. `av-cactus` — a round potted cactus hugging itself, shy bashful smile, tiny flower on top
2. `av-sunflower` — a sunflower with a huge happy grin, looking up at the sun
3. `av-flyagaric` — a red fly-agaric mushroom with white dots, sly winking look
4. `av-dandelion` — a dandelion puff with cheeks puffed as if about to scatter, playful
5. `av-monstera` — a single monstera leaf, the natural holes acting as relaxed eyes, chill
6. `av-aloe` — an aloe vera plant, busy/no-nonsense businesslike expression
7. `av-basil` — a basil sprig, gourmet foodie expression, tiny chef vibe
8. `av-rose` — a red rose, haughty coquettish look, one leaf like a raised eyebrow
9. `av-fern` — a curly fern frond, dreamy daydreaming expression
10. `av-oak` — a tiny oak sprout wearing an acorn cap as a hat, proud important look
11. `av-clover` — a four-leaf clover winking, lucky cheerful vibe
12. `av-tulip` — a bright tulip, upbeat energetic morning look
13. `av-wheat` — an ear of wheat, calm zen peaceful expression
14. `av-flytrap` — a venus flytrap, mischievous toothy grin (friendly, not scary)
15. `av-succulent` — a rosette succulent, unbothered deadpan expression
16. `av-pine` — a small fir tree, cheerful, a tiny bit of snow, winter-fresh

## Венки уровней (5 шт., `wreath-<n>.png`) — РАМКА вокруг аватара
Тот же стиль, чуть наряднее. Кольцом по краю кадра, центр прозрачный. Эскалация богатства:
1. `wreath-1` (новичок) — two simple small green leaves at the bottom, minimal half-sprig,
   thin, modest
2. `wreath-2` (любитель) — a slim green leaf wreath, half-circle along the bottom and sides
3. `wreath-3` (знаток) — a full circular leaf wreath with a few small white flowers
4. `wreath-4` (мастер) — a lush full wreath: leaves + small berries + flowers, subtle gold tips
5. `wreath-5` (легенда) — a rich golden laurel wreath, ornate gilded leaves, gentle glow/shine

Все 5 — одинаковый внешний/внутренний диаметр кольца (чтобы аватар садился одинаково на всех уровнях).

## Что я сделаю после получения ассетов
1. Хостинг: `landing/public/avatars/*`, `landing/public/wreaths/*` (+ бандл в приложении).
2. БД: колонка `quest_devices.avatar` (slug) + `PATCH /devices/{key}/avatar`.
3. UI выбора аватара в Профиле (сетка из сета).
4. Композиция аватар + венок(по уровню) в OG-карточке `/p/{id}` + новый `/p/{id}/card.png`.
5. Приложение: «Поделиться карточкой» — тянет `/p/{id}/card.png` и шэрит картинкой (не линком).
