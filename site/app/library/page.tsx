import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../ui";
import { Empty, Pager } from "../../components/common";
import { BookTile } from "../../components/library/BookTile";
import { DEFAULT_OG, SITE_URL, fmtInt, pluralRu } from "../../lib/api";
import {
  ERAS, SHELF_LIMIT, SORTS, countBooks, domainLabel, getBooks, getLibraryStats, param,
  type LibraryStats, type ShelfQuery,
} from "../../lib/api-library";
import "./library.css";

type SP = { [key: string]: string | string[] | undefined };
type Shelf = ShelfQuery & { sort: string; page: number };

const ERA_KEYS = new Set(ERAS.map((e) => e.key));
const SORT_KEYS = new Set(SORTS.map((s) => s.key));

function parseShelf(sp: SP): Shelf {
  const domain = param(sp.domain);
  const era = param(sp.era);
  const access = param(sp.access);
  const sort = param(sp.sort);
  const page = Number(param(sp.page) ?? "1");
  return {
    q: param(sp.q)?.slice(0, 80),
    domain: domain && /^[a-z_]{2,30}$/.test(domain) ? domain : undefined,
    era: era && ERA_KEYS.has(era) ? era : undefined,
    access: access === "open" || access === "cited" ? access : undefined,
    scans: param(sp.scans) === "true" || param(sp.scans) === "1",
    sort: sort && SORT_KEYS.has(sort) ? sort : "year",
    page: Number.isInteger(page) && page >= 1 && page <= 1000 ? page : 1,
  };
}

const isFiltered = (s: Shelf) => !!(s.q || s.domain || s.era || s.access || s.scans || s.sort !== "year");

/** Параметры адреса полки без номера страницы (его добавляет Pager). */
function shelfParams(s: Shelf): Record<string, string | undefined> {
  return {
    q: s.q, domain: s.domain, era: s.era, access: s.access,
    scans: s.scans ? "true" : undefined, sort: s.sort !== "year" ? s.sort : undefined,
  };
}

/** Адрес полки с изменёнными фильтрами; любая смена фильтра сбрасывает страницу. */
function shelfHref(s: Shelf, patch: Partial<Shelf>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(shelfParams({ ...s, ...patch, page: 1 }))) if (v) p.set(k, v);
  const q = p.toString();
  return q ? `/library?${q}` : "/library";
}

function statsLine(stats: LibraryStats | null, openCount: number | null): string {
  if (!stats) return "Травники, лечебники, поваренные книги и справочники, из которых собран атлас.";
  const open = openCount ?? stats.open_books;
  const years = stats.year_min && stats.year_max ? ` с ${stats.year_min} по ${stats.year_max} год` : "";
  return `${fmtInt(stats.books)} ${pluralRu(stats.books, "книга", "книги", "книг")}${years}, ` +
    `${fmtInt(stats.books_with_scans)} со сканами страниц, ${fmtInt(open)} ${pluralRu(open, "открыта", "открыты", "открыты")} целиком.`;
}

export async function generateMetadata({ searchParams }: { searchParams: SP }): Promise<Metadata> {
  const s = parseShelf(searchParams);
  const stats = await getLibraryStats();
  const title = "Библиотека травников, лечебников и поваренных книг";
  const description = stats
    ? `${statsLine(stats, null)} Страницы старых книг можно листать целиком, а у каждого факта атласа указаны книга, год и страница.`
    : "Травники, лечебники и поваренные книги, из которых собран атлас. Страницы старых книг можно листать целиком.";
  const url = `${SITE_URL}/library`;
  return {
    title,
    description,
    alternates: { canonical: url },
    robots: isFiltered(s) || s.page > 1 ? { index: false, follow: true } : undefined,
    openGraph: { title, description, url, type: "website", images: [DEFAULT_OG] },
  };
}

function Facets({ s, stats, counts }: { s: Shelf; stats: LibraryStats | null; counts: Record<string, number | null> }) {
  const n = (v: number | null | undefined) => (v == null ? null : <span className="n">{fmtInt(v)}</span>);
  return (
    <>
      <div className="facet">
        <h4>Раздел</h4>
        <Link href={shelfHref(s, { domain: undefined })} className={!s.domain ? "active" : undefined}>
          все книги {n(stats?.books)}
        </Link>
        {(stats?.domains ?? []).map((d) => (
          <Link
            key={d.domain}
            href={shelfHref(s, { domain: s.domain === d.domain ? undefined : d.domain })}
            className={s.domain === d.domain ? "active" : undefined}
          >
            {domainLabel(d.domain)} {n(d.count)}
          </Link>
        ))}
      </div>
      <div className="facet">
        <h4>Время издания</h4>
        <Link href={shelfHref(s, { era: undefined })} className={!s.era ? "active" : undefined}>любое</Link>
        {ERAS.map((e) => (
          <Link key={e.key} href={shelfHref(s, { era: s.era === e.key ? undefined : e.key })} className={s.era === e.key ? "active" : undefined}>
            {e.label} {n(counts[e.key])}
          </Link>
        ))}
      </div>
      <div className="facet">
        <h4>Что можно открыть</h4>
        <Link href={shelfHref(s, { access: s.access === "open" ? undefined : "open" })} className={s.access === "open" ? "active" : undefined}>
          можно читать целиком {n(counts.open)}
        </Link>
        <Link href={shelfHref(s, { scans: !s.scans })} className={s.scans ? "active" : undefined}>
          со сканами страниц {n(counts.scans)}
        </Link>
      </div>
      <div className="facet">
        <h4>Порядок</h4>
        {SORTS.map((o) => (
          <Link key={o.key} href={shelfHref(s, { sort: o.key })} className={s.sort === o.key ? "active" : undefined}>{o.label}</Link>
        ))}
      </div>
    </>
  );
}

export default async function LibraryShelf({ searchParams }: { searchParams: SP }) {
  const s = parseShelf(searchParams);
  const [stats, list, pre1917, soviet, modern, unknown, open, scans] = await Promise.all([
    getLibraryStats(),
    getBooks(s, (s.page - 1) * SHELF_LIMIT),
    countBooks({ era: "pre1917" }),
    countBooks({ era: "soviet" }),
    countBooks({ era: "modern" }),
    countBooks({ era: "unknown" }),
    countBooks({ access: "open" }),
    countBooks({ scans: true }),
  ]);
  const counts = { pre1917, soviet, modern, unknown, open, scans };
  const total = list?.total ?? 0;
  const pages = Math.ceil(total / SHELF_LIMIT);
  const activeCount = [s.q, s.domain, s.era, s.access, s.scans || undefined].filter(Boolean).length;

  return (
    <>
      <Header active="/library" />
      <main>
        <section className="hero-grad lib-hero">
          <span className="chip">Библиотека</span>
          <h1>Книги, из которых собран атлас</h1>
          <p className="lead">{statsLine(stats, open)}</p>
          <p className="lib-hero-note">
            Срок авторских прав на книги, изданные до 1917 года, истёк. Их можно листать целиком по
            сканам, и рядом с каждой страницей видно, что с неё вошло в атлас. Если год издания неизвестен или книга ещё
            охраняется, открыты только номера страниц, библиографическая ссылка и цитаты, которые вошли в
            атлас. У закрытых книг доступно только описание.
          </p>
          <form className="lib-search" action="/library" method="get" role="search">
            {Object.entries(shelfParams(s)).map(([k, v]) => (k !== "q" && v ? <input key={k} type="hidden" name={k} value={v} /> : null))}
            <input type="search" name="q" defaultValue={s.q ?? ""} maxLength={80} placeholder="Название книги или автор" aria-label="Поиск по названию и автору" />
            <button type="submit" className="btn btn-primary">Найти</button>
          </form>
        </section>

        <div className="with-aside lib-shelf-wrap">
          <aside className="aside lib-aside" aria-label="Фильтры полки">
            <Facets s={s} stats={stats} counts={counts} />
          </aside>
          <div className="lib-shelf-main">
            <details className="lib-filters-mobile">
              <summary>Фильтры и порядок{activeCount ? `, выбрано ${activeCount}` : ""}</summary>
              <Facets s={s} stats={stats} counts={counts} />
            </details>

            {list ? (
              <div className="toolbar">
                <span>
                  {total
                    ? `Нашлось ${fmtInt(total)} ${pluralRu(total, "книга", "книги", "книг")}`
                    : "Ничего не нашлось"}
                  {s.q ? ` по запросу «${s.q}»` : ""}
                  {pages > 1 ? `, страница ${s.page} из ${pages}` : ""}
                </span>
                {isFiltered(s) ? <Link href="/library" className="more">сбросить фильтры</Link> : null}
              </div>
            ) : null}

            {!list ? (
              <Empty>
                Полка сейчас не загрузилась, библиотека не отвечает. Обнови страницу через минуту, а пока
                загляни в <Link href="/atlas">атлас</Link>.
              </Empty>
            ) : list.items.length ? (
              <div className="lib-shelf">
                {list.items.map((b) => <BookTile key={b.id} b={b} />)}
              </div>
            ) : (
              <Empty>
                Под такие условия в библиотеке книг нет. Убери часть фильтров или <Link href="/library">открой всю полку</Link>.
              </Empty>
            )}

            <Pager page={s.page} pages={pages} base="/library" params={shelfParams(s)} />
          </div>
        </div>
      </main>
      <Footer />
    </>
  );
}
