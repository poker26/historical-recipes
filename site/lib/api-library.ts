// Библиотека: полка книг, страница книги, страница источника и прокси картинок.
// Классы доступа (RFC §8.1): open — книга свободна, страницы листаются целиком;
// cited — год неизвестен или книга охраняется: только номер страницы, ссылка и
// фрагменты, уже разобранные в атлас; closed — только карточка книги.
import { ApiError, getJson, getJsonStrict, getRaw, qs } from "./api";

export type Access = "open" | "cited" | "closed";

export type LibraryStats = {
  books: number; with_year: number; open_books: number; year_min: number | null; year_max: number | null;
  pages: number; scan_pages: number; books_with_scans: number; domains: { domain: string; count: number }[];
};

export type BookItem = {
  id: string; title: string; author: string | null; year: number | null; domain: string | null;
  language: string | null; source_format: string | null; pdf_type: string | null; status: string;
  pages: number; scan_pages: number; text_pages: number; first_scan_page: number | null;
  plants: number; recipes: number; home_recipes: number; uses: number; access: Access; has_cover: boolean;
};

export type TopPlant = {
  id: string; name: string; name_latin: string | null; name_modern: string | null; photo_url: string | null;
  kingdom: string | null; mentions: number; uses: number;
};
export type BookRecipe = {
  id: string; name: string; category: string | null; recipe_kind: string | null; home_doable: boolean | null; source_page: number | null;
};
/** В ответе о книге поле recipes уже не число, а первые 24 рецепта (бэкенд
 *  перезаписывает счётчик списком). Сколько рецептов всего: recipesTotal(). */
export type BookDetail = Omit<BookItem, "recipes"> & {
  top_plants: TopPlant[]; toc: { title: string; type: string }[]; recipes: BookRecipe[];
  recipe_kinds: { kind: string | null; count: number }[]; anchored_facts: number;
  busiest_pages: { page: number; facts: number }[]; citation: string;
};

export const recipesTotal = (b: BookDetail) => (b.recipe_kinds ?? []).reduce((s, k) => s + (k.count || 0), 0);

export type FactKind = "use" | "culinary" | "harvest" | "habitat" | "toxicity" | "mention";
export type PageFact = {
  kind: FactKind; id: string; plant_id: string; plant_name: string; plant_latin: string | null; photo_url: string | null;
  original_text: string | null; anchor_score: number | null; label: string | null; part: string | string[] | null;
};
export type PageRecipe = {
  id: string; name: string | null; category: string | null; recipe_kind: string | null; home_doable: boolean | null;
  original_text: string | null; anchor_score: number | null;
};
export type PagePlant = { id: string; name: string; name_latin: string | null; photo_url: string | null; facts: number };
export type BookPage = {
  book: { id: string; title: string; author: string | null; year: number | null; domain: string | null; language: string | null;
    access: Access; pages: number; scan_pages: number };
  page: { number: number; has_scan: boolean; has_image: boolean; text: string | null; text_len: number; ocr_confidence: number | null };
  nav: { prev: number | null; next: number | null; first: number | null; last: number | null };
  facts: PageFact[]; recipes: PageRecipe[]; plants: PagePlant[]; citation: string;
};
export type BookSearch = { q: string; book_id: string; access: Access; hits: { page: number; snippet?: string }[] };

/** Ответ API вместе со статусом: 404 ведёт на notFound(), сбой бэкенда (0 или 5xx)
 *  на честное пустое состояние. */
export type Fetched<T> = { data: T; status: 200 } | { data: null; status: number };

async function fetchWithStatus<T>(path: string, revalidate: number, timeoutMs: number): Promise<Fetched<T>> {
  try {
    return { data: await getJsonStrict<T>(path, revalidate, timeoutMs), status: 200 };
  } catch (e) {
    return { data: null, status: e instanceof ApiError ? e.status : 0 };
  }
}

// ---------------------------------------------------------------- подписи

export const DOMAIN_RU: Record<string, string> = {
  herbalism: "травники и лечебники",
  recipes: "кулинария и напитки",
  reference: "справочники",
  fungi: "грибы",
  aromatherapy: "ароматерапия",
};
export const domainLabel = (d: string | null | undefined) => (d ? DOMAIN_RU[d] ?? d : null);

export const ERAS: { key: string; label: string }[] = [
  { key: "pre1917", label: "до 1917 года" },
  { key: "soviet", label: "советские" },
  { key: "modern", label: "современные" },
  { key: "unknown", label: "год неизвестен" },
];

export const SORTS: { key: string; label: string }[] = [
  { key: "year", label: "по году издания" },
  { key: "title", label: "по названию" },
  { key: "plants", label: "больше растений" },
  { key: "recipes", label: "больше рецептов" },
  { key: "pages", label: "больше страниц" },
];

export const FACT_KIND_RU: Record<FactKind, string> = {
  use: "применение",
  culinary: "кулинария",
  harvest: "сбор",
  habitat: "где растёт",
  toxicity: "ядовитость",
  mention: "упоминание",
};

export const RECIPE_KIND_RU: Record<string, string> = {
  medicinal: "лечебное",
  food: "еда",
  cosmetic: "косметика",
  fragment: "фрагмент",
  monograph: "статья о растении",
};

/** Форма и вид рецепта одной строкой: «отвар · лечебное». Вид «прочее» ничего не
 *  добавляет к форме, его не пишем. */
export function recipeMeta(category: string | null, kind: string | null): string {
  const k = kind && kind !== "other" ? RECIPE_KIND_RU[kind] ?? kind : null;
  return [category, k].filter(Boolean).join(" · ");
}

export const ACCESS_CHIP: Record<Access, { label: string; kind: "leaf" | "mist" }> = {
  open: { label: "читать целиком", kind: "leaf" },
  cited: { label: "фрагменты и ссылки", kind: "mist" },
  closed: { label: "только карточка", kind: "mist" },
};

/** Почему книга показана так, как показана. Одно-два предложения для читателя. */
export function accessNote(access: Access, year: number | null): string {
  if (access === "open") {
    return year
      ? `Книга вышла в ${year} году и свободна от авторских прав, поэтому её страницы можно листать целиком: скан и распознанный текст рядом.`
      : "Книга свободна от авторских прав, поэтому её страницы можно листать целиком: скан и распознанный текст рядом.";
  }
  if (access === "closed") return "Эта книга закрыта: от неё на сайте есть только карточка с описанием и ссылкой.";
  if (!year) {
    return "Год издания не установлен, поэтому показываем только номера страниц и фрагменты, уже разобранные в атлас. Картинки страниц и их полный текст откроются, когда станет ясно, что срок охраны истёк.";
  }
  return `Книга вышла в ${year} году и ещё охраняется авторским правом, поэтому показываем только номера страниц, библиографическую ссылку и фрагменты, уже разобранные в атлас.`;
}

/** С какой страницы начинать чтение. Страницы PDF и DjVu рисуются из исходника целиком,
 *  поэтому с первой; у остальных с первого скана. */
export function firstReadablePage(b: Pick<BookItem, "source_format" | "first_scan_page">): number {
  if (b.source_format === "pdf" || b.source_format === "djvu") return 1;
  return b.first_scan_page ?? 1;
}

/** Первое значение query-параметра. */
export function param(v: string | string[] | undefined): string | undefined {
  const s = Array.isArray(v) ? v[0] : v;
  const t = s?.trim();
  return t ? t : undefined;
}

// ---------------------------------------------------------------- запросы

export const getLibraryStats = () => getJson<LibraryStats>(`/library/stats`, 3600);

export type ShelfQuery = { q?: string; domain?: string; era?: string; access?: string; scans?: boolean; sort?: string };

export const SHELF_LIMIT = 60;

export const getBooks = (f: ShelfQuery, offset = 0, limit = SHELF_LIMIT) =>
  getJson<{ total: number; items: BookItem[] }>(
    `/library/books${qs({ q: f.q, domain: f.domain, era: f.era, access: f.access, scans: f.scans ? "true" : undefined, sort: f.sort, limit, offset })}`,
    1800,
    15000,
  );

/** Сколько книг под одним фильтром (для счётчиков в фасетах): total при limit=1. */
export async function countBooks(f: ShelfQuery): Promise<number | null> {
  const r = await getJson<{ total: number }>(
    `/library/books${qs({ domain: f.domain, era: f.era, access: f.access, scans: f.scans ? "true" : undefined, limit: 1 })}`,
    3600,
  );
  return r ? r.total : null;
}

export const getBook = (id: string) => fetchWithStatus<BookDetail>(`/library/books/${id}`, 3600, 20000);

export const getBookPage = (id: string, n: number) =>
  fetchWithStatus<BookPage>(`/library/books/${id}/pages/${n}`, 3600, 20000);

export const searchBook = (id: string, q: string, limit = 20) =>
  getJson<BookSearch>(`/library/books/${id}/search${qs({ q, limit })}`, 600, 15000);

// ---------------------------------------------------------------- прокси картинок

const NOT_CACHED = { "Cache-Control": "no-store" };

/** Отдать картинку бэкенда как есть: MinIO и бэкенд снаружи не видны, браузер ходит
 *  только на сайт. 403 и 404 возвращаем тем же статусом с пустым телом. */
export async function proxyImage(path: string, cacheControl: string): Promise<Response> {
  let res: Response;
  try {
    res = await getRaw(path, 20000);
  } catch {
    return new Response(null, { status: 504, headers: NOT_CACHED });
  }
  if (res.status === 403 || res.status === 404) {
    return new Response(null, { status: res.status, headers: { "Cache-Control": "public, max-age=600" } });
  }
  if (!res.ok) return new Response(null, { status: 502, headers: NOT_CACHED });
  try {
    const body = await res.arrayBuffer();
    return new Response(body, {
      status: 200,
      headers: { "Content-Type": "image/jpeg", "Cache-Control": cacheControl, "X-Content-Type-Options": "nosniff" },
    });
  } catch {
    return new Response(null, { status: 504, headers: NOT_CACHED });
  }
}
