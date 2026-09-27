// Подсветка фрагментов в распознанном тексте страницы. Чистые функции без React.
//
// Цитата факта и текст страницы расходятся в мелочах: переносы слов, разрядка в
// заголовках («Д У Р М А Н Ъ»), знаки препинания, ѣ и і против е и и, латинские буквы
// вместо похожих русских после распознавания. Поэтому обе стороны приводим к одному
// виду (только буквы и цифры в нижнем регистре, старые буквы заменены современными) и
// ищем по этой копии, а найденный диапазон переводим обратно в индексы исходного текста
// через таблицу соответствия. Так подсветка ложится ровно на слова страницы.

export type Needle = { id: string; text: string | null | undefined; strong?: boolean };
export type Range = { start: number; end: number; id: string; strong: boolean };
export type Segment = { text: string; ids: string[]; strong: boolean; anchor: boolean };

const WORD = /[\p{L}\p{N}]/u;
const TRAIL = /[.,;:!?…)\]»"']/;
const CHAR_MAP: Record<string, string> = {
  "ѣ": "е", "і": "и", "ї": "и", "ѳ": "ф", "ѵ": "и", "ё": "е", "й": "и",
  // латинские двойники русских букв, которые путает распознавание
  a: "а", e: "е", o: "о", p: "р", c: "с", x: "х", y: "у", i: "и",
};

/** Сколько знаков нормализованной цитаты берём как начало и как конец. */
const HEAD = 60;
const TAIL = 40;
/** Короче этого цитату не ищем: слово-другое найдётся где угодно. */
const MIN_NEEDLE = 12;

/** Нижний регистр знак в знак: длина строки не меняется, индексы совпадают. */
function lower1(ch: string): string {
  const l = ch.toLowerCase();
  return l.length === 1 ? l : ch;
}

/** Нормализованная копия текста и таблица «индекс в копии → индекс в исходнике». */
export function normalize(src: string): { s: string; map: number[] } {
  const out: string[] = [];
  const map: number[] = [];
  for (let i = 0; i < src.length; i++) {
    const ch = lower1(src[i]);
    if (ch === "ъ" || !WORD.test(ch)) continue;
    out.push(CHAR_MAP[ch] ?? ch);
    map.push(i);
  }
  return { s: out.join(""), map };
}

/** Где цитата лежит в нормализованном тексте страницы: [начало, конец) или null.
 *  Сначала ищем цитату целиком, потом её начало (конец дотягиваем по последним знакам
 *  или по длине цитаты), потом только конец, потом окна из середины. */
export function locate(hay: string, needle: string): [number, number] | null {
  const len = needle.length;
  if (len < MIN_NEEDLE || !hay.length) return null;
  const whole = hay.indexOf(needle);
  if (whole >= 0) return [whole, whole + len];
  if (len <= HEAD) return null;

  const head = needle.slice(0, HEAD);
  const tail = needle.slice(-TAIL);
  const maxSpan = Math.round(len * 1.4) + TAIL;
  const s = hay.indexOf(head);
  if (s >= 0) {
    const t = hay.indexOf(tail, s);
    const e = t >= 0 && t + TAIL - s <= maxSpan ? t + TAIL : Math.min(hay.length, s + len);
    return [s, e];
  }
  const t = hay.indexOf(tail);
  if (t >= 0) return [Math.max(0, t + TAIL - len), t + TAIL];
  for (const f of [0.25, 0.5, 0.75]) {
    const off = Math.floor((len - TAIL) * f);
    const k = hay.indexOf(needle.slice(off, off + TAIL));
    if (k >= 0) {
      const st = Math.max(0, k - off);
      return [st, Math.min(hay.length, st + len)];
    }
  }
  return null;
}

/** Нечёткий поиск для шумного распознавания, где ошибка встречается через каждые
 *  несколько букв и точные окна не находятся. Режем цитату на пятибуквенные кусочки,
 *  находим их на странице и голосуем за сдвиг «позиция на странице минус позиция в
 *  цитате». Если заметная доля кусочков сходится на одном сдвиге, цитата там.
 *
 *  Пороги подобраны на 38 страницах двух книг (чистый скан 1870 года и шумный
 *  текстовый слой 1871 года): находятся 128 из 133 цитат, которым не хватило точного
 *  поиска, и 2 ложных совпадения на 5 668 пар «цитата с соседней страницы той же
 *  книги». Короткие цитаты нечётко не ищем: обороты вроде «Отечество Южная Европа»
 *  повторяются по всей книге. */
const GRAM = 5;
const GRAM_MAX_OCC = 48;
const DIAG_BUCKET = 10;
const FUZZY_MAX_HAY = 150_000;
const FUZZY_MIN_NEEDLE = 60;
/** Доля кусочков цитаты, сошедшихся на одном сдвиге. */
const FUZZY_MIN_SHARE = 0.3;
/** Доля букв цитаты, покрытых этими кусочками. */
const FUZZY_MIN_COVER = 0.4;
/** От первого до последнего совпавшего кусочка не меньше половины цитаты. */
const FUZZY_MIN_SPREAD = 0.5;

export type GramIndex = Map<string, number[]>;

export function gramIndex(hay: string): GramIndex {
  const idx: GramIndex = new Map();
  for (let i = 0; i + GRAM <= hay.length; i++) {
    const g = hay.slice(i, i + GRAM);
    const occ = idx.get(g);
    if (!occ) idx.set(g, [i]);
    else if (occ.length < GRAM_MAX_OCC) occ.push(i);
  }
  return idx;
}

export function fuzzyLocate(idx: GramIndex, hayLen: number, needle: string): [number, number] | null {
  const len = needle.length;
  if (len < FUZZY_MIN_NEEDLE) return null;
  const step = Math.max(1, Math.floor(len / 400));
  const votes = new Map<number, number>();
  const pairs: { j: number; h: number; b: number }[] = [];
  let grams = 0;
  for (let j = 0; j + GRAM <= len; j += step) {
    grams++;
    const occ = idx.get(needle.slice(j, j + GRAM));
    if (!occ) continue;
    for (const h of occ) {
      const b = Math.floor((h - j) / DIAG_BUCKET);
      votes.set(b, (votes.get(b) ?? 0) + 1);
      pairs.push({ j, h, b });
    }
  }
  // Сдвиг плывёт на пропущенных и лишних буквах, поэтому считаем голоса с соседями.
  let best: number | null = null;
  let bestScore = 0;
  votes.forEach((_, b) => {
    let s = 0;
    for (let d = -2; d <= 2; d++) s += votes.get(b + d) ?? 0;
    if (s > bestScore) {
      bestScore = s;
      best = b;
    }
  });
  if (best === null || bestScore < Math.max(4, grams * FUZZY_MIN_SHARE)) return null;
  const b0: number = best;
  const hits = pairs.filter((p) => Math.abs(p.b - b0) <= 2);
  const covered = new Uint8Array(len);
  let first = hits[0];
  let last = hits[0];
  for (const p of hits) {
    if (p.j < first.j) first = p;
    if (p.j > last.j) last = p;
    covered.fill(1, p.j, p.j + GRAM);
  }
  let cover = 0;
  for (let k = 0; k < len; k++) cover += covered[k];
  if (cover < len * FUZZY_MIN_COVER) return null;
  if (last.j - first.j + GRAM < len * FUZZY_MIN_SPREAD) return null;
  const start = Math.max(0, first.h - first.j);
  const end = Math.min(hayLen, last.h + (len - last.j));
  return end > start ? [start, end] : null;
}

/** Разрезать текст на куски по диапазонам. Диапазоны могут перекрываться: у каждого
 *  куска свой набор id, куски без подсветки склеиваются. Первый кусок «яркого»
 *  диапазона получает anchor, на него ставится якорь #hl. */
export function cut(text: string, ranges: Range[]): Segment[] {
  if (!ranges.length) return text ? [{ text, ids: [], strong: false, anchor: false }] : [];
  const points = Array.from(new Set([0, text.length, ...ranges.flatMap((r) => [r.start, r.end])]))
    .filter((p) => p >= 0 && p <= text.length)
    .sort((a, b) => a - b);
  const out: Segment[] = [];
  let anchored = false;
  for (let i = 0; i + 1 < points.length; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (a >= b) continue;
    const cover = ranges.filter((r) => r.start <= a && r.end >= b);
    const ids = cover.map((r) => r.id);
    const strong = cover.some((r) => r.strong);
    const anchor = strong && !anchored;
    if (anchor) anchored = true;
    const prev = out[out.length - 1];
    if (prev && !ids.length && !prev.ids.length) {
      prev.text += text.slice(a, b);
      continue;
    }
    out.push({ text: text.slice(a, b), ids, strong, anchor });
  }
  return out;
}

/** Найти фрагменты фактов в тексте страницы и разрезать текст на куски для <mark>.
 *  found: какие id удалось найти (у остальных ссылку «показать на странице» не рисуем). */
export function highlightText(text: string, needles: Needle[]): { segments: Segment[]; found: Set<string> } {
  const found = new Set<string>();
  if (!text) return { segments: [], found };
  const { s: hay, map } = normalize(text);
  const ranges: Range[] = [];
  // Индекс кусочков для нечёткого поиска строим только если точный не справился.
  let idx: GramIndex | null = null;
  for (const nd of needles) {
    if (!nd.text) continue;
    const needle = normalize(nd.text).s;
    let r = locate(hay, needle);
    if (!r && hay.length <= FUZZY_MAX_HAY) {
      idx = idx ?? gramIndex(hay);
      r = fuzzyLocate(idx, hay.length, needle);
    }
    if (!r) continue;
    const start = map[r[0]];
    let end = map[r[1] - 1] + 1;
    // Точку или кавычку сразу за цитатой тоже подсвечиваем, чтобы фраза не обрывалась.
    for (let k = 0; k < 2 && end < text.length && TRAIL.test(text[end]); k++) end++;
    ranges.push({ start, end, id: nd.id, strong: !!nd.strong });
    found.add(nd.id);
  }
  return { segments: cut(text, ranges), found };
}

/** Подсветить все вхождения запроса в коротком отрывке (результаты поиска по книге). */
export function markQuery(snippet: string, q: string): Segment[] {
  // Регистр понижаем по одной кодовой единице, чтобы индексы копии совпадали с отрывком.
  const needle = q.trim().split("").map(lower1).join("");
  if (!needle) return cut(snippet, []);
  const low = snippet.split("").map(lower1).join("");
  const ranges: Range[] = [];
  for (let i = low.indexOf(needle); i >= 0; i = low.indexOf(needle, i + needle.length)) {
    ranges.push({ start: i, end: i + needle.length, id: "q", strong: false });
  }
  return cut(snippet, ranges);
}
