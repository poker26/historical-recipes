// Данные справочников: действия, показания, вещества, эфирные масла, биотопы.
// Словари медленные и тяжёлые, поэтому кэшируются на сутки. Словарь веществ весит
// около 3,5 МБ: это больше предела кэша fetch в Next (2 МБ), поэтому его урезанную
// копию держим в памяти процесса сервера (см. getCompoundVocab).
import { cache } from "react";
import { API, getJson, pluralRu, qs } from "./api";
import { getEntity, getJsonTotal } from "./api-recipes";

const DAY = 86400;

// ---------- растения (краткие карточки для плиток) ----------
export type PlantSummary = {
  id: string;
  name: string;
  name_latin?: string | null;
  name_modern?: string | null;
  family?: string | null;
  family_latin?: string | null;
  is_toxic?: boolean | null;
  kingdom?: string | null;
  photo_url?: string | null;
  photo_attribution?: string | null;
  uses_count?: number | null;
  rank?: string | null;
  safety_level?: number | null;
  deadly_twin?: string | null;
};

type PlantFilter = { action?: string; indication?: string; compound?: string; biotope?: string };

/** Плитки атласа по фильтру: только карточки, прошедшие гейт публикации. */
export function getPublishedPlants(f: PlantFilter, sort: "uses" | "photo", limit = 48) {
  return getJsonTotal<PlantSummary[]>(`/plants/${qs({ ...f, published: true, sort, limit })}`, 3600, 15000);
}

// ---------- фасеты и биотопы ----------
export type Facet = { value: string; count: number };
export type PlantFacets = { actions: Facet[]; compound_groups?: Facet[]; edibility?: Facet[]; kingdom?: Facet[] };
export const getPlantFacets = () => getJson<PlantFacets>(`/plants/facets`, 21600, 15000);

export type Biotope = { key: string; group: string; count: number };
export const getBiotopes = () => getJson<{ biotopes: Biotope[] }>(`/plants/biotopes`, 21600, 15000);

export const BIOTOPE_GROUPS = ["лес", "открытое", "влажное", "субстрат", "рельеф", "антропогенное", "прочее"];
export const BIOTOPE_GROUP_RU: Record<string, string> = {
  лес: "Лес и опушки",
  открытое: "Открытые места",
  влажное: "У воды и на болоте",
  субстрат: "Камни, пески и солончаки",
  рельеф: "Горы",
  антропогенное: "Рядом с человеком",
  прочее: "Заросли кустарников",
};
/** Одна фраза о группе биотопов для страницы биотопа. */
export const BIOTOPE_GROUP_TEXT: Record<string, string> = {
  лес: "Лесные виды живут в тени деревьев, на подстилке и на опушках, где света больше, чем в чаще.",
  открытое: "На лугах, в степи и на полях растения стоят на солнце и привыкли к сухому лету.",
  влажное: "У воды и на болотах растут виды, которым нужна сырая почва или корни прямо в воде.",
  субстрат: "На камнях, песках и засолённых почвах выживают виды, которые терпят бедную почву и засуху.",
  рельеф: "В горах и предгорьях растения приспособлены к холоду, ветру и короткому лету.",
  антропогенное: "В садах и парках растут и посаженные человеком виды, и те, что пришли к жилью сами.",
  прочее: "В кустарниковых зарослях растут виды, которым нужна полутень и защита от ветра.",
};
/** «опушки/поляны/вырубки/редколесье» → «опушки, поляны, вырубки, редколесье». */
export const biotopeLabel = (key: string) => key.split("/").map((s) => s.trim()).filter(Boolean).join(", ");
/** Адрес страницы биотопа. Косая черта остаётся разделителем пути: страница ловит все сегменты. */
export const biotopeHref = (key: string) => `/biotopes/${key.split("/").map(encodeURIComponent).join("/")}`;

// ---------- действия ----------
export type ActionTerm = {
  id: string;
  name: string;
  name_modern?: string | null;
  parent_id?: string | null;
  system?: string | null;
  synonyms: string[];
  linked_facts: number;
};
export const getActionVocab = () => getJson<ActionTerm[]>(`/medical/actions`, DAY, 30000);

// ---------- показания ----------
export type IndicationTerm = {
  id: string;
  name: string;
  name_modern?: string | null;
  parent_id?: string | null;
  system?: string | null;
  synonyms: string[];
  archaic: string[];
  definition?: string | null;
  linked_facts: number;
};
export const getIndicationVocab = () => getJson<IndicationTerm[]>(`/medical/indications`, DAY, 30000);

export type IndicationDetail = {
  id: string;
  name: string;
  name_modern?: string | null;
  parent?: { id: string; name: string } | null;
  parent_id?: string | null;
  system?: string | null;
  synonyms: string[];
  archaic: string[];
  definition?: string | null;
  children: { id: string; name: string }[];
  plants: { id: string; name: string; name_latin?: string | null; parts?: string[]; raw_indications?: string[] }[];
};
export const getIndication = cache((id: string) => getEntity<IndicationDetail>(`/medical/indications/${id}`, DAY, 20000));

export type AssocRow = {
  id?: string | null;
  name: string;
  support: number;
  source_plants: number;
  target_plants: number;
  base_plants: number;
  lift: number;
  p_value: number;
  plants: { id: string; name: string; name_latin?: string | null }[];
};
export type Assoc = {
  source?: { name?: string } | null;
  axis?: string;
  min_support?: number;
  note?: string | null;
  n_base?: number;
  n_source?: number;
  results: AssocRow[];
};
/** Считается на лету около 10 секунд, поэтому кэш на сутки и длинный таймаут. */
export const getIndicationCompounds = (id: string, limit = 10) =>
  getJson<Assoc>(`/medical/indications/${id}/compounds${qs({ limit })}`, DAY, 25000);

// ---------- вещества ----------
export type CompoundLite = {
  id: string;
  name: string;
  name_latin?: string | null;
  parent_id?: string | null;
  compound_class?: string | null;
  linked_facts: number;
};
type CompoundFull = CompoundLite & { synonyms?: string[]; definition?: string | null };

let compoundMemo: { at: number; data: CompoundLite[] } | null = null;
let compoundInflight: Promise<CompoundLite[] | null> | null = null;

/** Словарь веществ, только термины с привязанными фактами (около 4,3 тыс. из 9 тыс.).
 *  Держим в памяти сутки; при сбое обновления отдаём прошлую копию. */
export async function getCompoundVocab(): Promise<CompoundLite[] | null> {
  if (compoundMemo && Date.now() - compoundMemo.at < DAY * 1000) return compoundMemo.data;
  if (!compoundInflight) {
    compoundInflight = (async () => {
      try {
        const res = await fetch(API + "/compounds", { cache: "no-store", signal: AbortSignal.timeout(30000) });
        if (!res.ok) return null;
        const raw = (await res.json()) as CompoundFull[];
        const data: CompoundLite[] = raw
          .filter((c) => (c.linked_facts ?? 0) > 0 && c.name)
          .map((c) => ({ id: c.id, name: c.name, name_latin: c.name_latin, parent_id: c.parent_id, compound_class: c.compound_class, linked_facts: c.linked_facts }));
        compoundMemo = { at: Date.now(), data };
        return data;
      } catch {
        return null;
      } finally {
        compoundInflight = null;
      }
    })();
  }
  const fresh = await compoundInflight;
  return fresh ?? compoundMemo?.data ?? null;
}

export type CompoundDetail = {
  id: string;
  name: string;
  name_latin?: string | null;
  parent?: { id: string; name: string } | null;
  parent_id?: string | null;
  compound_class?: string | null;
  synonyms: string[];
  definition?: string | null;
  original_text?: string | null;
  children: { id: string; name: string }[];
  plants: { id: string; name: string; name_latin?: string | null; parts?: string[]; raw_names?: string[] }[];
  linked_facts?: number;
};
export const getCompound = cache((id: string) => getEntity<CompoundDetail>(`/compounds/${id}`, DAY, 20000));
export const getCompoundAssociations = (id: string, limit = 10) =>
  getJson<Assoc>(`/compounds/${id}/associations${qs({ axis: "action", limit })}`, DAY, 25000);

// ---------- эфирные масла ----------
export type OilItem = {
  id: string;
  name: string;
  name_latin?: string | null;
  plant_id?: string | null;
  plant_name?: string | null;
  plant_name_latin?: string | null;
  part?: string | null;
  extraction?: string | null;
  aroma_profile?: string | null;
  uses_count: number;
};
export const OILS_PAGE = 200;
/** Масла по алфавиту; q ищет по имени, латыни и растению-источнику. */
export const getOils = (limit: number, offset = 0, q?: string) =>
  getJson<{ items: OilItem[]; total: number }>(`/oils${qs({ q, limit, offset })}`, q ? 3600 : 21600, 15000);

/** Масла с самым большим числом записей о применении: для витрины на хабе.
 *  Список отдаётся по алфавиту, поэтому берём его целиком (до 2 тыс.) страницами по 500. */
export async function getTopOils(n = 24): Promise<OilItem[] | null> {
  const first = await getOils(500, 0);
  if (!first) return null;
  const pages = Math.min(3, Math.ceil((first.total || 0) / 500) - 1);
  const rest = await Promise.all(Array.from({ length: Math.max(0, pages) }, (_, i) => getOils(500, (i + 1) * 500)));
  const all = [first, ...rest].flatMap((p) => p?.items ?? []);
  return all
    .filter((o) => o.name)
    .sort((a, b) => (b.uses_count || 0) - (a.uses_count || 0) || Number(!!b.plant_id) - Number(!!a.plant_id) || a.name.localeCompare(b.name, "ru"))
    .slice(0, n);
}

export type OilUse = {
  id: string;
  action?: string | null;
  action_raw?: string | null;
  indications?: string | null;
  indication_concepts: string[];
  application?: string | null;
  dosage?: string | null;
  contraindications?: string | null;
  original_text?: string | null;
};
export type OilDetail = {
  id: string;
  name: string;
  name_latin?: string | null;
  synonyms: string[];
  plant?: { id: string; name: string; name_latin?: string | null; photo_url?: string | null } | null;
  source_plant_raw?: string | null;
  part?: string | null;
  extraction?: string | null;
  aroma_profile?: string | null;
  description?: string | null;
  original_text?: string | null;
  uses: OilUse[];
};
export const getOil = cache((id: string) => getEntity<OilDetail>(`/oils/${id}`, 21600, 15000));

// ---------- системы организма ----------
/** Подписи систем организма. Мелкие близнецы из словаря («глаз», «глаза», «зрение»)
 *  сводятся в одну группу только на экране, данные не меняются. */
const SYSTEM_ALIAS: Record<string, string> = {
  глаз: "зрение", глаза: "зрение", иммунная: "иммунитет", иммунные: "иммунитет",
  ухо: "ЛОР", костная: "опорно-двигательная", рот: "ЖКТ", онкология: "прочее",
};
export const SYSTEM_RU: Record<string, string> = {
  дыхание: "Дыхание",
  ЖКТ: "Желудок и кишечник",
  ССС: "Сердце и сосуды",
  ЦНС: "Нервная система",
  мочеполовая: "Мочеполовая система",
  кожа: "Кожа",
  инфекции: "Инфекции",
  обмен: "Обмен веществ",
  кровь: "Кровь",
  печень: "Печень",
  выделение: "Выделение",
  токсины: "Отравления",
  иммунитет: "Иммунитет",
  "опорно-двигательная": "Кости и суставы",
  зрение: "Глаза и зрение",
  эндокринная: "Эндокринная система",
  ЛОР: "Ухо, горло, нос",
  наружное: "Наружное действие",
  общее: "Общее действие",
  прочее: "Прочее",
};
export const systemKey = (s?: string | null) => (s ? SYSTEM_ALIAS[s] ?? s : "прочее");
export const systemLabel = (s?: string | null) => {
  const k = systemKey(s);
  return SYSTEM_RU[k] ?? k.charAt(0).toUpperCase() + k.slice(1);
};

// ---------- мелочи для текста ----------
export const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const norm = (s?: string | null) => (s || "").toLowerCase().replace(/ё/g, "е").trim();
export const sameTerm = (a?: string | null, b?: string | null) => !!a && !!b && norm(a) === norm(b);

/** Синонимы из словаря чистим для показа: без перечислений через запятую, без повторов
 *  имени и без длинных фраз, которые попали туда из распознанного текста. */
export function cleanSynonyms(list: string[] | null | undefined, exclude: (string | null | undefined)[] = [], max = 16): string[] {
  const out: string[] = [];
  const names = exclude.filter(Boolean).map((x) => norm(x));
  const seen = new Set(names);
  // Основы имени («мочегонн» из «мочегонное»): «мочегонным», «слабое мочегонное» и
  // «мочегонное средство» ничего нового читателю не говорят.
  const stems = names.filter((n) => n.length > 5 && !n.includes(" ")).map((n) => n.slice(0, -2));
  for (const raw of list ?? []) {
    const s = (raw || "").replace(/\s+/g, " ").trim();
    if (!s || s.length > 40 || /[,;:()]/.test(s) || / и /.test(s)) continue;
    const k = norm(s);
    if (seen.has(k) || stems.some((st) => k.includes(st))) continue;
    seen.add(k);
    out.push(s);
    if (out.length >= max) break;
  }
  return out;
}

/** Вероятность случайного совпадения словами: читателю нужен порядок, а не 7e-28. */
export function pValueText(p: number): string {
  if (!Number.isFinite(p)) return "нет данных";
  if (p < 1e-6) return "почти нулевая";
  if (p < 0.001) return "меньше 0,1 %";
  if (p < 0.01) return "меньше 1 %";
  if (p < 0.05) return "меньше 5 %";
  return `около ${Math.round(p * 100)} %`;
}

/** «в 2,3 раза», «в 3 раза», «в 5 раз». */
export function liftText(lift: number): string {
  if (!Number.isFinite(lift)) return "нет данных";
  const r = Math.round(lift * 10) / 10;
  const s = r.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
  return Number.isInteger(r) ? `в ${s} ${pluralRu(r, "раз", "раза", "раз")}` : `в ${s} раза`;
}

// ---------- группировки для списков ----------
/** Показания с записями, сгруппированные по системам организма: сначала крупные, «прочее» в конце. */
export function indicationsBySystem(vocab: IndicationTerm[] | null) {
  const by = new Map<string, IndicationTerm[]>();
  for (const i of vocab ?? []) {
    if (!i.linked_facts) continue;
    const k = systemKey(i.system);
    const list = by.get(k) ?? [];
    list.push(i);
    by.set(k, list);
  }
  return [...by.entries()]
    .map(([key, list]) => ({
      key,
      list: list.sort((a, b) => b.linked_facts - a.linked_facts || a.name.localeCompare(b.name, "ru")),
      sum: list.reduce((s, x) => s + x.linked_facts, 0),
    }))
    .sort((a, b) => Number(a.key === "прочее") - Number(b.key === "прочее") || b.sum - a.sum);
}

/** Сводная группа «небольшие классы и без класса» в адресе /compounds?class=_other. */
export const COMPOUND_OTHER = "_other";
const NO_CLASS = "без класса";
const MIN_CLASS = 3;

export type CompoundClass = { key: string; list: (CompoundLite & { cls?: string })[]; sum: number };

/** Классы веществ по числу записей. Классы меньше трёх терминов и термины без класса
 *  сведены в одну группу COMPOUND_OTHER, у каждого термина там указан его класс. */
export function compoundClasses(vocab: CompoundLite[] | null): { big: CompoundClass[]; other: CompoundClass } {
  const by = new Map<string, CompoundLite[]>();
  for (const c of vocab ?? []) {
    const k = (c.compound_class || "").trim() || NO_CLASS;
    const list = by.get(k) ?? [];
    list.push(c);
    by.set(k, list);
  }
  const byFacts = (a: CompoundLite, b: CompoundLite) => b.linked_facts - a.linked_facts || a.name.localeCompare(b.name, "ru");
  const all = [...by.entries()].map(([key, list]) => ({ key, list: list.sort(byFacts), sum: list.reduce((s, x) => s + x.linked_facts, 0) }));
  const big = all.filter((c) => c.list.length >= MIN_CLASS && c.key !== NO_CLASS).sort((a, b) => b.sum - a.sum);
  const rest = all.filter((c) => c.list.length < MIN_CLASS || c.key === NO_CLASS);
  const otherList = rest.flatMap((c) => c.list.map((t) => ({ ...t, cls: c.key === NO_CLASS ? undefined : c.key }))).sort(byFacts);
  return { big, other: { key: COMPOUND_OTHER, list: otherList, sum: otherList.reduce((s, x) => s + x.linked_facts, 0) } };
}

/** Название класса для заголовка: «азотистые_основания» → «Азотистые основания». */
export const compoundClassLabel = (k: string) =>
  k === COMPOUND_OTHER ? "Небольшие классы и вещества без класса" : cap(k.replace(/_/g, " ").trim());
