// Базовый доступ к внутреннему API бэкенда для серверного рендера сайта.
// Все запросы идут изнутри docker-сети (http://backend:8000/api), в браузер ничего
// не утекает, публичный белый список и CORS не нужны. Разделы сайта описывают свои
// типы и функции в lib/api-<раздел>.ts поверх этих помощников.

export const API = process.env.LANDING_API_BASE || process.env.SITE_API_BASE || "http://backend:8000/api";
export const SITE_URL = process.env.SITE_URL || "https://botanik.fun";
/** Картинка для соцсетей по умолчанию (app/opengraph-image.tsx). */
export const DEFAULT_OG = { url: `${SITE_URL}/opengraph-image`, width: 1200, height: 630 };

export class ApiError extends Error {
  constructor(public status: number, public path: string) {
    super(`API ${status} ${path}`);
  }
}

/** Как кэшировать ответ бэкенда. revalidate > 0: в кэше данных Next на этот срок, с метками
 *  (по ним /api/revalidate сбрасывает карточку раньше срока). revalidate 0: не кэшировать вовсе.
 *  Ноль нужен спискам с фильтрами и страницами и любому поиску: роботы порождают бесконечно
 *  новые адреса, а Next 14 записи кэша данных обновляет, но не удаляет. 05.10.2026 том кэша
 *  сайта вырос так до 55 ГБ за четыре дня. Бэкенд отвечает на такие списки за 0,2 с. */
export function fetchCache(revalidate: number, tags?: string[]): RequestInit {
  return revalidate > 0 ? { next: { revalidate, tags } } : { cache: "no-store" };
}

/** GET → JSON или null при любой ошибке и таймауте. revalidate в секундах, 0 значит без кэша. */
export async function getJson<T>(path: string, revalidate = 300, timeoutMs = 10000, tags?: string[]): Promise<T | null> {
  try {
    const res = await fetch(API + path, { ...fetchCache(revalidate, tags), signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** Как getJson, но различает «нет такой сущности» (404) и сбой: бросает ApiError на 4xx/5xx. */
export async function getJsonStrict<T>(path: string, revalidate = 300, timeoutMs = 10000, tags?: string[]): Promise<T> {
  const res = await fetch(API + path, { ...fetchCache(revalidate, tags), signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) throw new ApiError(res.status, path);
  return (await res.json()) as T;
}

/** Сырой ответ (для проксирования картинок страниц и других бинарных данных). */
export async function getRaw(path: string, timeoutMs = 20000): Promise<Response> {
  return fetch(API + path, { cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
}

/** Собрать query-строку, пропуская пустые значения. */
export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (s: string) => UUID_RE.test(s);

/** Человеческий адрес карточки из латыни: «Urtica dioica L.» → «urtica-dioica».
 *  Для однозначности к адресу добавляется хвост из первых 6 знаков id: «urtica-dioica-3fa9c1».
 *  Так адрес читается и не требует таблицы слагов; старые UUID-адреса продолжают работать. */
export function plantSlug(id: string, latin?: string | null): string {
  const core = (latin || "")
    .replace(/[^A-Za-z\s-]/g, " ")
    .trim()
    .split(/\s+/)
    .filter((w) => w && !/^(l|var|subsp|sp|spp|f)$/i.test(w))
    .slice(0, 3)
    .join("-")
    .toLowerCase();
  const tail = id.replace(/-/g, "").slice(0, 6);
  return core ? `${core}-${tail}` : id;
}

/** Канонический адрес карточки вида на сайте: /atlas/{слаг}.
 *  Не /plant/{слаг}: пути /plant/* перехватывают App Links и Universal Links
 *  приложения, а приложение ждёт там UUID. /plant/{uuid} остаётся адресом шэров
 *  из приложения и перенаправляет сюда. */
export function plantHref(id: string, latin?: string | null): string {
  return `/atlas/${plantSlug(id, latin)}`;
}

/** Из адреса карточки достать хвост id (6 hex) либо весь UUID. */
export function parsePlantSlug(slug: string): { uuid?: string; tail?: string } {
  if (isUuid(slug)) return { uuid: slug };
  const m = slug.match(/-([0-9a-f]{6})$/i);
  return m ? { tail: m[1].toLowerCase() } : {};
}

/** Назначение рецепта. Чип у одного рецепта согласован со словом «рецепт»: «лечебный».
 *  У «других» рецептов чипа нет, слово ничего не сообщает. */
export const RECIPE_KIND_ONE: Record<string, string> = { medicinal: "лечебный", food: "кулинарный", cosmetic: "косметический" };
/** Назначение в фильтре списка рецептов: «лечебные». */
export const RECIPE_KIND_MANY: Record<string, string> = { medicinal: "лечебные", food: "кулинарные", cosmetic: "косметические", other: "другие" };
/** Назначение внутри строки через запятую: «настойка, лечебный рецепт». */
export const recipeKindPhrase = (k?: string | null): string | null => (k && RECIPE_KIND_ONE[k] ? `${RECIPE_KIND_ONE[k]} рецепт` : null);

/** Автор книги для показа. «Неизвестен» в этом поле значит, что автора нет. */
export const realAuthor = (a?: string | null): string | null => (a && !/^(автор\s+)?неизвест/i.test(a.trim()) ? a : null);

/** «Название, 1962». Год не повторяем, если он уже стоит в названии: «Атлас… (1962)». */
export function titleYear(title: string, year?: number | null): string {
  return year && !title.includes(String(year)) ? `${title}, ${year}` : title;
}

export function pluralRu(n: number, one: string, few: string, many: string): string {
  const m100 = n % 100, m10 = n % 10;
  if (m100 >= 11 && m100 <= 14) return many;
  if (m10 === 1) return one;
  if (m10 >= 2 && m10 <= 4) return few;
  return many;
}

export function fmtInt(n: number): string {
  return new Intl.NumberFormat("ru-RU").format(n);
}

/** Обрезать текст до целого слова. */
export function excerpt(s: string | null | undefined, max = 180): string {
  if (!s) return "";
  const t = s.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  return cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 30)).trim() + "…";
}
