# Промпты итерации 2

Режим: встроенная генерация изображений. Каждый экран и каждый экспортный ассет создавался отдельным вызовом.

## Общий промпт экранов

> Use case: ui-mockup. Shippable mobile app screen, full-screen portrait 9:19.5, no device frame. Approved hybrid B-base + C-walks. Paper #FFF8EC, Cream #FFFDF8, Ink #25231F, Forest #2D5E48, Moss #6F8661, Leaf #E4EDD8, Lime #F2D36C, Mist #EEE9E1, Clay #D78CA5, Danger #A9362A. Literata-like Cyrillic serif headings, Golos Text-like sans body. 4dp grid; 18px rows, 24px cards, 28–32px photo hero. Flat Compose-feasible fills and simple shadows. Light theme only. Russian text verbatim except botanical Latin. 48dp-like targets and AA contrast. No glass, 3D, complex gradients, Material-default look, leaderboard or invented features.

Стилевые референсы: B/Home — палитра и дневниковые карточки; B/Результат — гравюра и типографика; C/Прогулки — композиция карт, радиуса и участников. В каждом экранном промпте далее перечислялись блоки в точном порядке из задания и нормативные строки из [README.md](./README.md).

## Общий промпт ботанических таблиц

> Use case: scientific-educational. Botanical species plate for mobile app, 768x1024 portrait PNG. Botanically accurate whole-plant watercolor engraving in the approved warm B field-journal style; natural proportions, restrained color, crisp diagnostic linework. One mature plant from base to flowering or fruiting top, a detached diagnostic leaf and flower/fruit detail, no labels. Genuinely transparent background with alpha. No text, frame, watermark, unrelated plants, insects, vase or fantasy morphology. Species recognition is more important than beauty.

Для двух повторных генераций — `arctium_tomentosum` и `tilia_cordata` — прозрачный фон заменён на Paper `#FFF8EC`, чтобы не повторять пустой результат.

### Диагностические добавки по видам

- `urtica_dioica`: opposite ovate-lanceolate sharply serrated leaves, cordate bases, fine stinging hairs, drooping branched green axillary flower clusters.
- `artemisia_absinthium`: overall silvery-gray hairs, lower leaves deeply two-to-three-pinnatisect, narrow rounded lobes, tiny yellow spherical heads in loose panicles.
- `achillea_nobilis`: alternate finely divided gray-green leaves, broad compact flat-topped cream-white corymb with pale yellow centers; no pink.
- `aegopodium_podagraria`: triangular twice-ternate leaves, ovate serrated leaflets, grooved hollow stem, flat compound white umbels.
- `prunella_vulgaris`: low square reddish stems, opposite ovate leaves, dense terminal spike of violet bilabiate flowers with broad dark bracts.
- `rubus_idaeus`: lightly prickly cane, 3–5 serrated leaflets pale beneath, white flowers and red aggregate fruits.
- `chelidonium_majus`: blue-green pinnate rounded-lobed leaves, yellow four-petaled flowers, long cylindrical capsules, orange latex detail.
- `artemisia_vulgaris`: reddish grooved stem, leaves green above and white-felted below, small reddish-brown heads; not uniformly silver.
- `saponaria_officinalis`: opposite ovate-lanceolate leaves with three strong veins, pale pink five-petaled flowers with tubular calyces.
- `arctium_tomentosum`: huge cordate leaves white-woolly beneath, purple heads with hooked bracts and conspicuous cobwebby wool.
- `veronica_longifolia`: opposite or whorled narrow serrated leaves, long tapering blue-violet spikes.
- `rosa_rugosa`: thorny branch, 5–9 strongly wrinkled serrated leaflets, large single pink five-petaled flowers, round orange-red hips.
- `cirsium_arvense`: multiple small lilac-purple heads, alternate deeply lobed spiny leaves, no winged stem, no giant solitary head.
- `plantago_major`: basal broad ovate rosette with 5–9 curved ribs, leafless scapes and narrow cylindrical spikes.
- `trifolium_repens`: creeping rooting stolons, rounded trifoliate leaves with pale V marks, globular white flower heads.
- `vicia_cracca`: pinnate leaves with 8–12 leaflet pairs and branched tendrils, dense one-sided violet pea-flower racemes.
- `tanacetum_vulgare`: deeply pinnate fern-like leaves, flat clusters of yellow button disc heads, absolutely no ray petals.
- `sonchus_oleraceus`: soft pinnatifid leaves, upper leaves clasping with auricles, multiple yellow heads, no hard thistle spines.
- `tilia_cordata`: small serrated cordate leaves with asymmetric bases, cream flower clusters attached to long strap-shaped bracts, round nutlets.
- `chamaenerion_angustifolium`: alternate narrow lanceolate leaves, long raceme of magenta four-petaled flowers, slender capsules below.

## Таб-бар

Общая часть:

> Mobile tab bar icon, 512x512 PNG. Warm botanical field-journal icon, hand-painted watercolor plus crisp engraved linework, simple strong silhouette, balanced centered composition, readable at 24–28dp. Forest #2D5E48, Moss #6F8661, Leaf #E4EDD8, small Lime #F2D36C only for active state. Genuinely transparent alpha. No text, frame, square tile, external shadow, watermark or 3D.

- Определить: botanical sprout + magnifying glass.
- Прогулки: compact backpack + leaf + short winding trail.
- Коллекция: closed herbarium book + pressed leaf.
- Active: Forest fill and one small Lime point.
- Inactive: Moss engraved outline, pale Leaf wash, no Lime.

## Прочие ассеты

- **Фон камеры:** delicate fern, tansy, clover and meadow edge illustration; central 55% empty; very low visual opacity; transparent alpha.
- **Пустая коллекция:** open blank herbarium notebook, loose leaf, pencil and tiny camera charm; no text; transparent alpha.
- **Нет прогулок:** gentle forest path, resting backpack and sparse botanical sprigs; no text; transparent alpha.
- **Map tile:** flat green-paper cartography; Leaf forest masses, Moss vegetation marks, light path, dark dotted trail, muted stream; opaque; no labels, pins, controls or radius.
