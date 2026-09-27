// Состояние каталога атласа в адресе: фильтры, сортировка, вид и страница.
// Фасеты, чипы активных фильтров, пейджер и форма поиска строят ссылки через atlasHref,
// поэтому порядок параметров в адресе всегда один и тот же.

export type AtlasState = {
  q?: string;
  kingdom?: string;
  family?: string;
  action?: string;
  indication?: string;
  biotope?: string;
  edible?: string;
  edibility?: string;
  is_toxic?: string;
  sort?: string;
  view?: string;
  page?: number;
};

export type SearchParams = Record<string, string | string[] | undefined>;

export const FILTER_KEYS = ["q", "kingdom", "family", "action", "indication", "biotope", "edible", "edibility", "is_toxic"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];
const ORDER = [...FILTER_KEYS, "sort", "view", "page"] as const;

export const firstParam = (v: string | string[] | undefined): string | undefined => {
  const s = (Array.isArray(v) ? v[0] : v)?.replace(/\s+/g, " ").trim();
  return s ? s : undefined;
};

export function parseAtlasParams(sp: SearchParams): AtlasState {
  const page = Number(firstParam(sp.page));
  const sort = firstParam(sp.sort);
  return {
    q: firstParam(sp.q)?.slice(0, 80),
    kingdom: firstParam(sp.kingdom),
    family: firstParam(sp.family),
    action: firstParam(sp.action),
    indication: firstParam(sp.indication),
    biotope: firstParam(sp.biotope),
    edible: firstParam(sp.edible) === "true" ? "true" : undefined,
    edibility: firstParam(sp.edibility),
    is_toxic: firstParam(sp.is_toxic) === "true" ? "true" : undefined,
    // «photo» и есть сортировка по умолчанию: в адрес её не пишем.
    sort: sort === "name" || sort === "uses" ? sort : undefined,
    view: firstParam(sp.view) === "table" ? "table" : undefined,
    page: Number.isFinite(page) && page > 1 ? Math.floor(page) : undefined,
  };
}

/** Ссылка на каталог с изменёнными параметрами; пустые значения из адреса выпадают. */
export function atlasHref(state: AtlasState, patch: Partial<AtlasState> = {}, base = "/atlas"): string {
  const next: AtlasState = { ...state, ...patch };
  const sp = new URLSearchParams();
  for (const k of ORDER) {
    const v = next[k];
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `${base}?${s}` : base;
}

/** Параметры для общего Pager: всё состояние, кроме страницы. */
export function pagerParams(state: AtlasState): Record<string, string | undefined> {
  const out: Record<string, string | undefined> = {};
  for (const k of ORDER) if (k !== "page" && state[k]) out[k] = String(state[k]);
  return out;
}

export const activeFilterKeys = (s: AtlasState): FilterKey[] => FILTER_KEYS.filter((k) => !!s[k]);
