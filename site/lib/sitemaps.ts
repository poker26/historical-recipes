// Карта сайта: индекс /sitemap.xml и файлы /sitemaps/<имя>.xml. Свои обработчики вместо
// generateSitemaps, потому что Next 14.2 кладёт такие карты по разным адресам в разработке
// и в сборке. Данные кэшируются через revalidate в getJson.
import { getJson, plantHref, SITE_URL } from "./api";

const CHUNK = 5000;

type Entry = { loc: string; changefreq?: string; priority?: number };
type Item = { id: string; name?: string; name_latin?: string | null };

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&apos;");
}

/** Кириллица в адресе должна быть закодирована: поисковики ждут ASCII в <loc>. */
function url(path: string): string {
  return SITE_URL + encodeURI(path);
}

export function urlset(entries: Entry[]): string {
  const body = entries
    .map((e) => `<url><loc>${esc(e.loc)}</loc>${e.changefreq ? `<changefreq>${e.changefreq}</changefreq>` : ""}${e.priority != null ? `<priority>${e.priority.toFixed(1)}</priority>` : ""}</url>`)
    .join("");
  return `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${body}</urlset>`;
}

export function sitemapIndex(names: string[]): string {
  const now = new Date().toISOString();
  const body = names.map((n) => `<sitemap><loc>${esc(`${SITE_URL}/sitemaps/${n}.xml`)}</loc><lastmod>${now}</lastmod></sitemap>`).join("");
  return `<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${body}</sitemapindex>`;
}

/** Имена файлов карты: static, plants-1…N, recipes-1…M, books. */
export async function sitemapNames(): Promise<string[]> {
  const [p, r] = await Promise.all([
    getJson<{ total: number }>(`/plants/sitemap?limit=1`, 21600),
    getJson<{ total: number }>(`/recipes/sitemap?limit=1`, 21600),
  ]);
  const names = ["static", "books"];
  for (let i = 1; i <= Math.max(1, Math.ceil((p?.total ?? 0) / CHUNK)); i++) names.push(`plants-${i}`);
  for (let i = 1; i <= Math.max(1, Math.ceil((r?.total ?? 0) / CHUNK)); i++) names.push(`recipes-${i}`);
  return names;
}

export async function sitemapEntries(name: string): Promise<Entry[] | null> {
  if (name === "static") {
    const out: Entry[] = ["/", "/atlas", "/atlas?kingdom=гриб", "/recipes", "/recipes?kind=medicinal", "/recipes?kind=food", "/library", "/reference", "/indications", "/places", "/compounds", "/oils", "/leaderboard"]
      .map((p) => ({ loc: url(p), changefreq: "daily", priority: p === "/" ? 1 : 0.8 }));
    const [facets, biotopes, inds] = await Promise.all([
      getJson<{ actions: { value: string }[] }>(`/plants/facets`, 86400),
      getJson<{ biotopes: { key: string }[] }>(`/plants/biotopes`, 86400),
      getJson<{ id: string; linked_facts?: number }[]>(`/medical/indications`, 86400, 60000),
    ]);
    for (const a of facets?.actions ?? []) out.push({ loc: url(`/actions/${a.value}`), changefreq: "weekly", priority: 0.6 });
    for (const b of biotopes?.biotopes ?? []) out.push({ loc: url(`/biotopes/${b.key}`), changefreq: "weekly", priority: 0.5 });
    for (const i of (inds ?? []).filter((x) => (x.linked_facts ?? 0) >= 5)) out.push({ loc: url(`/indications/${i.id}`), changefreq: "monthly", priority: 0.5 });
    return out;
  }
  if (name === "books") {
    const res = await getJson<{ items: Item[] }>(`/library/books?limit=400&sort=year`, 21600);
    return (res?.items ?? []).map((b) => ({ loc: url(`/library/${b.id}`), changefreq: "monthly", priority: 0.6 }));
  }
  let m = name.match(/^plants-(\d{1,3})$/);
  if (m) {
    const res = await getJson<{ items: Item[] }>(`/plants/sitemap?offset=${(Number(m[1]) - 1) * CHUNK}&limit=${CHUNK}`, 21600, 30000);
    return (res?.items ?? []).map((p) => ({ loc: url(plantHref(p.id, p.name_latin)), changefreq: "monthly", priority: 0.7 }));
  }
  m = name.match(/^recipes-(\d{1,3})$/);
  if (m) {
    const res = await getJson<{ items: Item[] }>(`/recipes/sitemap?offset=${(Number(m[1]) - 1) * CHUNK}&limit=${CHUNK}`, 21600, 30000);
    return (res?.items ?? []).map((r) => ({ loc: url(`/recipe/${r.id}`), changefreq: "yearly", priority: 0.5 }));
  }
  return null;
}
