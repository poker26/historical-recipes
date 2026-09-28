// Данные раздела «Атлас» и страницы поиска.
// Список видов отдаёт общее число совпадений в заголовке X-Total-Count, а getJson заголовков
// не видит, поэтому здесь свой fetch с тем же кэшем данных Next. Рядом лежат словари, которые
// чистят грязные поля корпуса для показа (семейства, биотопы, подписи фото), и помощники
// русского текста для заголовков вида «Растения при кашле».
import { API, getJson, qs, excerpt } from "./api";

// ─── типы ответов ─────────────────────────────────────────────────────────────

export type PlantSummary = {
  id: string;
  name: string;
  name_latin?: string | null;
  name_modern?: string | null;
  names_historical?: string[] | null;
  family?: string | null;
  family_latin?: string | null;
  parts_used?: string[] | null;
  is_toxic?: boolean | null;
  kingdom?: string | null;
  photo_url?: string | null;
  photo_attribution?: string | null;
  uses_count?: number | null;
  rank?: string | null;
  safety_level?: number | null;
  deadly_twin?: string | null;
};

export type PlantQuery = {
  q?: string;
  action?: string;
  indication?: string;
  family?: string;
  kingdom?: string;
  biotope?: string;
  edibility?: string;
  edible?: boolean;
  is_toxic?: boolean;
  published?: boolean;
  sort?: "name" | "uses" | "photo" | "match";
  limit: number;
  offset?: number;
};

export type ListPage<T> = { items: T[]; total: number; ok: boolean };

export type FacetValue = { value: string; count: number };
export type PlantFacets = { compound_groups: FacetValue[]; actions: FacetValue[]; edibility: FacetValue[]; kingdom: FacetValue[] };
export type BiotopeFacet = { key: string; group: string; count: number };

export type SuggestPlant = {
  id: string;
  name: string;
  name_latin?: string | null;
  name_modern?: string | null;
  photo_url?: string | null;
  kingdom?: string | null;
  rank?: string | null;
  uses?: number | null;
};
export type SuggestIndication = { id: string; name: string; name_modern?: string | null; system?: string | null; facts?: number | null };
export type Suggest = {
  q: string;
  plants: SuggestPlant[];
  indications: SuggestIndication[];
  actions: { name: string; plants: number }[];
  books: { id: string; title: string; author?: string | null; year?: number | null }[];
};

export type IndicationDetail = {
  id: string;
  name: string;
  name_modern?: string | null;
  parent?: { id: string; name: string } | null;
  parent_id?: string | null;
  system?: string | null;
  synonyms?: string[] | null;
  archaic?: string[] | null;
  definition?: string | null;
  children?: { id: string; name: string }[] | null;
  plants?: { id: string; name: string; name_latin?: string | null; parts?: string[]; raw_indications?: string[] }[] | null;
};

export type RecipeBrief = {
  id: string;
  book_id?: string | null;
  book_title?: string | null;
  book_author?: string | null;
  book_year?: number | null;
  name?: string | null;
  category?: string | null;
  recipe_kind?: string | null;
  home_doable?: boolean | null;
  step_by_step?: boolean | null;
  excerpt?: string | null;
  year?: number | null;
};

export type BookHit = {
  id: string;
  title: string;
  author?: string | null;
  year?: number | null;
  domain?: string | null;
  pages?: number | null;
  plants?: number | null;
  recipes?: number | null;
  home_recipes?: number | null;
  uses?: number | null;
  access?: "open" | "cited" | "closed" | null;
  has_cover?: boolean | null;
};

export type SectionHit = {
  id: string;
  score?: number;
  payload?: {
    book_id?: string | null;
    book_title?: string | null;
    author?: string | null;
    year?: number | null;
    section_type?: string | null;
    title?: string | null;
    content?: string | null;
    domain?: string | null;
  } | null;
};

// ─── запросы ──────────────────────────────────────────────────────────────────

/** GET списка и общего числа совпадений из X-Total-Count. Таймаут 15 с; при сбое пусто и 0. */
async function getList<T>(path: string, revalidate: number, timeoutMs = 15000): Promise<ListPage<T>> {
  try {
    const res = await fetch(API + path, { next: { revalidate }, signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return { items: [], total: 0, ok: false };
    const data: unknown = await res.json();
    const items = Array.isArray(data) ? (data as T[]) : [];
    const header = Number(res.headers.get("x-total-count"));
    const total = Number.isFinite(header) && header >= items.length ? header : items.length;
    return { items, total, ok: true };
  } catch {
    return { items: [], total: 0, ok: false };
  }
}

export const listPlants = (query: PlantQuery, revalidate = 600) =>
  getList<PlantSummary>(`/plants/${qs(query)}`, revalidate);

const byUses = (a: PlantSummary, b: PlantSummary) =>
  (b.uses_count ?? 0) - (a.uses_count ?? 0) || a.name.localeCompare(b.name, "ru");

/** Объединить выдачи без дублей, самые богатые карточки сверху. */
export function mergePlants(pages: ListPage<PlantSummary>[], limit: number): PlantSummary[] {
  const seen = new Map<string, PlantSummary>();
  for (const p of pages) for (const it of p.items) if (!seen.has(it.id)) seen.set(it.id, it);
  return Array.from(seen.values()).sort(byUses).slice(0, limit);
}

/** Корпус пишет то «отеки», то «отёки», а поиск бэкенда букву ё к е не сводит.
 *  Если в значении есть ё, спрашиваем оба написания и объединяем выдачу.
 *  `value` — написание с большим числом совпадений: его ставим в ссылку «все в атласе». */
export async function listPlantsAnySpelling(
  query: PlantQuery,
  key: "indication" | "action" | "q",
  value: string,
  revalidate = 600,
): Promise<ListPage<PlantSummary> & { value: string }> {
  const plain = value.replace(/ё/g, "е").replace(/Ё/g, "Е");
  if (plain === value) return { ...(await listPlants({ ...query, [key]: value }, revalidate)), value };
  const [a, b] = await Promise.all([value, plain].map((v) => listPlants({ ...query, [key]: v }, revalidate)));
  const items = mergePlants([a, b], query.limit);
  return {
    items,
    total: Math.max(items.length, a.total, b.total),
    ok: a.ok || b.ok,
    value: b.total > a.total ? plain : value,
  };
}

export const getPlantFacets = () => getJson<PlantFacets>(`/plants/facets`, 3600, 15000);
/** Семейства карточек атласа. Берём 200, а не 60: у грибов семейства мелкие и в первые 60 не попадают. */
export const getFamilies = (limit = 200) => getJson<{ families: FacetValue[] }>(`/plants/families?limit=${limit}`, 3600);
export const getBiotopes = () => getJson<{ biotopes: BiotopeFacet[] }>(`/plants/biotopes`, 3600);

export function getSuggest(q: string, limit = 12): Promise<Suggest | null> {
  const s = q.trim().slice(0, 80);
  if (s.length < 2) return Promise.resolve(null);
  return getJson<Suggest>(`/plants/suggest${qs({ q: s, limit })}`, 600);
}

export const getIndication = (id: string) =>
  getJson<IndicationDetail>(`/medical/indications/${encodeURIComponent(id)}`, 3600);

export const searchRecipes = (q: string, limit = 12) =>
  getList<RecipeBrief>(`/recipes/${qs({ q, home_doable: true, brief: true, limit, sort: "quality" })}`, 600);

export const searchBooks = (q: string, limit = 8) =>
  getJson<{ total: number; items: BookHit[] }>(`/library/books${qs({ q, limit })}`, 600);

/** Смысловой поиск по фрагментам книг (POST, без кэша, таймаут 12 с). null при любой ошибке. */
export async function searchSections(query: string, limit = 6): Promise<SectionHit[] | null> {
  try {
    const res = await fetch(`${API}/search/`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ query, collection: "sections_v1", mode: "hybrid", limit }),
      cache: "no-store",
      signal: AbortSignal.timeout(12000),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { results?: SectionHit[] } | null;
    return data && Array.isArray(data.results) ? data.results : null;
  } catch {
    return null;
  }
}

// ─── имена, семейства, биотопы ───────────────────────────────────────────────

/** Сравнение без регистра, буквы ё и лишних пробелов. */
export const norm = (s?: string | null) => (s ?? "").toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();

/** Имя для плитки: без перечня синонимов после запятой или скобки и с заглавной буквы.
 *  «Пижма обыкновенная (Глистник, Дикая рябина…)» → «Пижма обыкновенная». */
export function displayName(name?: string | null): string {
  let n = (name ?? "").replace(/\s+/g, " ").trim();
  const cut = n.search(/\s*[(,;]/);
  if (cut >= 3) n = n.slice(0, cut).trim();
  return n ? n.charAt(0).toUpperCase() + n.slice(1) : "Без названия";
}

/** Современное имя, если оно действительно другое (а не та же строка с ё). */
export function modernName(p: { name: string; name_modern?: string | null }): string | null {
  if (!p.name_modern) return null;
  const m = displayName(p.name_modern);
  return norm(m) !== norm(displayName(p.name)) ? m : null;
}

const FAMILY_RU: Record<string, string> = {
  Rosaceae: "розоцветные", Ranunculaceae: "лютиковые", Compositae: "сложноцветные", Asteraceae: "астровые",
  Gramineae: "злаки", Poaceae: "злаки", Cyperaceae: "осоковые", Liliaceae: "лилейные", Fabaceae: "бобовые",
  Leguminosae: "бобовые", Polygonaceae: "гречишные", Scrophulariaceae: "норичниковые", Caryophyllaceae: "гвоздичные",
  Euphorbiaceae: "молочайные", Lamiaceae: "яснотковые", Labiatae: "губоцветные", Apiaceae: "зонтичные",
  Umbelliferae: "зонтичные", Orchidaceae: "орхидные", Chenopodiaceae: "маревые", Ericaceae: "вересковые",
  Salicaceae: "ивовые", Solanaceae: "паслёновые", Malvaceae: "мальвовые", Primulaceae: "первоцветные",
  Brassicaceae: "капустные", Cruciferae: "крестоцветные", Crassulaceae: "толстянковые", Onagraceae: "кипрейные",
  Papaveraceae: "маковые", Caprifoliaceae: "жимолостные", Betulaceae: "берёзовые", Amaryllidaceae: "амариллисовые",
  Rutaceae: "рутовые", Dioscoreaceae: "диоскорейные", Campanulaceae: "колокольчиковые", Geraniaceae: "гераниевые",
  Boraginaceae: "бурачниковые", Convolvulaceae: "вьюнковые", Araceae: "ароидные", Cucurbitaceae: "тыквенные",
  Plantaginaceae: "подорожниковые", Rhamnaceae: "крушиновые", Myrtaceae: "миртовые", Iridaceae: "ирисовые",
  Araliaceae: "аралиевые", Saxifragaceae: "камнеломковые", Pinaceae: "сосновые", Equisetaceae: "хвощовые",
  Violaceae: "фиалковые", Rubiaceae: "мареновые", Gentianaceae: "горечавковые", Lycopodiaceae: "плауновые",
  Asclepiadaceae: "ластовневые", Urticaceae: "крапивные", Hypericaceae: "зверобойные", Valerianaceae: "валериановые",
  Linaceae: "льновые", Fagaceae: "буковые", Aceraceae: "кленовые", Sapindaceae: "сапиндовые", Oleaceae: "маслиновые",
  Grossulariaceae: "крыжовниковые", Juglandaceae: "ореховые", Vitaceae: "виноградовые", Cannabaceae: "коноплёвые",
  Moraceae: "тутовые", Paeoniaceae: "пионовые", Berberidaceae: "барбарисовые", Cupressaceae: "кипарисовые",
  Taxaceae: "тисовые", Polypodiaceae: "многоножковые", Dryopteridaceae: "щитовниковые", Nymphaeaceae: "кувшинковые",
  Typhaceae: "рогозовые", Alismataceae: "частуховые", Juncaceae: "ситниковые", Asparagaceae: "спаржевые",
  Amaranthaceae: "амарантовые", Portulacaceae: "портулаковые", Thymelaeaceae: "волчниковые", Elaeagnaceae: "лоховые",
  Cornaceae: "кизиловые", Tiliaceae: "липовые", Hippocastanaceae: "конскокаштановые", Anacardiaceae: "сумаховые",
  Zygophyllaceae: "парнолистниковые", Lauraceae: "лавровые", Piperaceae: "перечные", Zingiberaceae: "имбирные",
  Theaceae: "чайные", Lythraceae: "дербенниковые", Fumariaceae: "дымянковые", Menyanthaceae: "вахтовые",
  Polygalaceae: "истодовые", Oxalidaceae: "кисличные", Balsaminaceae: "бальзаминовые", Ulmaceae: "вязовые",
  Aristolochiaceae: "кирказоновые", Santalaceae: "санталовые", Ephedraceae: "эфедровые", Magnoliaceae: "магнолиевые",
  Cactaceae: "кактусовые", Arecaceae: "пальмовые", Palmae: "пальмовые", Verbenaceae: "вербеновые",
  Apocynaceae: "кутровые", Dipsacaceae: "ворсянковые", Adoxaceae: "адоксовые", Cistaceae: "ладанниковые",
  Plumbaginaceae: "свинчатковые", Pyrolaceae: "грушанковые", Empetraceae: "водяниковые", Orobanchaceae: "заразиховые",
  Colchicaceae: "безвременниковые", Alliaceae: "луковые", Convallariaceae: "ландышевые", Melanthiaceae: "мелантиевые",
  Sparganiaceae: "ежеголовниковые", Lemnaceae: "рясковые", Potamogetonaceae: "рдестовые", Punicaceae: "гранатовые",
  Clusiaceae: "клузиевые", Guttiferae: "зверобойные", Gesneriaceae: "геснериевые", Acanthaceae: "акантовые",
  // грибы и лишайники
  Tricholomataceae: "рядовковые", Russulaceae: "сыроежковые", Boletaceae: "болетовые", Polyporaceae: "трутовиковые",
  Agaricaceae: "шампиньоновые", Strophariaceae: "строфариевые", Amanitaceae: "мухоморовые", Cortinariaceae: "паутинниковые",
  Morchellaceae: "сморчковые", Suillaceae: "маслёнковые", Lycoperdaceae: "дождевиковые", Psathyrellaceae: "псатирелловые",
  Physalacriaceae: "физалакриевые", Paxillaceae: "свинушковые", Hygrophoraceae: "гигрофоровые", Cantharellaceae: "лисичковые",
  Helvellaceae: "гельвелловые", Pleurotaceae: "вёшенковые", Hydnaceae: "ежовиковые", Fomitopsidaceae: "фомитопсисовые",
  Ganodermataceae: "ганодермовые", Clavariaceae: "рогатиковые", Tremellaceae: "дрожалковые", Auriculariaceae: "аурикуляриевые",
  Hymenochaetaceae: "гименохетовые", Gomphidiaceae: "мокруховые", Cladoniaceae: "кладониевые", Parmeliaceae: "пармелиевые",
  Coprinaceae: "навозниковые", Nidulariaceae: "гнездовковые", Ustilaginaceae: "головнёвые", Sclerodermataceae: "ложнодождевиковые",
  Phallaceae: "весёлковые", Clavicipitaceae: "спорыньёвые", Sparassidaceae: "спарассисовые", Fistulinaceae: "фистулиновые",
  Entolomataceae: "энтоломовые", Pluteaceae: "плютеевые", Marasmiaceae: "негниючниковые", Mycenaceae: "миценовые",
  Hericiaceae: "герициевые", Tuberaceae: "трюфелевые", Pezizaceae: "пецицевые", Geastraceae: "звездовиковые",
  Albatrellaceae: "альбатрелловые", Coriolaceae: "кориоловые", Exidiaceae: "эксидиевые", Hypocreaceae: "гипокрейные",
};

const FUNGAL_FAMILIES = new Set([
  "Tricholomataceae", "Russulaceae", "Boletaceae", "Polyporaceae", "Agaricaceae", "Strophariaceae", "Amanitaceae",
  "Cortinariaceae", "Morchellaceae", "Suillaceae", "Lycoperdaceae", "Psathyrellaceae", "Physalacriaceae", "Paxillaceae",
  "Hygrophoraceae", "Cantharellaceae", "Helvellaceae", "Pleurotaceae", "Hydnaceae", "Fomitopsidaceae", "Ganodermataceae",
  "Clavariaceae", "Tremellaceae", "Auriculariaceae", "Hymenochaetaceae", "Gomphidiaceae", "Cladoniaceae", "Parmeliaceae",
  "Coprinaceae", "Nidulariaceae", "Ustilaginaceae", "Sclerodermataceae", "Phallaceae", "Clavicipitaceae", "Sparassidaceae",
  "Fistulinaceae", "Entolomataceae", "Pluteaceae", "Marasmiaceae", "Mycenaceae", "Hericiaceae", "Tuberaceae", "Pezizaceae",
  "Geastraceae", "Albatrellaceae", "Coriolaceae", "Exidiaceae", "Hypocreaceae", "Poriaceae", "Phaeolaceae",
  "Sarcosomataceae", "Sarcodontaceae", "Bolbitiaceae", "Inocybaceae", "Meruliaceae", "Gyroporaceae", "Stereaceae",
  "Thelephoraceae", "Laetiporaceae", "Schizophyllaceae", "Xylariaceae", "Usneaceae", "Physciaceae", "Lyophyllaceae",
  "Omphalotaceae", "Cordycipitaceae", "Ophiocordycipitaceae", "Sarcoscyphaceae", "Gomphaceae",
]);
const FUNGAL_RU = new Set<string>([
  ...Array.from(FUNGAL_FAMILIES).map((k) => FAMILY_RU[k]).filter((v): v is string => !!v),
  "трубчатые",
  "пластинчатые",
]);

/** Латинское семейство из грязной строки: «ROSACEAE Juss.» → «Rosaceae»; иначе null. */
export function latinFamily(s?: string | null): string | null {
  if (!s) return null;
  const w = s.trim().split(/[\s(,.;]/)[0].replace(/[^A-Za-z]/g, "");
  if (w.length < 6) return null;
  const t = w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
  return /ae$/.test(t) ? t : null;
}

/** Русское семейство из грязной строки: «семейство сложноцветных» → «сложноцветные»; мусор → null. */
export function russianFamily(s?: string | null): string | null {
  if (!s) return null;
  let t = s.toLowerCase().replace(/^семейство\s+/, "").replace(/\s*\(.*$/, "").split(",")[0].trim();
  if (!/^[а-яё-]{4,}$/.test(t)) return null;
  if (/ых$/.test(t)) t = t.slice(0, -2) + "ые";
  else if (/их$/.test(t)) t = t.slice(0, -2) + "ие";
  return /(ые|ие)$/.test(t) || t === "злаки" ? t : null;
}

/** Семейство для подписи плитки: по-русски, если известно, иначе латынью. */
export function familyShort(p: { family?: string | null; family_latin?: string | null }): string | null {
  const lat = latinFamily(p.family_latin) ?? latinFamily(p.family);
  if (lat && FAMILY_RU[lat]) return FAMILY_RU[lat];
  return russianFamily(p.family) ?? lat;
}

export type FamilyFacet = { key: string; label: string; latin: string | null; count: number; fungal: boolean };

/** Фасет семейств: варианты написания одного семейства сливаются, мусор распознавания отбрасывается.
 *  Ключ уходит в `family=` (бэкенд ищет подстрокой по русскому и латинскому полю). */
export function familyFacets(values: FacetValue[]): FamilyFacet[] {
  const map = new Map<string, FamilyFacet>();
  for (const v of values) {
    const lat = latinFamily(v.value);
    const ru = lat ? null : russianFamily(v.value);
    const key = lat ?? ru;
    if (!key) continue;
    const cur = map.get(key);
    if (cur) {
      cur.count += v.count;
      continue;
    }
    map.set(key, {
      key,
      label: lat ? FAMILY_RU[lat] ?? lat : key,
      latin: lat,
      count: v.count,
      fungal: lat ? FUNGAL_FAMILIES.has(lat) : FUNGAL_RU.has(key),
    });
  }
  // Русская строка, которая повторяет уже известное латинское семейство, остаётся только латинской.
  const known = new Set(Array.from(map.values()).filter((f) => f.latin).map((f) => f.label));
  return Array.from(map.values())
    .filter((f) => f.latin || !known.has(f.label))
    .sort((a, b) => b.count - a.count);
}

/** Ключ семейства из параметра адреса, чтобы узнать активный пункт фасета. */
export const familyKey = (value?: string | null): string | null =>
  value ? latinFamily(value) ?? russianFamily(value) ?? value : null;

/** Подпись выбранного семейства для чипа активного фильтра. */
export function familyLabel(value: string): string {
  const lat = latinFamily(value);
  if (lat) return FAMILY_RU[lat] ? `${FAMILY_RU[lat]} (${lat})` : lat;
  return russianFamily(value) ?? value;
}

const BIOTOPE_RU: Record<string, string> = {
  "лес": "лес",
  "лес лиственный": "лиственный лес",
  "лес хвойный": "хвойный лес",
  "лес смешанный": "смешанный лес",
  "опушки/поляны/вырубки/редколесье": "опушки, поляны, вырубки",
  "луг": "луга",
  "степь": "степи",
  "поле/сорное": "поля и сорные места",
  "болото/сырое": "болота и сырые места",
  "берега водоёмов": "берега водоёмов",
  "водное/прибрежное": "в воде и у воды",
  "каменистые/скалистые склоны": "каменистые склоны и скалы",
  "пески/дюны/обнажения": "пески и дюны",
  "пустыня/полупустыня": "пустыни и полупустыни",
  "солончаки/засоленное": "солончаки",
  "горы/предгорья": "горы и предгорья",
  "сады/парки": "сады и парки",
  "кустарники/заросли": "кустарники и заросли",
};
export const biotopeLabel = (key: string) => BIOTOPE_RU[key] ?? key.replace(/\//g, ", ");

export const BIOTOPE_GROUPS: { group: string; label: string }[] = [
  { group: "лес", label: "Лес" },
  { group: "открытое", label: "Открытые места" },
  { group: "влажное", label: "Вода и сырость" },
  { group: "субстрат", label: "Камни, пески, соль" },
  { group: "рельеф", label: "Горы" },
  { group: "антропогенное", label: "Рядом с человеком" },
  { group: "прочее", label: "Другое" },
];

export const KINGDOM_LABEL: Record<string, string> = { "растение": "растения", "гриб": "грибы" };

/** Где растёт, с предлогом и в нужном падеже: «на лугах», «в хвойном лесу». */
const BIOTOPE_WHERE: Record<string, string> = {
  "лес": "в лесу",
  "лес лиственный": "в лиственном лесу",
  "лес хвойный": "в хвойном лесу",
  "лес смешанный": "в смешанном лесу",
  "опушки/поляны/вырубки/редколесье": "на опушках, полянах и вырубках",
  "луг": "на лугах",
  "степь": "в степи",
  "поле/сорное": "на полях и сорных местах",
  "болото/сырое": "на болотах и в сырых местах",
  "берега водоёмов": "на берегах водоёмов",
  "водное/прибрежное": "в воде и у воды",
  "каменистые/скалистые склоны": "на каменистых склонах и скалах",
  "пески/дюны/обнажения": "на песках и дюнах",
  "пустыня/полупустыня": "в пустынях и полупустынях",
  "солончаки/засоленное": "на солончаках",
  "горы/предгорья": "в горах и предгорьях",
  "сады/парки": "в садах и парках",
  "кустарники/заросли": "в кустарниках и зарослях",
};

export function biotopeWhere(key: string): string {
  return BIOTOPE_WHERE[key] ?? `в месте «${key}»`;
}

/** Съедобность. «съедобно» бэкенд ищет подстрокой и зацепил бы «несъедобно»,
 *  поэтому этот пункт идёт через флаг edible=true (съедобно или условно съедобно). */
export const EDIBILITY_OPTIONS: { value: string; label: string; param: "edible" | "edibility"; paramValue: string }[] = [
  { value: "съедобно", label: "съедобно или условно съедобно", param: "edible", paramValue: "true" },
  { value: "условно-съедобно", label: "условно съедобно", param: "edibility", paramValue: "условно-съедобно" },
  { value: "несъедобно", label: "несъедобно", param: "edibility", paramValue: "несъедобно" },
  { value: "ядовито", label: "ядовито", param: "edibility", paramValue: "ядовито" },
];


/** Частые запросы «растения при…», те же, что на главной. */
export const POPULAR_CONDITIONS = ["кашель", "отёки", "бессонница", "раны", "желудок", "простуда", "ревматизм", "головная боль"];

// ─── безопасность и подписи фото ─────────────────────────────────────────────

export type SafetyKind = "danger" | "warn" | "ok" | "unknown";

/** Отметка безопасности по правилам сайта: уровень L0–L4 главнее флага ядовитости,
 *  молчание источников никогда не выдаётся за «безопасно». */
export function safetyInfo(p: { safety_level?: number | null; is_toxic?: boolean | null }): { kind: SafetyKind; label: string } {
  const l = p.safety_level;
  if (l === 4) return { kind: "danger", label: "смертельно ядовито" };
  if (l === 3) return { kind: "warn", label: "ядовито в больших дозах" };
  if (p.is_toxic && (l == null || l === 0)) return { kind: "danger", label: "ядовито" };
  if (l === 2) return { kind: "warn", label: "условно съедобно" };
  if (l === 1) return { kind: "ok", label: "съедобно" };
  return { kind: "unknown", label: "о съедобности данных нет" };
}

export type TileTag = { label: string; tone?: "warn" | "mist" };

/** Чипы плитки: гриб, род, одна отметка опасности (самая сильная), «без фото». */
export function plantTags(p: PlantSummary, opts: { noPhoto?: boolean } = {}): TileTag[] {
  const t: TileTag[] = [];
  if (p.kingdom === "гриб") t.push({ label: "гриб" });
  if (p.rank === "genus") t.push({ label: "род" });
  if (p.safety_level === 4) t.push({ label: "смертельно ядовито", tone: "warn" });
  else if (p.safety_level === 3) t.push({ label: "ядовито в больших дозах", tone: "warn" });
  else if (p.is_toxic && (p.safety_level == null || p.safety_level === 0)) t.push({ label: "ядовито", tone: "warn" });
  if (opts.noPhoto && !p.photo_url) t.push({ label: "без фото", tone: "mist" });
  return t;
}

/** Короткая подпись фото для плитки: «© автор, CC BY-NC». Полная строка уходит в title.
 *  Форматы корпуса: iNaturalist «(c) Имя, some rights reserved (CC BY)», «no rights reserved,
 *  uploaded by …» и Викимедиа «Автор, лицензия, Викимедиа». */
export function creditShort(attribution?: string | null): string | null {
  const a = (attribution ?? "").replace(/\s+/g, " ").trim();
  if (!a) return null;
  let m = a.match(/^\(c\)\s*(.+?),\s*(?:some|all) rights reserved(?:\s*\(([^)]+)\))?/i);
  if (m) return `© ${m[1].trim()}${m[2] ? `, ${m[2].trim()}` : ""}`;
  m = a.match(/^no rights reserved(?:,\s*uploaded by (.+))?$/i);
  if (m) return m[1] ? `${m[1].trim()}, CC0` : "CC0, без ограничений";
  if (/,\s*Викимедиа$/.test(a)) return a.replace(/,\s*Викимедиа$/, "");
  return a.length > 70 ? a.slice(0, 67) + "…" : a;
}

/** Откуда снимок: у iNaturalist своя форма строки прав, у Викимедиа приписка в конце. */
export function creditSource(attribution?: string | null): string | null {
  const a = (attribution ?? "").trim();
  if (/Викимедиа$/.test(a)) return "Викимедиа";
  if (/^\(c\)|rights reserved/i.test(a)) return "iNaturalist";
  return null;
}

// ─── поиск ───────────────────────────────────────────────────────────────────

/** Ранжировать карточки под запрос: совпадение с начала имени, потом с начала слова,
 *  потом внутри имени, потом только по старым именам (их показываем подписью). */
export function rankForQuery(items: PlantSummary[], q: string): { p: PlantSummary; oldName: string | null }[] {
  const nq = norm(q);
  return items
    .map((p, i) => {
      const names = [p.name, p.name_modern, p.name_latin].map(norm).filter(Boolean);
      let rank = 3;
      if (names.some((n) => n.startsWith(nq))) rank = 0;
      else if (names.some((n) => n.split(/[\s-]+/).some((w) => w.startsWith(nq)))) rank = 1;
      else if (names.some((n) => n.includes(nq))) rank = 2;
      const oldName = rank === 3 ? (p.names_historical ?? []).find((h) => norm(h).includes(nq)) ?? null : null;
      return { p, i, rank, oldName };
    })
    .sort((a, b) => a.rank - b.rank || a.i - b.i)
    .map(({ p, oldName }) => ({ p, oldName }));
}

/** Вид-«ответ» для строки поиска: из подсказок, чьё имя начинается с запроса, берём
 *  карточку с фото и самым большим числом записей (дубли корпуса так уходят вниз). */
export function pickAnswer(s: Suggest | null, q: string): SuggestPlant | null {
  const nq = norm(q);
  if (!nq) return null;
  const cands = (s?.plants ?? []).filter((p) => [p.name, p.name_modern, p.name_latin].some((n) => norm(n).startsWith(nq)));
  if (!cands.length) return null;
  return cands
    .slice()
    .sort((a, b) => Number(!!b.photo_url) - Number(!!a.photo_url) || (b.uses ?? 0) - (a.uses ?? 0))[0];
}

/** Фрагмент книги для выдачи: без разметки, переносов и мягких дефисов, до max знаков. */
export function cleanFragment(s?: string | null, max = 200): string {
  const t = (s ?? "")
    .replace(/­\s*/g, "")
    .replace(/([A-Za-zА-Яа-яЁё])-\s*\n\s*([a-zа-яё])/g, "$1$2")
    .replace(/[*#_`|>]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return excerpt(t, max);
}

// ─── русский текст: «растения при кашле», «с мочегонным действием» ──────────

const PREP_WORD: Record<string, string> = {
  "кашель": "кашле",
  "ячмень": "ячмене",
  "лишай": "лишае",
  "мигрень": "мигрени",
  "желудок": "болезнях желудка",
};
const PREP_PHRASE: Record<string, string> = {
  "печень": "болезнях печени",
  "почки": "болезнях почек",
  "сердце": "болезнях сердца",
  "кожа": "болезнях кожи",
  "глаза": "болезнях глаз",
  "суставы": "болезнях суставов",
  "легкие": "болезнях лёгких",
  "горло": "болезнях горла",
  "зубы": "болезнях зубов",
  "кишечник": "болезнях кишечника",
  "нервы": "нервных болезнях",
  "мочевой пузырь": "болезнях мочевого пузыря",
  "желчный пузырь": "болезнях желчного пузыря",
};

function nounPrep(w: string): string | null {
  const lw = w.toLowerCase();
  const special = PREP_WORD[lw] ?? PREP_WORD[lw.replace(/ё/g, "е")];
  if (special) return special;
  if (w.length > 1 && w === w.toUpperCase() && /[А-ЯЁ]/.test(w)) return w; // аббревиатура: ОРВИ
  if (!/^[а-яё-]+$/.test(lw)) return null;
  if (/[гкхжшчщ]и$/.test(lw) || /ы$/.test(lw)) return w.slice(0, -1) + "ах"; // отёки → отёках, раны → ранах
  if (/и$/.test(lw)) return w.slice(0, -1) + "ях"; // боли → болях, мозоли → мозолях
  if (/(ия|ие)$/.test(lw)) return w.slice(0, -1) + "и"; // пневмония → пневмонии, воспаление → воспалении
  if (/ье$/.test(lw)) return w; // удушье
  if (/[ая]$/.test(lw)) return w.slice(0, -1) + "е"; // простуда → простуде, водянка → водянке
  if (/ь$/.test(lw)) return w.slice(0, -1) + "и"; // боль → боли
  if (/[йо]$/.test(lw)) return w.slice(0, -1) + "е"; // геморрой → геморрое
  if (/[бвгджзклмнпрстфхцчшщ]$/.test(lw)) return w + "е"; // ревматизм → ревматизме
  return null;
}

const isAdjSingular = (w: string) => /(ая|яя|ый|ой|ий|ое|ее)$/i.test(w);
const isAdjPlural = (w: string) => /(ые|[гкхжшчщ]ие|нние)$/i.test(w);

function adjPrep(w: string): string | null {
  const lw = w.toLowerCase();
  if (/ые$/.test(lw)) return w.slice(0, -2) + "ых";
  if (/ие$/.test(lw)) return w.slice(0, -2) + "их";
  if (/[жшчщ]ая$/.test(lw)) return w.slice(0, -2) + "ей";
  if (/ая$/.test(lw)) return w.slice(0, -2) + "ой";
  if (/яя$/.test(lw)) return w.slice(0, -2) + "ей";
  if (/[гкх]ий$/.test(lw)) return w.slice(0, -2) + "ом";
  if (/ий$/.test(lw)) return w.slice(0, -2) + "ем";
  if (/(ый|ой|ое)$/.test(lw)) return w.slice(0, -2) + "ом";
  if (/ее$/.test(lw)) return w.slice(0, -2) + "ем";
  return null;
}

/** Предложный падеж состояния после «при»: «кашель» → «кашле», «головная боль» → «головной боли».
 *  Не уверены в форме — null, и заголовок строится без склонения. */
export function prepositionalRu(phrase: string): string | null {
  const s = phrase.replace(/\s+/g, " ").trim();
  if (!s) return null;
  const key = norm(s);
  if (PREP_PHRASE[key]) return PREP_PHRASE[key];
  const words = s.split(" ");
  if (words.length === 1) return nounPrep(words[0]);
  if (isAdjPlural(words[0]) || isAdjSingular(words[0])) {
    const adj = adjPrep(words[0]);
    const noun = nounPrep(words[1]);
    return adj && noun ? [adj, noun, ...words.slice(2)].join(" ") : null;
  }
  const head = nounPrep(words[0]);
  return head ? [head, ...words.slice(1)].join(" ") : null;
}

/** Действие в творительном: «мочегонное» → «мочегонным», «возбуждающее аппетит» → «возбуждающим аппетит». */
export function actionInstrumental(action: string): string | null {
  const [first = "", ...rest] = action.replace(/\s+/g, " ").trim().split(" ");
  const lw = first.toLowerCase();
  let f: string | null = null;
  if (/[гкх]ое$/.test(lw)) f = first.slice(0, -2) + "им";
  else if (/ое$/.test(lw)) f = first.slice(0, -2) + "ым";
  else if (/ее$/.test(lw)) f = first.slice(0, -2) + "им";
  return f ? [f, ...rest].join(" ") : null;
}

/** «при кашле», «при головной боли», «с мочегонным действием», «со спазмолитическим действием». */
export function conditionPhrase(x: string, isAction: boolean): string {
  const s = x.replace(/\s+/g, " ").trim();
  if (isAction) {
    const instr = actionInstrumental(s.toLowerCase());
    if (instr) return `${/^[сзшжщ][^аеёиоуыэюя]/i.test(instr) ? "со" : "с"} ${instr} действием`;
    return `с действием «${s}»`;
  }
  // Заглавные буквы убираем («Кашель» → «при кашле»), аббревиатуры оставляем («ОРВИ»).
  const lower = s
    .split(" ")
    .map((w) => (w.length > 1 && w === w.toUpperCase() && /[А-ЯЁ]/.test(w) ? w : w.toLowerCase()))
    .join(" ");
  const prep = prepositionalRu(lower);
  return prep ? `при ${prep}` : `при состоянии «${s}»`;
}
