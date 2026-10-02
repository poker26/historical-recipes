// Данные карточки вида и родового хаба: сырой монограф (все факты с книгой, годом и
// страницей), читательский очерк Слоя 2, рецепты, пары, «состав → действие»,
// наблюдения iNaturalist. Сырой монограф бывает больше 2 МБ (крапива, чистотел),
// а кэш данных Next такие ответы не хранит. Поэтому страница кэширует не сырой
// ответ, а уже собранную модель карточки: сгруппированную, без дублей и с потолками
// на длинные списки. Модель в десятки раз меньше и не пересобирается на каждый запрос.

import { cache } from "react";
import { unstable_cache } from "next/cache";
import { existsSync } from "fs";
import { join } from "path";
import { API, ApiError, getJson, getJsonStrict, isUuid, parsePlantSlug, qs } from "./api";

// ——— сырые формы ответов бэкенда ———

export type SafetyInfo = {
  level?: number | null;
  label?: string | null;
  edible_parts?: string[] | null;
  dangerous_parts?: string[] | null;
  deadly_twin?: string | null;
  rationale?: string | null;
  note?: string | null;
  dangerous_members?: { id: string; name: string; level?: number | null; deadly_twin?: string | null }[] | null;
};

type FactBase = {
  id: string;
  original_text?: string | null;
  source?: string | null;
  book_id?: string | null;
  year?: number | null;
  source_page?: number | null;
};
export type RawUse = FactBase & {
  part?: string | null; action?: string | null; action_system?: string | null; indications?: string | null;
  indication_ids?: string[] | null; preparation?: string | null; dosage?: string | null; contraindications?: string | null;
};
export type RawCompound = FactBase & {
  compound?: string | null; compound_group?: string | null; compound_id?: string | null; part?: string | null; notes?: string | null;
};
export type RawHarvest = FactBase & { part?: string | null; season?: string | null; method?: string | null };
export type RawHabitat = FactBase & { region?: string | null; biotope?: string | null; status?: string | null };
export type RawToxicity = FactBase & {
  toxic_parts?: string[] | string | null; symptoms?: string | null; antidote?: string | null; severity?: string | null;
};
export type RawCulinary = FactBase & {
  part?: string | null; edibility?: string | null; preparation?: string | null; use?: string | null; season?: string | null; caution?: string | null;
};
export type RawMention = { id: string; book?: string | null; book_id?: string | null; year?: number | null; original_name?: string | null; page_number?: number | null };
export type RawRecipeLink = { id: string; name?: string | null; category?: string | null; book?: string | null; year?: number | null; book_id?: string | null; kind?: string | null };
export type RawOil = { id: string; name?: string | null; name_latin?: string | null; part?: string | null; extraction?: string | null; uses_count?: number | null };

export type RawPlant = {
  id: string;
  name: string;
  name_latin?: string | null;
  name_modern?: string | null;
  names_historical?: string[] | null;
  rank?: string | null;
  parent?: { id: string; name: string; name_latin?: string | null } | null;
  family?: string | null;
  family_latin?: string | null;
  description?: string | null;
  parts_used?: string[] | null;
  is_toxic?: boolean | null;
  safety?: SafetyInfo | null;
  kingdom?: string | null;
  photo_url?: string | null;
  photo_attribution?: string | null;
  photo_license?: string | null;
  photo_source?: string | null;
  inat_taxon_id?: number | null;
  medicinal_uses?: RawUse[] | null;
  compounds?: RawCompound[] | null;
  harvests?: RawHarvest[] | null;
  habitats?: RawHabitat[] | null;
  toxicities?: RawToxicity[] | null;
  culinary_uses?: RawCulinary[] | null;
  mentions?: RawMention[] | null;
  recipes?: RawRecipeLink[] | null;
  essential_oils?: RawOil[] | null;
};

export type RawGenusHub = {
  id: string;
  name: string;
  name_latin?: string | null;
  rank: "genus";
  kingdom?: string | null;
  member_count?: number | null;
  note?: string | null;
  safety?: SafetyInfo | null;
  members?: { id: string; name: string; name_latin?: string | null }[] | null;
  uses?: { action: string; n_species: number; species?: string[] | null; indications?: string[] | null; sources?: string[] | null }[] | null;
  compounds?: { compound: string; n_species: number; species?: string[] | null }[] | null;
  recipes?: { id: string; name?: string | null; category?: string | null; book?: string | null; year?: number | null }[] | null;
};

/** Цитата очерка: объект {text, source} или просто строка. */
export type FieldQuote = { text?: string | null; source?: string | null; page?: number | null } | string | null;

/** Читательский очерк (`?view=field`): проверенный Слой 2 или вычисленный вариант. */
export type FieldView = {
  id?: string;
  name?: string;
  name_latin?: string | null;
  name_modern?: string | null;
  kingdom?: string | null;
  photo_url?: string | null;
  photo_attribution?: string | null;
  photo_license?: string | null;
  photo_source?: string | null;
  verdict?: string | null;
  description?: string | null;
  lead_fact?: FieldQuote;
  fun_fact?: FieldQuote;
  safety?: SafetyInfo | null;
  is_toxic?: boolean | null;
  cautions?: {
    text?: string | null;
    toxic_parts?: string[] | null;
    contraindications?: string[] | null;
    symptoms?: string | null;
    antidote?: string | null;
    source?: string | null;
    page?: number | null;
  } | null;
  recipes_total?: number | null;
  harvest?: { parts?: string[] | null; seasons?: string[] | null; where?: string[] | null } | null;
  habitat?: { biotopes?: { key: string; group?: string | null }[] | null; regions?: string[] | null; summary?: string | null } | null;
  care_sections?: string[] | null;
  care?: { field?: string | null; title?: string | null; voices?: { page?: number | null; text?: string | null; season?: string | null; source?: string | null }[] | null }[] | null;
  care_summary?: string | null;
  origin?: string | null;
  rank?: string | null;
};

export type PlantRecipeItem = {
  id: string; name: string; category?: string | null; kind?: string | null; n_ingredients?: number | null;
  book?: string | null; year?: number | null; step_by_step?: boolean | null; text?: string | null; truncated?: boolean | null;
};
export type PlantRecipes = { plant_id: string; kinds?: string[] | null; items?: PlantRecipeItem[] | null };

export type Pairing = {
  plant: { id: string; name: string; name_latin?: string | null; photo_url?: string | null; safety_level?: number | null; rank?: string | null };
  support: number;
  lift?: number | null;
  specific?: boolean | null;
  recipes?: { id: string; name: string; book?: string | null; year?: number | null }[] | null;
};
export type Pairings = { plant_id: string; canon_id?: string | null; categories?: string[] | null; items?: Pairing[] | null };

export type CompoundInsight = {
  compound: { key: string; name: string };
  action: { name: string };
  support: number;
  lift?: number | null;
  p_value?: number | null;
  strength?: string | null;
  n_plants_with_compound?: number | null;
};
export type CompoundInsights = { plant_id: string; disclaimer?: string | null; insights?: CompoundInsight[] | null };

export type Observation = {
  id: number; observed_on?: string | null; uri?: string | null; observer?: string | null; quality_grade?: string | null;
  photo_url?: string | null; photo_attribution?: string | null; photo_license?: string | null;
};
export type Observations = {
  taxon_id?: number | null; scope?: string | null; total_count?: number | null; seasonality?: Record<string, number> | null;
  count?: number | null; observations?: Observation[] | null; error?: string | null;
};

export type PlantSummary = {
  id: string; name: string; name_latin?: string | null; photo_url?: string | null; photo_attribution?: string | null; uses_count?: number | null;
  rank?: string | null; safety_level?: number | null; is_toxic?: boolean | null; kingdom?: string | null;
};

// ——— собранная модель карточки ———

/** Цитата или хотя бы ссылка на книгу, если дословного текста нет. */
export type Cite = { text: string | null; book: string | null; bookId: string | null; year: number | null; page: number | null };
/** Запись о сборе, месте, еде или ядовитости: подписи полей и цитата. */
export type Fact = { meta: { label: string; value: string }[]; cite: Cite };
export type FactSet = { items: Fact[]; total: number };

export type UseGroup = {
  actions: string[];         // пусто → «прочее»; несколько, когда у записей одни и те же цитаты
  quotes: Cite[];            // уже с потолком
  quotesTotal: number;       // все уникальные цитаты группы
  books: number;
  restBooks: number;         // книг среди цитат после первых двух
  indications: string[];
  parts: string[];
  preparations: string[];
};

export type CompoundItem = { name: string; compoundId: string | null; parts: string[]; sources: { book: string; bookId: string | null; year: number | null }[]; sourcesTotal: number };
export type CompoundGroup = { group: string; rows: number; items: CompoundItem[]; itemsTotal: number };
export type MentionBook = { book: string; bookId: string | null; year: number | null; names: string[]; pages: number[] };
export type SourceBook = { book: string; bookId: string | null; year: number | null; quotes: number; compounds: number; pages: number[]; pagesTotal: number };
export type Photo = { url: string; attribution: string | null; license: string | null; source: string | null };

export type SpeciesCard = {
  kind: "species";
  id: string;
  name: string;
  latin: string | null;
  nameModern: string | null;
  family: string | null;
  familyLatin: string | null;
  kingdom: string | null;
  description: string | null;
  isToxic: boolean;
  safety: SafetyInfo | null;
  parent: { id: string; name: string; latin: string | null } | null;
  photo: Photo | null;
  inatTaxonId: number | null;
  names: string[];
  namesTotal: number;
  usesTotal: number;
  useQuotesTotal: number;
  useBooks: number;
  useGroups: UseGroup[];
  useGroupsTotal: number;
  toxicities: FactSet;
  compoundGroups: CompoundGroup[];
  compoundGroupsTotal: number;
  compoundsTotal: number;
  harvests: FactSet;
  habitats: FactSet;
  culinary: FactSet;
  mentions: MentionBook[];
  mentionBooksTotal: number;
  sources: SourceBook[];
  sourcesTotal: number;
  bookCount: number;
  yearMin: number | null;
  yearMax: number | null;
  recipesRawTotal: number;
  firstQuote: string | null;
  oils: RawOil[];
  /** Название книги (в нижнем регистре) → id: чтобы цитаты очерка, где есть только
   *  название, тоже вели в библиотеку. */
  bookIds: Record<string, string>;
};

export type GenusCard = {
  kind: "genus";
  id: string;
  name: string;
  latin: string | null;
  kingdom: string | null;
  memberCount: number;
  note: string | null;
  safety: SafetyInfo | null;
  members: { id: string; name: string; latin: string | null }[];
  uses: { action: string; nSpecies: number; species: string[]; indications: string[]; sources: string[] }[];
  compounds: { compound: string; nSpecies: number; species: string[] }[];
  recipes: { id: string; name: string; category: string | null; book: string | null; year: number | null }[];
  recipesTotal: number;
};

export type PlantCard = SpeciesCard | GenusCard;

// ——— потолки: страница должна оставаться лёгкой даже у крапивы (877 цитат) ———
const TOP_GROUPS = 12;           // действия, раскрытые целиком
const TOP_GROUP_QUOTES = 6;      // цитат на такое действие: 2 открыты, 4 под «ещё»
const REST_GROUPS = 30;          // действия из хвоста: имя, счёт и показания
const FACTS_CAP = 10;
const COMPOUND_GROUPS_CAP = 16;
const COMPOUND_ITEMS_CAP = 8;
const NAMES_CAP = 150;
const MENTION_BOOKS_CAP = 30;
const SOURCE_BOOKS_CAP = 60;
const QUOTE_MAX = 900;

// ——— текстовые помощники ———

const clean = (s: string | null | undefined): string => (s ?? "").replace(/\s+/g, " ").trim();
const keyOf = (s: string | null | undefined): string => clean(s).toLowerCase().replace(/[.;:,!]+$/g, "").replace(/ё/g, "е");

function clip(s: string, max: number): string {
  if (s.length <= max) return s;
  const cut = s.slice(0, max);
  return cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 40)).trim() + "…";
}

/** Разрезать перечень по запятым и точкам с запятой, не трогая скобки:
 *  «эпилептиформные состояния (в т. ч. алкогольный), судороги» → 2 пункта. */
export function splitList(s: string | null | undefined): string[] {
  const src = s ?? "";
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of src) {
    if (ch === "(" || ch === "[") depth++;
    if ((ch === ")" || ch === "]") && depth > 0) depth--;
    if ((ch === "," || ch === ";" || ch === "\n") && depth === 0) {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out.map((x) => clean(x).replace(/^[-–—•·]+\s*/, "").replace(/[.;:]+$/, "").trim()).filter((x) => x.length >= 2 && x.length <= 90);
}

/** Уникальные значения по ключу без регистра, в порядке первого появления. */
// Части растения в записях книг пишутся по-разному: «лист», «листья», «листьев».
// Для показа сводим их к одной форме, иначе строка «Части растения» состоит из повторов.
const PART_CANON: [RegExp, string][] = [
  [/^лист/, "листья"],
  [/^соцвети/, "соцветия"],
  [/^цвет(ы|ки|ков|ок|ах|ами|)$|^цветк/, "цветки"],
  [/^сем(я|ен|ян)/, "семена"],
  [/^корн(и|ей|ях|ями|ям)$|^корень$|^корешк/, "корни"],
  [/^корневищ/, "корневища"],
  [/^трав(а|у|ы|е)$/, "трава"],
  [/^плод(ы|ов|ах|)$/, "плоды"],
  [/^кор(а|у|ы|е)$/, "кора"],
  [/^стебл|^стебел/, "стебли"],
  [/^надземн/, "надземная часть"],
  [/^почк/, "почки"],
  [/^ягод/, "ягоды"],
  [/^побег/, "побеги"],
  [/^клубн/, "клубни"],
  [/^лукови/, "луковицы"],
  [/^сок$/, "сок"],
  [/^вс[её] растение$/, "всё растение"],
];
// Способ приготовления или вещество в поле «часть» оказываются по ошибке распознавания.
const PART_DROP = /^(сырь[её]|сырь[её] растения|растение|экстракт|порошок|настой|настойка|отвар|препараты|хлорофилл|масло)$/;
// «свежие листья», «в семенах»: состояние сырья и предлог к части растения не относятся.
const PART_PREFIX = /^(в\s+)?((свеж|молод|сух|высушенн|сушен|зел[её]н|измельченн)\S*\s+)?/;

/** Части из поля записи: «корневища и корни, листьев» → ["корневища", "корни", "листья"]. */
export function canonParts(s: string | null | undefined): string[] {
  const out: string[] = [];
  for (const raw of splitList(s)) {
    for (const piece of raw.split(/\s+и\s+/)) {
      const p = piece.trim().toLowerCase().replace(/ё/g, "е").replace(PART_PREFIX, "");
      if (!p || PART_DROP.test(p)) continue;
      const hit = PART_CANON.find(([rx]) => rx.test(p));
      out.push(hit ? hit[1] : piece.trim());
    }
  }
  return out;
}

// Способы приготовления: делим на отдельные слова, множественное число сводим к
// единственному, путь приёма («внутрь», «наружно») сюда не относится.
const PREP_CANON: [RegExp, string][] = [
  [/^настои$/, "настой"],
  [/^отвары$/, "отвар"],
  [/^настойки$/, "настойка"],
  [/^сборы$/, "сбор"],
  [/^компрессы$/, "компресс"],
  [/^примочки$/, "примочка"],
  [/^чаи$/, "чай"],
  [/^ванны$/, "ванна"],
  [/^мази$/, "мазь"],
  [/^порошки$/, "порошок"],
  [/^(свеж(ая|ий|ие|ее|ем виде)|в свежем виде)$/, "в свежем виде"],
];
const PREP_DROP = /^(внутрь|наружно|препараты|сырь[её])$/;

export function canonPreps(s: string | null | undefined): string[] {
  const out: string[] = [];
  for (const raw of splitList(s)) {
    const p = raw.trim();
    const low = p.toLowerCase();
    if (!p || p.length > 60 || PREP_DROP.test(low)) continue;
    const hit = PREP_CANON.find(([rx]) => rx.test(low));
    out.push(hit ? hit[1] : p);
  }
  return out;
}

/** Прямые кавычки из распознанного текста показываем ёлочками: «стоячее дыхание». */
export function ruQuotes(s: string): string {
  return s.replace(/"([^"]+)"/g, "«$1»");
}

/** Подпись рецепта под названием: вид, книга, год. Категория «другое» ничего не сообщает,
 *  год не повторяем, если он уже стоит в названии книги. */
export function recipeMeta(r: { category?: string | null; book?: string | null; year?: number | null }): string {
  const cat = r.category && !/^(другое|прочее)$/i.test(r.category.trim()) ? r.category : null;
  const book = r.book ? `«${r.book}»` : null;
  const year = r.year && !(r.book ?? "").includes(String(r.year)) ? r.year : null;
  return [cat, book, year].filter(Boolean).join(", ");
}

class Uniq {
  private seen = new Map<string, string>();
  add(v: string | null | undefined) {
    const t = clean(v);
    if (!t) return;
    const k = keyOf(t);
    if (k && !this.seen.has(k)) this.seen.set(k, t);
  }
  list(max = Infinity): string[] {
    return Array.from(this.seen.values()).slice(0, max);
  }
  addAll(other: Uniq) {
    for (const v of other.list()) this.add(v);
  }
  get size() {
    return this.seen.size;
  }
}

function citeOf(f: FactBase): Cite {
  const text = clean(f.original_text);
  return {
    text: text ? clip(text, QUOTE_MAX) : null,
    book: clean(f.source) || null,
    bookId: f.book_id || null,
    year: f.year ?? null,
    page: f.source_page ?? null,
  };
}

const bookKey = (c: { bookId: string | null; book: string | null }) => c.bookId || keyOf(c.book) || "";

/** Порядок цитат: сначала те, у которых есть страница скана и год, и не обрывки. */
function citeScore(c: Cite): number {
  const len = c.text?.length ?? 0;
  return (c.page ? 2 : 0) + (c.year ? 1 : 0) + (len >= 60 && len <= 600 ? 1 : 0);
}
function sortCites<T>(list: T[], get: (x: T) => Cite): T[] {
  return list
    .map((x, i) => ({ x, i, s: citeScore(get(x)) }))
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map((r) => r.x);
}

// ——— сборка видовой карточки ———

function buildFacts<T extends FactBase>(rows: T[] | null | undefined, meta: (r: T) => { label: string; value: string }[]): FactSet {
  const seen = new Set<string>();
  const facts: Fact[] = [];
  for (const r of rows ?? []) {
    const cite = citeOf(r);
    const m = meta(r).filter((x) => x.value);
    if (!cite.text && !m.length) continue;
    const k = cite.text ? keyOf(cite.text) : "meta:" + m.map((x) => keyOf(x.value)).join("|");
    if (seen.has(k)) continue;
    seen.add(k);
    facts.push({ meta: m, cite });
  }
  const sorted = sortCites(facts, (f) => f.cite);
  return { items: sorted.slice(0, FACTS_CAP), total: sorted.length };
}

function toxicParts(v: string[] | string | null | undefined): string {
  if (!v) return "";
  return (Array.isArray(v) ? v : [v]).map(clean).filter(Boolean).join(", ");
}

function buildUseGroups(uses: RawUse[]): { groups: UseGroup[]; total: number; quotes: number; books: number } {
  type Acc = { action: string; label: string; quotes: Map<string, Cite>; books: Set<string>; ind: Uniq; parts: Uniq; preps: Uniq; n: number };
  const acc = new Map<string, Acc>();
  const allQuotes = new Set<string>();
  const allBooks = new Set<string>();
  for (const u of uses) {
    const cite = citeOf(u);
    const bk = bookKey(cite);
    if (bk) allBooks.add(bk);
    if (cite.text) allQuotes.add(keyOf(cite.text));
    const acts = splitList(u.action).map((a) => a.toLowerCase()).filter((a) => a.length <= 60);
    const keys = acts.length ? Array.from(new Set(acts)) : [""];
    for (const a of keys) {
      const k = keyOf(a);
      let g = acc.get(k);
      if (!g) {
        g = { action: k, label: a, quotes: new Map(), books: new Set(), ind: new Uniq(), parts: new Uniq(), preps: new Uniq(), n: 0 };
        acc.set(k, g);
      }
      g.n++;
      if (cite.text) {
        const qk = keyOf(cite.text);
        if (!g.quotes.has(qk)) g.quotes.set(qk, cite);
      }
      if (bk) g.books.add(bk);
      for (const i of splitList(u.indications)) g.ind.add(i);
      for (const p of canonParts(u.part)) g.parts.add(p);
      for (const p of canonPreps(u.preparation)) g.preps.add(p);
    }
  }
  const sorted = Array.from(acc.values()).sort((a, b) => {
    if (!a.action !== !b.action) return a.action ? -1 : 1; // «прочее» всегда в конце
    return b.quotes.size - a.quotes.size || b.n - a.n || a.label.localeCompare(b.label, "ru");
  });
  // Запись с несколькими действиями («антианемический, витаминизирующий») даёт группы
  // с одними и теми же цитатами. Такие группы сливаем в одну с несколькими действиями,
  // чтобы одна цитата не повторялась подряд под разными заголовками.
  const all: (Acc & { labels: string[] })[] = [];
  const bySig = new Map<string, Acc & { labels: string[] }>();
  for (const g of sorted) {
    const sig = g.action && g.quotes.size ? Array.from(g.quotes.keys()).sort().join(" |~| ") : "";
    const prev = sig ? bySig.get(sig) : undefined;
    if (prev) {
      prev.labels.push(g.label);
      prev.n += g.n;
      g.books.forEach((b) => prev.books.add(b));
      prev.ind.addAll(g.ind);
      prev.parts.addAll(g.parts);
      prev.preps.addAll(g.preps);
      continue;
    }
    const m = { ...g, labels: g.action ? [g.label] : [] };
    if (sig) bySig.set(sig, m);
    all.push(m);
  }
  const groups: UseGroup[] = all.slice(0, TOP_GROUPS + REST_GROUPS).map((g, idx) => {
    const quotes = sortCites(Array.from(g.quotes.values()), (c) => c);
    const rest = quotes.slice(2);
    const restBooks = new Set(rest.map(bookKey).filter(Boolean)).size;
    return {
      actions: g.labels,
      quotes: quotes.slice(0, idx < TOP_GROUPS ? TOP_GROUP_QUOTES : 0),
      quotesTotal: quotes.length,
      books: g.books.size,
      restBooks,
      indications: g.ind.list(idx < TOP_GROUPS ? 12 : 4),
      parts: idx < TOP_GROUPS ? g.parts.list(10) : [],
      preparations: idx < TOP_GROUPS ? g.preps.list(6) : [],
    };
  });
  return { groups, total: all.length, quotes: allQuotes.size, books: allBooks.size };
}

function buildCompounds(rows: RawCompound[]): { groups: CompoundGroup[]; total: number; items: number } {
  type Item = { name: string; compoundId: string | null; parts: Uniq; sources: Map<string, { book: string; bookId: string | null; year: number | null }> };
  type Grp = { label: string; rows: number; items: Map<string, Item> };
  const groups = new Map<string, Grp>();
  for (const r of rows) {
    const name = clean(r.compound);
    if (!name) continue;
    const gk = keyOf(r.compound_group);
    let g = groups.get(gk);
    if (!g) {
      g = { label: clean(r.compound_group), rows: 0, items: new Map() };
      groups.set(gk, g);
    }
    g.rows++;
    const ik = keyOf(name);
    let it = g.items.get(ik);
    if (!it) {
      it = { name, compoundId: r.compound_id || null, parts: new Uniq(), sources: new Map() };
      g.items.set(ik, it);
    }
    if (!it.compoundId && r.compound_id) it.compoundId = r.compound_id;
    for (const p of canonParts(r.part)) it.parts.add(p);
    const book = clean(r.source);
    const sk = r.book_id || keyOf(book);
    if (sk && !it.sources.has(sk)) it.sources.set(sk, { book: book || "книга", bookId: r.book_id || null, year: r.year ?? null });
  }
  const sorted = Array.from(groups.entries()).sort(([ak, a], [bk, b]) => {
    if (!ak !== !bk) return ak ? -1 : 1; // без группы — в конце
    return b.rows - a.rows || a.label.localeCompare(b.label, "ru");
  });
  const items = sorted.reduce((n, [, g]) => n + g.items.size, 0);
  const out: CompoundGroup[] = sorted.slice(0, COMPOUND_GROUPS_CAP).map(([, g]) => {
    const list = Array.from(g.items.values()).sort((a, b) => b.sources.size - a.sources.size);
    return {
      group: g.label,
      rows: g.rows,
      itemsTotal: list.length,
      items: list.slice(0, COMPOUND_ITEMS_CAP).map((it) => {
        const sources = Array.from(it.sources.values());
        return { name: it.name, compoundId: it.compoundId, parts: it.parts.list(4), sources: sources.slice(0, 1), sourcesTotal: sources.length };
      }),
    };
  });
  return { groups: out, total: sorted.length, items };
}

function buildSpecies(raw: RawPlant): SpeciesCard {
  const uses = raw.medicinal_uses ?? [];
  const compounds = raw.compounds ?? [];
  const harvests = raw.harvests ?? [];
  const habitats = raw.habitats ?? [];
  const toxicities = raw.toxicities ?? [];
  const culinary = raw.culinary_uses ?? [];
  const mentions = raw.mentions ?? [];

  // Источники: книги из всех слоёв фактов, со счётом цитат, записей состава и страницами.
  type Src = { book: string; bookId: string | null; year: number | null; quotes: number; compounds: number; pages: Set<number> };
  const src = new Map<string, Src>();
  const bookIds: Record<string, string> = {};
  let yearMin: number | null = null;
  let yearMax: number | null = null;
  const touch = (f: FactBase, isCompound: boolean) => {
    const book = clean(f.source);
    const key = f.book_id || keyOf(book);
    if (!key) return;
    let s = src.get(key);
    if (!s) {
      s = { book: book || "Книга без названия", bookId: f.book_id || null, year: f.year ?? null, quotes: 0, compounds: 0, pages: new Set() };
      src.set(key, s);
    }
    if (!s.year && f.year) s.year = f.year;
    if (isCompound) s.compounds++;
    else if (clean(f.original_text)) s.quotes++;
    if (f.source_page) s.pages.add(f.source_page);
    if (book && f.book_id) bookIds[keyOf(book)] = f.book_id;
    if (f.year) {
      yearMin = yearMin === null ? f.year : Math.min(yearMin, f.year);
      yearMax = yearMax === null ? f.year : Math.max(yearMax, f.year);
    }
  };
  for (const f of uses) touch(f, false);
  for (const f of harvests) touch(f, false);
  for (const f of habitats) touch(f, false);
  for (const f of toxicities) touch(f, false);
  for (const f of culinary) touch(f, false);
  for (const f of compounds) touch(f, true);
  const sources = Array.from(src.values())
    .sort((a, b) => b.quotes + b.compounds - (a.quotes + a.compounds) || a.book.localeCompare(b.book, "ru"))
    .map((s) => {
      const pages = Array.from(s.pages).sort((a, b) => a - b);
      return { book: s.book, bookId: s.bookId, year: s.year, quotes: s.quotes, compounds: s.compounds, pages: pages.slice(0, 8), pagesTotal: pages.length };
    });

  // Упоминания: одна строка на книгу — под какими именами и на каких страницах.
  type Men = { book: string; bookId: string | null; year: number | null; names: Uniq; pages: Set<number> };
  const men = new Map<string, Men>();
  for (const m of mentions) {
    const book = clean(m.book);
    const key = m.book_id || keyOf(book);
    if (!key) continue;
    let e = men.get(key);
    if (!e) {
      e = { book: book || "Книга без названия", bookId: m.book_id || null, year: m.year ?? null, names: new Uniq(), pages: new Set() };
      men.set(key, e);
    }
    e.names.add(m.original_name);
    if (m.page_number) e.pages.add(m.page_number);
    if (book && m.book_id && !bookIds[keyOf(book)]) bookIds[keyOf(book)] = m.book_id;
  }
  const mentionBooks = Array.from(men.values())
    .map((e) => ({ book: e.book, bookId: e.bookId, year: e.year, names: e.names.list(4), pages: Array.from(e.pages).sort((a, b) => a - b).slice(0, 6) }))
    .sort((a, b) => (b.pages.length ? 1 : 0) - (a.pages.length ? 1 : 0) || (a.year ?? 9999) - (b.year ?? 9999) || a.book.localeCompare(b.book, "ru"));

  // Имена: без дублей по регистру и без основного и современного имени.
  const names = new Uniq();
  const skip = new Set([keyOf(raw.name), keyOf(raw.name_modern)]);
  for (const n of raw.names_historical ?? []) {
    const t = clean(n);
    if (t && !skip.has(keyOf(t)) && t.length <= 60) names.add(t);
  }

  const ug = buildUseGroups(uses);
  const cg = buildCompounds(compounds);
  const firstQuote = ug.groups.find((g) => g.quotes[0]?.text)?.quotes[0]?.text ?? null;

  return {
    kind: "species",
    id: raw.id,
    name: clean(raw.name) || "Без названия",
    latin: clean(raw.name_latin) || null,
    nameModern: clean(raw.name_modern) || null,
    family: clean(raw.family) || null,
    familyLatin: clean(raw.family_latin) || null,
    kingdom: raw.kingdom ?? null,
    description: raw.description?.trim() || null,
    isToxic: !!raw.is_toxic,
    safety: raw.safety ?? null,
    parent: raw.parent ? { id: raw.parent.id, name: raw.parent.name, latin: raw.parent.name_latin ?? null } : null,
    photo: raw.photo_url
      ? { url: raw.photo_url, attribution: raw.photo_attribution ?? null, license: raw.photo_license ?? null, source: raw.photo_source ?? null }
      : null,
    inatTaxonId: raw.inat_taxon_id ?? null,
    names: names.list(NAMES_CAP),
    namesTotal: names.size,
    usesTotal: uses.length,
    useQuotesTotal: ug.quotes,
    useBooks: ug.books,
    useGroups: ug.groups,
    useGroupsTotal: ug.total,
    toxicities: buildFacts(toxicities, (t) => [
      { label: "Ядовитые части", value: toxicParts(t.toxic_parts) },
      { label: "Признаки отравления", value: clean(t.symptoms) },
      { label: "Что советовали книги", value: clean(t.antidote) },
      { label: "Тяжесть", value: clean(t.severity) },
    ]),
    compoundGroups: cg.groups,
    compoundGroupsTotal: cg.total,
    compoundsTotal: cg.items,
    harvests: buildFacts(harvests, (h) => [
      { label: "Часть", value: canonParts(h.part).join(", ") || clean(h.part) },
      { label: "Когда", value: clean(h.season) },
      { label: "Как", value: clean(h.method) },
    ]),
    habitats: buildFacts(habitats, (h) => [
      { label: "Где", value: clean(h.region) },
      { label: "Места", value: clean(h.biotope) },
      { label: "Встречается", value: clean(h.status) },
    ]),
    culinary: buildFacts(culinary, (c) => [
      { label: "Съедобность", value: clean(c.edibility) },
      { label: "Часть", value: canonParts(c.part).join(", ") || clean(c.part) },
      { label: "Как едят", value: clean(c.use) },
      { label: "Как готовят", value: clean(c.preparation) },
      { label: "Когда", value: clean(c.season) },
      { label: "Осторожно", value: clean(c.caution) },
    ]),
    mentions: mentionBooks.slice(0, MENTION_BOOKS_CAP),
    mentionBooksTotal: mentionBooks.length,
    sources: sources.slice(0, SOURCE_BOOKS_CAP),
    sourcesTotal: sources.length,
    bookCount: sources.length,
    yearMin,
    yearMax,
    recipesRawTotal: (raw.recipes ?? []).length,
    firstQuote,
    oils: (raw.essential_oils ?? []).slice(0, 20),
    bookIds,
  };
}

function buildGenus(raw: RawGenusHub): GenusCard {
  const members = (raw.members ?? []).map((m) => ({ id: m.id, name: clean(m.name), latin: clean(m.name_latin) || null }));
  const recipes = raw.recipes ?? [];
  return {
    kind: "genus",
    id: raw.id,
    name: clean(raw.name) || "Род",
    latin: clean(raw.name_latin) || null,
    kingdom: raw.kingdom ?? null,
    memberCount: raw.member_count ?? members.length,
    note: clean(raw.note) || null,
    safety: raw.safety ?? null,
    members: members.slice(0, 240),
    uses: (raw.uses ?? []).slice(0, 40).map((u) => {
      const ind = new Uniq();
      for (const s of u.indications ?? []) for (const i of splitList(s)) ind.add(i);
      return { action: clean(u.action), nSpecies: u.n_species, species: (u.species ?? []).slice(0, 8), indications: ind.list(8), sources: (u.sources ?? []).slice(0, 5) };
    }),
    compounds: (raw.compounds ?? []).slice(0, 40).map((c) => ({ compound: clean(c.compound), nSpecies: c.n_species, species: (c.species ?? []).slice(0, 8) })),
    recipes: recipes.slice(0, 12).map((r) => ({ id: r.id, name: clean(r.name) || "Рецепт", category: r.category ?? null, book: r.book ?? null, year: r.year ?? null })),
    recipesTotal: recipes.length,
  };
}

// ——— загрузка с кэшем ———

const CARD_VERSION = "card-v7";
/** Срок кэша данных карточки и её блоков, секунды. */
const CARD_TTL = 21600;
/** Метка кэша карточки: по ней /api/revalidate сбрасывает все ответы бэкенда об этой
 *  карточке сразу после правки данных, а не через CARD_TTL. */
export const plantTag = (id: string) => `plant:${id.toLowerCase()}`;

class PlantMissing extends Error {}
class BackendDown extends Error {}

async function fetchRawPlant(id: string): Promise<RawPlant | RawGenusHub> {
  let res: Response;
  try {
    res = await fetch(`${API}/plants/${encodeURIComponent(id)}`, { cache: "no-store", signal: AbortSignal.timeout(25000) });
  } catch {
    throw new BackendDown(id);
  }
  if (res.status === 404 || res.status === 422) throw new PlantMissing(id);
  if (!res.ok) throw new BackendDown(`${res.status}`);
  return (await res.json()) as RawPlant | RawGenusHub;
}

async function buildCard(id: string): Promise<PlantCard> {
  const raw = await fetchRawPlant(id);
  return raw.rank === "genus" ? buildGenus(raw as RawGenusHub) : buildSpecies(raw as RawPlant);
}

export type CardResult = { state: "ok"; card: PlantCard } | { state: "missing" } | { state: "error" };

/** Модель карточки: собирается из сырого монографа и живёт в кэше данных шесть часов.
 *  Сырой монограф у богатых видов весит мегабайты и собирается бэкендом секунды (замер
 *  28.09: зверобой 6,3 с, 1,7 МБ), а обход роботов при сроке в час пересобирал карточки
 *  снова и снова. Данные карточек меняются редко, прогонами обработки. */
export const getPlantCard = cache(async (id: string): Promise<CardResult> => {
  const load = () => buildCard(id);
  try {
    let card: PlantCard;
    try {
      card = await unstable_cache(load, [CARD_VERSION, id], { revalidate: CARD_TTL, tags: [plantTag(id)] })();
    } catch (e) {
      if (e instanceof PlantMissing || e instanceof BackendDown) throw e;
      card = await load(); // кэш недоступен: собираем без него
    }
    return { state: "ok", card };
  } catch (e) {
    return e instanceof PlantMissing ? { state: "missing" } : { state: "error" };
  }
});

export type ResolveResult = { state: "ok"; id: string } | { state: "missing" } | { state: "error" };

/** Адрес карточки → id: UUID как есть, у человеческого адреса хвост из 6 знаков id. */
export const resolvePlantParam = cache(async (param: string): Promise<ResolveResult> => {
  let p = param;
  try {
    p = decodeURIComponent(param);
  } catch {
    /* оставляем как есть */
  }
  const { uuid, tail } = parsePlantSlug(p.trim());
  if (uuid && isUuid(uuid)) return { state: "ok", id: uuid.toLowerCase() };
  if (!tail) return { state: "missing" };
  try {
    const r = await getJsonStrict<{ id: string; ambiguous?: boolean }>(`/plants/resolve?tail=${tail}`, 3600);
    return r?.id ? { state: "ok", id: r.id } : { state: "missing" };
  } catch (e) {
    return e instanceof ApiError && (e.status === 404 || e.status === 422) ? { state: "missing" } : { state: "error" };
  }
});

/** Куда слили карточку с этим id (журнал чистки идентичности); null, если некуда. */
export const resolveMerged = cache(async (id: string): Promise<string | null> => {
  const r = await getJson<{ id: string; merged?: boolean }>(`/plants/resolve?tail=${id.replace(/-/g, "").slice(0, 32)}`, 3600);
  return r?.merged ? r.id : null;
});

export const getFieldView = cache((id: string) =>
  getJson<FieldView>(`/plants/${encodeURIComponent(id)}?view=field`, CARD_TTL, 10000, [plantTag(id)]));

/** Очерк со статусом: для страницы-перенаправления нужно отличать «нет вида» от сбоя. */
export const getFieldViewStrict = cache(async (id: string): Promise<{ state: "ok"; field: FieldView } | { state: "missing" } | { state: "error" }> => {
  try {
    const field = await getJsonStrict<FieldView>(`/plants/${encodeURIComponent(id)}?view=field`, CARD_TTL, 10000, [plantTag(id)]);
    return { state: "ok", field };
  } catch (e) {
    return e instanceof ApiError && (e.status === 404 || e.status === 422) ? { state: "missing" } : { state: "error" };
  }
});

export const RECIPE_KINDS = ["medicinal", "food", "cosmetic", "other"] as const;
export type RecipeKind = (typeof RECIPE_KINDS)[number];
export const normKind = (k?: string | string[] | null): RecipeKind | null => {
  const v = Array.isArray(k) ? k[0] : k;
  return v && (RECIPE_KINDS as readonly string[]).includes(v) ? (v as RecipeKind) : null;
};

export const getPlantRecipes = cache((id: string, kind: RecipeKind | null) =>
  getJson<PlantRecipes>(`/plants/${encodeURIComponent(id)}/recipes${qs({ kind, limit: 12 })}`, CARD_TTL, 10000, [plantTag(id)]),
);
export const getPairings = cache((id: string) =>
  getJson<Pairings>(`/plants/${encodeURIComponent(id)}/pairings?limit=8`, CARD_TTL, 10000, [plantTag(id)]));
export const getCompoundInsights = cache((id: string) =>
  getJson<CompoundInsights>(`/plants/${encodeURIComponent(id)}/compound_insights?limit=6`, CARD_TTL, 10000, [plantTag(id)]),
);

/** Наблюдения iNaturalist в Москве. Название места передаём по-английски: бэкенд ищет
 *  его в справочнике мест iNaturalist, и «Москва» там сейчас находит школу, а «Moscow»
 *  находит город (Moscow City, RU). */
export const OBS_PLACE_RU = "в Москве";
export const getObservations = cache((id: string) =>
  getJson<Observations>(`/plants/${encodeURIComponent(id)}/observations${qs({ place: "Moscow", limit: 8 })}`, 21600, 15000),
);

/** Фото видов рода: список сводок только с фото, сопоставляется с членами хаба по id. */
export const getGenusPhotos = cache(async (latin: string): Promise<PlantSummary[]> => {
  const page = (offset: number) => getJson<PlantSummary[]>(`/plants/${qs({ q: latin, has_photo: true, limit: 60, offset: offset || null })}`, 3600);
  const first = (await page(0)) ?? [];
  if (first.length < 60) return first;
  const second = (await page(60)) ?? [];
  return first.concat(second);
});

// ——— гравюры ———

/** Ключ гравюры: первые два слова латыни через «_» в нижнем регистре. */
export function plateKey(latin?: string | null): string | null {
  const words = (latin || "").replace(/[^A-Za-z\s-]/g, " ").trim().split(/\s+/).filter(Boolean);
  if (words.length < 2) return null;
  return `${words[0]}_${words[1]}`.toLowerCase();
}
export const plateUrl = (key: string) => `https://botanik.fun/ui/plates/${key}.png`;

const plateMemo = new Map<string, { ok: boolean; at: number }>();

/** Есть ли гравюра: сначала своя копия в public/ui/plates, потом HEAD на домен.
 *  Кэш данных Next хранит только ответы 200, поэтому отказ запоминаем здесь на сутки. */
export async function plateExists(key: string | null): Promise<boolean> {
  if (!key || !/^[a-z-]+_[a-z-]+$/.test(key)) return false;
  const hit = plateMemo.get(key);
  if (hit && Date.now() - hit.at < 86400000) return hit.ok;
  let ok = false;
  try {
    ok = existsSync(join(process.cwd(), "public", "ui", "plates", `${key}.png`));
  } catch {
    ok = false;
  }
  if (!ok) {
    try {
      const r = await fetch(plateUrl(key), { method: "HEAD", next: { revalidate: 86400 }, signal: AbortSignal.timeout(4000) });
      ok = r.ok;
    } catch {
      ok = false;
    }
  }
  plateMemo.set(key, { ok, at: Date.now() });
  return ok;
}

// ——— мелочи для страницы ———

/** Имя для заголовка: «РОЗА МУСКУСНАЯ» → «Роза мускусная», «лисичка желтая» → «Лисичка желтая». */
export function displayName(s: string | null | undefined): string {
  const t = clean(s);
  if (!t) return "";
  const letters = t.replace(/[^A-Za-zА-Яа-яЁё]/g, "");
  const base = letters.length > 3 && letters === letters.toUpperCase() ? t.toLowerCase() : t;
  return base.charAt(0).toUpperCase() + base.slice(1);
}

export const capFirst = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/** id книги по её названию (у цитат очерка есть только название). */
export function bookIdFor(map: Record<string, string>, title: string | null | undefined): string | null {
  const k = keyOf(title);
  return k ? map[k] ?? null : null;
}

/** Большие фото iNaturalist: medium (500 px) мало для шапки, берём large. */
export function largePhoto(url: string): string {
  return /inaturalist/.test(url) ? url.replace(/\/(square|small|medium)\.(jpe?g|png)/i, "/large.$2") : url;
}
/** Источник фото по адресу, когда поле photo_source не пришло (сводки списка). */
export function photoSourceOf(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/inaturalist/.test(url)) return "inaturalist";
  if (/wikimedia|wikipedia/.test(url)) return "wikimedia";
  return null;
}
export function mediumPhoto(url: string): string {
  return /inaturalist/.test(url) ? url.replace(/\/(square|small|thumb)\.(jpe?g|png)/i, "/medium.$2") : url;
}

export function quoteOf(q: FieldQuote | undefined): { text: string; source: string | null; page: number | null } | null {
  if (!q) return null;
  if (typeof q === "string") return clean(q) ? { text: clean(q), source: null, page: null } : null;
  const text = clean(q.text);
  return text ? { text, source: clean(q.source) || null, page: q.page ?? null } : null;
}

/** Query-строка из searchParams страницы: переносим метки при перенаправлении. */
export function queryFrom(sp: Record<string, string | string[] | undefined> | undefined): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(sp ?? {})) {
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else if (v !== undefined && v !== null) q.set(k, v);
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** JSON-LD безопасно для <script>: каждый «<» заменяем на юникод-экранирование (обратная
 *  косая + u003c), чтобы «</script>» внутри строк не закрыл тег. */
const LT_ESCAPE = String.fromCharCode(92) + "u003c";
export const jsonLd = (obj: unknown) => JSON.stringify(obj).replace(/</g, LT_ESCAPE);

/** Совпадают ли имена без учёта регистра и «ё». */
export const sameName = (a?: string | null, b?: string | null) => keyOf(a) === keyOf(b);

/** Есть ли у карточки своё содержание для поисковика: записи о применении или о еде,
 *  описание или очерк длиннее 200 знаков, у рода хотя бы два вида. Пустые карточки
 *  получают noindex. Бэкенд отбирает карту сайта по тому же правилу (SUBSTANTIVE_SQL в
 *  backend/app/routers/plants.py), поэтому в карту не попадает страница с noindex. */
export function isSubstantive(card: PlantCard, field?: FieldView | null): boolean {
  if (card.kind === "genus") return card.memberCount >= 2;
  return (
    card.usesTotal > 0 ||
    card.culinary.total > 0 ||
    (card.description ?? "").length >= 200 ||
    (field?.description ?? "").length >= 200
  );
}

/** Заголовок карточки под то, что ищут: «Зверобой (Hypericum perforatum), лечебные
 *  свойства и применение», у грибов съедобность или ядовитость. Если с латынью выходит
 *  длиннее 70 знаков, латынь уходит: поисковик всё равно обрежет хвост. */
export function cardTitle(card: PlantCard, name: string): string {
  const withLatin = card.latin ? `${name} (${card.latin})` : name;
  let suffix = "";
  if (card.kind === "genus") {
    suffix = card.memberCount >= 2 ? ", виды и применение" : "";
    return `Род ${withLatin}${suffix}`;
  }
  const level = card.safety?.level ?? null;
  if (card.kingdom === "гриб") {
    if (level != null && level >= 3) suffix = ", ядовитость и описание";
    else if (level === 1 || level === 2) suffix = ", съедобность и описание";
  } else if (card.usesTotal > 0) suffix = ", лечебные свойства и применение";
  else if (card.culinary.total > 0) suffix = ", съедобность и рецепты";
  const full = withLatin + suffix;
  return full.length <= 70 || !suffix ? full : name + suffix;
}

/** Гейт индексации: растение или гриб, фото, кириллическое имя, латынь похожа на латынь. */
export function passesGate(p: { kingdom: string | null; name: string; latin: string | null; photo: boolean }): boolean {
  const kingdomOk = p.kingdom === "растение" || p.kingdom === "гриб";
  const nameOk = /[А-Яа-яЁё]/.test(p.name) && !/[A-Za-z]/.test(p.name);
  const latinOk = !!p.latin && /^[A-Za-z][a-z-]+(\s|$)/.test(p.latin.trim());
  return kingdomOk && p.photo && nameOk && latinOk;
}
