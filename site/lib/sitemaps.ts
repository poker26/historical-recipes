// Карта сайта: индекс /sitemap.xml и файлы /sitemaps/<имя>.xml. Свои обработчики вместо
// generateSitemaps, потому что Next 14.2 кладёт такие карты по разным адресам в разработке
// и в сборке. Данные кэшируются через revalidate в getJson.
//
// В карту идут только страницы, которые сами просятся в индекс: карточки с содержанием
// (/plants/sitemap?substantive=true), рецепты с текстом от 200 знаков, страницы открытых
// книг, с которых в атлас вошли факты. Адрес в карте пишется так же, как канонический
// адрес страницы, иначе поисковик считает их разными страницами.
import { getJson, plantHref, SITE_URL } from "./api";
import { familyFacets, type FacetValue } from "./api-atlas";
import { biotopeHref } from "./api-reference";
import { MONTH_SLUGS } from "./season";
import { atlasHref } from "../components/atlas/params";
import { SAFETY_LANDINGS } from "../components/atlas/SafetyLanding";

const CHUNK = 5000;
const DAY = 86400;
const SIX_HOURS = 21600;
const SLOW = 60000;

type Entry = { loc: string; lastmod?: string | null; changefreq?: string; priority?: number };
type Dated = { id: string; name?: string; name_latin?: string | null; lastmod?: string | null; created_at?: string | null };
type LibrarySitemap = { books: { id: string; access: string; updated_at: string | null }[]; pages: { book_id: string; page: number }[] };

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");
}

/** Путь как в адресной строке: кириллица кодируется, поисковики ждут ASCII в <loc>. */
const url = (path: string) => SITE_URL + encodeURI(path);
/** Путь, уже закодированный так же, как в каноническом адресе страницы. */
const urlAsIs = (path: string) => SITE_URL + path;

/** Дата для <lastmod>: только настоящая дата изменения, без неё тег не пишем. */
const day = (iso?: string | null) => (iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? iso.slice(0, 10) : null);

export function urlset(entries: Entry[]): string {
  const body = entries
    .map(
      (e) =>
        `<url><loc>${esc(e.loc)}</loc>` +
        (e.lastmod ? `<lastmod>${e.lastmod}</lastmod>` : "") +
        (e.changefreq ? `<changefreq>${e.changefreq}</changefreq>` : "") +
        (e.priority != null ? `<priority>${e.priority.toFixed(1)}</priority>` : "") +
        `</url>`,
    )
    .join("");
  return `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${body}</urlset>`;
}

/** Индекс карт. Дату у файлов не пишем: общая «сейчас» у всех файлов была бы неправдой. */
export function sitemapIndex(names: string[]): string {
  const body = names.map((n) => `<sitemap><loc>${esc(`${SITE_URL}/sitemaps/${n}.xml`)}</loc></sitemap>`).join("");
  return `<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${body}</sitemapindex>`;
}

const plantsSitemap = (offset: number, limit: number) =>
  getJson<{ total: number; items: Dated[] }>(`/plants/sitemap?substantive=true&offset=${offset}&limit=${limit}`, SIX_HOURS, SLOW);
const recipesSitemap = (offset: number, limit: number) =>
  getJson<{ total: number; items: Dated[] }>(`/recipes/sitemap?min_text=200&offset=${offset}&limit=${limit}`, SIX_HOURS, SLOW);
const librarySitemap = () => getJson<LibrarySitemap>(`/library/sitemap`, SIX_HOURS, SLOW);

const chunks = (total: number) => Math.max(1, Math.ceil(total / CHUNK));

/** Имена файлов карты: static, books, library-pages-1…K, plants-1…N, recipes-1…M. */
export async function sitemapNames(): Promise<string[]> {
  const [p, r, lib] = await Promise.all([plantsSitemap(0, 1), recipesSitemap(0, 1), librarySitemap()]);
  const names = ["static", "books"];
  for (let i = 1; i <= chunks(lib?.pages.length ?? 0); i++) names.push(`library-pages-${i}`);
  for (let i = 1; i <= chunks(p?.total ?? 0); i++) names.push(`plants-${i}`);
  for (let i = 1; i <= chunks(r?.total ?? 0); i++) names.push(`recipes-${i}`);
  return names;
}

async function staticEntries(): Promise<Entry[]> {
  const sections = [
    "/", "/atlas", "/atlas?kingdom=гриб", "/recipes", "/recipes?kind=medicinal", "/recipes?kind=food", "/library",
    "/reference", "/indications", "/places", "/compounds", "/oils", "/leaderboard", "/season", "/about",
  ];
  const out: Entry[] = sections.map((p) => ({ loc: url(p), changefreq: "daily", priority: p === "/" ? 1 : 0.8 }));
  for (const l of SAFETY_LANDINGS) out.push({ loc: url(l.path), changefreq: "weekly", priority: 0.8 });
  MONTH_SLUGS.forEach((m) => out.push({ loc: url(`/season/${m}`), changefreq: "monthly", priority: 0.7 }));

  const [facets, biotopes, inds, families] = await Promise.all([
    getJson<{ actions: { value: string }[] }>(`/plants/facets`, DAY),
    getJson<{ biotopes: { key: string }[] }>(`/plants/biotopes`, DAY),
    getJson<{ name: string; linked_facts?: number }[]>(`/medical/indications`, DAY, SLOW),
    getJson<{ families: FacetValue[] }>(`/plants/families?limit=200`, DAY),
  ]);
  for (const a of facets?.actions ?? []) out.push({ loc: urlAsIs(`/actions/${encodeURIComponent(a.value)}`), changefreq: "weekly", priority: 0.6 });
  for (const b of biotopes?.biotopes ?? []) out.push({ loc: urlAsIs(biotopeHref(b.key)), changefreq: "weekly", priority: 0.5 });
  // Запрос «растения при кашле» закрывает страница атласа /atlas/for/кашель; справочная
  // страница показания стоит с noindex и в карту не идёт.
  const seen = new Set<string>();
  for (const i of inds ?? []) {
    if ((i.linked_facts ?? 0) < 5 || !i.name) continue;
    const path = `/atlas/for/${encodeURIComponent(i.name.toLowerCase())}`;
    if (seen.has(path)) continue;
    seen.add(path);
    out.push({ loc: urlAsIs(path), changefreq: "weekly", priority: 0.6 });
  }
  for (const f of familyFacets(families?.families ?? []).filter((x) => x.count >= 5)) {
    out.push({ loc: urlAsIs(atlasHref({}, { family: f.key })), changefreq: "weekly", priority: 0.6 });
  }
  return out;
}

export async function sitemapEntries(name: string): Promise<Entry[] | null> {
  if (name === "static") return staticEntries();
  if (name === "books") {
    const lib = await librarySitemap();
    return (lib?.books ?? []).map((b) => ({
      loc: url(`/library/${b.id}`),
      lastmod: day(b.updated_at),
      changefreq: "monthly",
      priority: b.access === "open" ? 0.7 : 0.5,
    }));
  }
  let m = name.match(/^library-pages-(\d{1,3})$/);
  if (m) {
    const lib = await librarySitemap();
    const start = (Number(m[1]) - 1) * CHUNK;
    return (lib?.pages ?? []).slice(start, start + CHUNK).map((p) => ({
      loc: url(`/library/${p.book_id}/p/${p.page}`),
      changefreq: "yearly",
      priority: 0.4,
    }));
  }
  m = name.match(/^plants-(\d{1,3})$/);
  if (m) {
    const res = await plantsSitemap((Number(m[1]) - 1) * CHUNK, CHUNK);
    return (res?.items ?? []).map((p) => ({
      loc: url(plantHref(p.id, p.name_latin)),
      lastmod: day(p.lastmod),
      changefreq: "monthly",
      priority: 0.7,
    }));
  }
  m = name.match(/^recipes-(\d{1,3})$/);
  if (m) {
    const res = await recipesSitemap((Number(m[1]) - 1) * CHUNK, CHUNK);
    return (res?.items ?? []).map((r) => ({
      loc: url(`/recipe/${r.id}`),
      lastmod: day(r.created_at),
      changefreq: "yearly",
      priority: 0.5,
    }));
  }
  return null;
}
