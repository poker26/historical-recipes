# Финальный набор промптов

Режим: встроенная генерация изображений, отдельная генерация каждого PNG. После генерации исходные полотна 852 × 1846 были механически масштабированы без изменения композиции до 1080 × 2340.

Ниже зафиксирован смысловой набор промптов. Общая часть для всех девяти изображений:

> High-fidelity mobile UI concept, single full-screen portrait canvas, exact 9:19.5 ratio, no device frame, no hands, no presentation board. Product «Что растёт». Light theme. Compose-feasible flat fills, rounded rectangles and simple shadows; no glass blur, complex gradients or 3D. All visible UI text is Russian exactly as supplied, except botanical Latin. Accessible contrast, large touch targets, safety states use icon plus text. Preserve the exact top-to-bottom block order from TASKcodexdesignv2.md.

## A «Научный определитель»

Стилевой модификатор:

> Calm, precise, trustworthy, airy scientific field guide. Palette #FFF9EF #FFFDF8 #20251F #245940 #5F7E59 #DCE9D9 #F4D66B #E8EAF8 #D68EAF, danger #A9362A. Elegant PT Serif-like Cyrillic headings with disciplined humanist sans. Exact modular grid, accurate specimen photography, restrained botanical line illustrations, 20–24px rounding.

- **A/Home:** камера-first; компактная шапка; полный camera hero; компактный reward; три карточки в одном горизонтальном ряду с полными крючками и бейджами; ботанический tab bar.
- **A/Результат:** заметное фото иван-чая; hero-кандидат; только затем кредит прогулки и биотоп; две альтернативы; громкий красный грибной баннер.
- **A/Прогулки:** строгие модульные карточки «рядом», активная прогулка, два парка, мини-карта с кругом и ручкой радиуса; активен таб «Прогулки».

## B «Живой полевой дневник»

Стилевой модификатор:

> Warm personal photo-led field journal, refined not childish. Palette #FFF8EC #FFFDF8 #25231F #2D5E48 #6F8661 #E4EDD8 #F2D36C #EEE9E1 #D78CA5, danger #A9362A. Literata/PT Serif-like Cyrillic headings plus Golos Text-like sans. Organic 28–32px rounding, tactile paper cards, minimal hand-drawn underline accents, rich botanical photography, no scrapbook clutter.

- **B/Home:** фотография внутри hero, мягкая «бумажная» кнопка галереи, дневниковая награда, три фото-карточки с полными текстами.
- **B/Результат:** пользовательский кадр крупнее, иллюстративный иван-чай переходит в карточку, вторичные прогрессы оформлены как записи дневника.
- **B/Прогулки:** фото леса доминируют в «рядом», активной прогулке и парках; карта остаётся функциональной и плоской.

## C «Совместная экспедиция»

Стилевой модификатор:

> Energetic social field expedition while preserving camera-first hierarchy. Palette #FFF8ED #1F2B22 #174C37 #4D7658 #DCE9D2 #F2D45C #E6ECDF #D9829F, danger #A9362A. Characterful Noto Serif/PT Serif-like Cyrillic display plus bold Golos Text-like sans. Confident 20–28px rounding, bolder color blocks, route and map motifs, small human avatars, warm botanical imagery, not game UI.

- **C/Home:** самый энергичный camera hero; сезонные карточки становятся вертикальными строками, сохраняя порядок и весь контент; tab bar плотнее.
- **C/Результат:** фото и ответ образуют двухколоночный hero; прогулка поддержана аватарами, но идёт после ответа; предупреждение занимает всю ширину.
- **C/Прогулки:** зелёный блок «рядом», карта активной прогулки, аватары, два парка и жёлтая ручка радиуса; без чатов и лидерборда.

Точные строки интерфейса для каждого экрана находятся в [README.md](./README.md) и имеют приоритет над текстом, который визуальная модель могла исказить.
