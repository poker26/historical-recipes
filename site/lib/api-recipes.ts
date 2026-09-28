// Данные раздела «Рецепты»: каталог домашних рецептов, словарь фильтров, страница рецепта.
// Список отдаёт общее число совпадений в заголовке X-Total-Count, поэтому здесь свой
// fetch поверх базового API, а не getJson: пейджеру нужно знать, сколько всего страниц.
import { cache } from "react";
import { API, ApiError, RECIPE_KIND_ONE, getJson, getJsonStrict, qs, realAuthor } from "./api";

/** GET → { data, total } или null при ошибке. total берётся из X-Total-Count. */
export async function getJsonTotal<T>(path: string, revalidate = 600, timeoutMs = 15000): Promise<{ data: T; total: number | null } | null> {
  try {
    const res = await fetch(API + path, { next: { revalidate }, signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return null;
    const raw = res.headers.get("x-total-count");
    const n = raw == null || raw === "" ? NaN : Number(raw);
    return { data: (await res.json()) as T, total: Number.isFinite(n) ? n : null };
  } catch {
    return null;
  }
}

/** Сущность по id: различает «такой нет» (404/422) и «бэкенд не ответил» (null). */
export async function getEntity<T>(path: string, revalidate = 3600, timeoutMs = 15000): Promise<{ data: T | null; missing: boolean }> {
  try {
    return { data: await getJsonStrict<T>(path, revalidate, timeoutMs), missing: false };
  } catch (e) {
    if (e instanceof ApiError && (e.status === 404 || e.status === 422)) return { data: null, missing: true };
    return { data: null, missing: false };
  }
}

export const KINDS = ["medicinal", "food", "cosmetic", "other"] as const;
export type RecipeKind = (typeof KINDS)[number];
export const DOMAINS = ["recipes", "herbalism", "fungi"] as const;

/** Подписи фасетов (множественное число, «рецепты») и чипов строки («рецепт»). */
export const KIND_FACET_RU: Record<string, string> = { medicinal: "Лечебные", food: "Кулинарные", cosmetic: "Косметические", other: "Другие" };
export const KIND_CHIP_RU: Record<string, string> = RECIPE_KIND_ONE;
export const DOMAIN_RU: Record<string, string> = { recipes: "Кулинарные книги", herbalism: "Травники", fungi: "Книги о грибах" };
export const ERA_RU: Record<string, string> = { pre1917: "до 1917 года", soviet: "в советских книгах", modern: "в современных", unknown: "без года издания" };

export type RecipeBrief = {
  id: string;
  book_id: string;
  book_title?: string | null;
  book_author?: string | null;
  book_year?: number | null;
  book_domain?: string | null;
  name?: string | null;
  category?: string | null;
  recipe_kind?: string | null;
  home_doable?: boolean | null;
  step_by_step?: boolean | null;
  excerpt?: string | null;
  year?: number | null;
};

export type RecipeFilters = {
  kind?: string;
  category?: string;
  domain?: string;
  step_by_step?: boolean;
  plant_id?: string;
  book_id?: string;
  q?: string;
};

export const PAGE_SIZE = 24;

/** Состояние каталога в адресе: /recipes?kind=&category=&domain=&step_by_step=true&plant_id=&q=&page= */
export type CatalogState = { kind?: string; category?: string; domain?: string; step?: boolean; plant_id?: string; book_id?: string; q?: string };

/** Адрес каталога с изменённым фильтром; страница сбрасывается на первую. */
export function catalogHref(s: CatalogState, patch: Partial<CatalogState> = {}): string {
  const n = { ...s, ...patch };
  return "/recipes" + qs({ kind: n.kind, category: n.category, domain: n.domain, step_by_step: n.step ? "true" : undefined, plant_id: n.plant_id, book_id: n.book_id, q: n.q });
}

/** Те же параметры строками, для Pager (он сам добавляет page). */
export function catalogParams(s: CatalogState): Record<string, string | undefined> {
  return { kind: s.kind, category: s.category, domain: s.domain, step_by_step: s.step ? "true" : undefined, plant_id: s.plant_id, book_id: s.book_id, q: s.q };
}

/** Страница каталога домашних рецептов (лучшие по оценке «пошаговости» первыми). */
export function getRecipeList(f: RecipeFilters, limit = PAGE_SIZE, offset = 0) {
  const path = `/recipes/${qs({
    home_doable: true, brief: true, sort: "quality", limit, offset,
    kind: f.kind, category: f.category, domain: f.domain,
    step_by_step: f.step_by_step || undefined, plant_id: f.plant_id, book_id: f.book_id, q: f.q,
  })}`;
  return getJsonTotal<RecipeBrief[]>(path, 600, 15000);
}

export type Facet = { value: string; count: number };
export type RecipeVocab = {
  categories: Facet[];
  kinds: Facet[];
  domains: Facet[];
  eras: Facet[];
  step_by_step: number;
  total: number;
};

export const getRecipeVocab = () => getJson<RecipeVocab>(`/recipes/categories?home_doable=true`, 3600, 15000);

export type Ingredient = {
  id: string;
  name?: string | null;
  original_name?: string | null;
  amount?: string | null;
  unit?: string | null;
  amount_modern?: string | null;
  unit_modern?: string | null;
  plant_id?: string | null;
  plant_name?: string | null;
  plant_latin?: string | null;
  plant_photo?: string | null;
  plant_safety_level?: number | null;
  plant_is_toxic?: boolean | null;
};

export type RecipeDetail = RecipeBrief & {
  book_language?: string | null;
  source_page?: number | null;
  anchor_method?: string | null;
  original_text?: string | null;
  normalized_text?: string | null;
  ingredients: Ingredient[];
};

/** Рецепт целиком. cache() склеивает вызовы из generateMetadata и страницы в одном запросе. */
export const getRecipe = cache((id: string) => getEntity<RecipeDetail>(`/recipes/${id}`, 21600, 15000));

/** Похожие рецепты: та же форма или то же главное растение. Текущий исключается на странице. */
export function getSimilarByCategory(category: string, limit = 7) {
  return getJson<RecipeBrief[]>(`/recipes/${qs({ category, home_doable: true, brief: true, limit, sort: "quality" })}`, 3600, 15000);
}
export function getSimilarByPlant(plantId: string, limit = 7) {
  return getJson<RecipeBrief[]>(`/recipes/${qs({ plant_id: plantId, home_doable: true, brief: true, limit, sort: "quality" })}`, 3600, 15000);
}

/** Имя растения для плашки «Рецепты с растением …». Родовой хаб тоже отдаёт name и name_latin. */
export type PlantName = { id?: string; name?: string | null; name_latin?: string | null };
export const getPlantName = cache((id: string) => getJson<PlantName>(`/plants/${id}?view=field`, 3600, 15000));

/** «101. Ликёр из роз» → «Ликёр из роз»: номер статьи в книге заголовку не нужен.
 *  «10% настойка имбиря» остаётся как есть: после цифр нет точки со скобкой. */
export function recipeTitle(name?: string | null): string {
  const t = (name || "").replace(/\s+/g, " ").trim().replace(/^\d{1,4}\s*[.)]\s+(?=\S)/, "");
  if (!t) return "Рецепт без названия";
  return t.charAt(0).toUpperCase() + t.slice(1);
}

/** SourceRef склеивает автора и книгу через «. », поэтому точку в конце автора
 *  («Соловьёв П.В.») убираем, иначе получится «П.В.. Домашний лечебник». */
export const refAuthor = (a?: string | null) => (realAuthor(a) ? a!.replace(/[.\s]+$/, "") || null : null);

/** Имя карточки внутри фразы: «Аконит» → «аконит»; латинские имена не трогаем. */
export const inSentence = (n: string) => (/^[А-ЯЁ][а-яё]/.test(n) ? n.charAt(0).toLowerCase() + n.slice(1) : n);

/** Год рецепта: год издания книги, а если его нет, год из самого рецепта. */
export const recipeYear = (r: { book_year?: number | null; year?: number | null }) => r.book_year ?? r.year ?? null;

/** Худший по безопасности привязанный ингредиент: 4 смертельно, 3 ядовито. Флаг is_toxic
 *  учитывается, только пока у карточки нет уровня: уровень главнее. */
export function worstIngredient(ings: Ingredient[]): { level: 4 | 3; names: string[] } | null {
  const deadly = ings.filter((i) => i.plant_id && i.plant_safety_level === 4);
  if (deadly.length) return { level: 4, names: uniqNames(deadly) };
  const toxic = ings.filter((i) => i.plant_id && (i.plant_safety_level === 3 || (i.plant_safety_level == null && i.plant_is_toxic)));
  if (toxic.length) return { level: 3, names: uniqNames(toxic) };
  return null;
}

function uniqNames(list: Ingredient[]): string[] {
  const out: string[] = [];
  for (const i of list) {
    const n = inSentence((i.plant_name || i.name || "").trim());
    if (n && !out.some((x) => x.toLowerCase() === n.toLowerCase())) out.push(n);
  }
  return out;
}

/** Количество как в книге и по-современному: «2 золотника ≈ 8,5 г по-современному». */
export function amountText(i: Ingredient): { book: string; modern: string } {
  const book = [i.amount, i.unit].filter((x) => x && String(x).trim()).join(" ").trim();
  const modernRaw = [i.amount_modern, i.unit_modern].filter((x) => x && String(x).trim()).join(" ").trim();
  const modern = modernRaw && modernRaw !== book ? modernRaw : "";
  return { book, modern };
}
