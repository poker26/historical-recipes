import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../ui";
import { Crumbs, Empty, Pager } from "../../components/common";
import { fmtInt, plantHref, pluralRu, qs } from "../../lib/api";
import { OILS_PAGE, cap, getOils } from "../../lib/api-reference";
import { pageMeta } from "../../components/reference/meta";
import "../reference/reference.css";

export const dynamic = "force-dynamic";

type SP = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || undefined;

function parse(sp: SP) {
  const n = parseInt(one(sp.page) || "1", 10);
  return { page: Number.isFinite(n) && n > 1 ? Math.min(n, 100) : 1, q: one(sp.q)?.slice(0, 80) };
}

export async function generateMetadata({ searchParams }: { searchParams: SP }): Promise<Metadata> {
  const { page, q } = parse(searchParams);
  const clean = page === 1 && !q;
  return pageMeta({
    title: clean ? "Эфирные масла: из каких растений и для чего" : `Эфирные масла${q ? `, поиск «${q}»` : ""}${page > 1 ? `, страница ${page}` : ""}`,
    description:
      "Эфирные масла по книгам: из какого растения и какой части их получают, каким способом, какой у них аромат и для чего их применяли. Это история ароматерапии, а не совет.",
    path: "/oils" + qs({ q, page: page > 1 ? page : undefined }),
    index: clean,
  });
}

export default async function OilsPage({ searchParams }: { searchParams: SP }) {
  const { page, q } = parse(searchParams);
  const data = await getOils(OILS_PAGE, (page - 1) * OILS_PAGE, q);
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / OILS_PAGE));

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { label: "Эфирные масла" }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">Эфирные масла</span>
        <h1>Эфирные масла</h1>
        <p className="lead">
          {data && !q ? `В справочнике ${fmtInt(total)} ${pluralRu(total, "масло", "масла", "масел")}. ` : ""}
          Для каждого видно, из какого растения и какой части его получают, каким способом и
          сколько записей о применении нашлось в книгах. Открой масло, чтобы прочитать цитаты.
        </p>
        <form className="rf-search" action="/oils" method="get" role="search">
          <input type="search" name="q" defaultValue={q ?? ""} placeholder="Лаванда, бергамот или Mentha" aria-label="Поиск по эфирным маслам" />
          <button type="submit" className="btn btn-primary btn-sm">Найти</button>
        </form>
      </section>

      <section className="section">
        {data ? (
          <div className="toolbar">
            <span>
              {q ? `По запросу «${q}» нашлось ${fmtInt(total)} ${pluralRu(total, "масло", "масла", "масел")}` : "Масла по алфавиту"}
              {pages > 1 ? `, страница ${fmtInt(page)} из ${fmtInt(pages)}` : ""}
            </span>
            {q ? <Link href="/oils" className="more">Показать все</Link> : null}
          </div>
        ) : null}
        {!data ? (
          <Empty>
            Список масел сейчас не загрузился. Обнови страницу через минуту или открой <Link href="/reference">справочники</Link>.
          </Empty>
        ) : items.length ? (
          <table className="table rf-table">
            <thead>
              <tr>
                <th scope="col">Масло</th>
                <th scope="col">Латынь</th>
                <th scope="col">Растение</th>
                <th scope="col">Часть</th>
                <th scope="col">Как получают</th>
                <th scope="col">Записей</th>
              </tr>
            </thead>
            <tbody>
              {items.map((o) => (
                <tr key={o.id}>
                  <td data-label="Масло"><Link href={`/oils/${o.id}`}>{cap(o.name)}</Link></td>
                  <td data-label="Латынь">{o.name_latin ? <span className="latin">{o.name_latin}</span> : <span className="muted">—</span>}</td>
                  <td data-label="Растение">
                    {o.plant_id && o.plant_name
                      ? <Link href={plantHref(o.plant_id, o.plant_name_latin)}>{o.plant_name}</Link>
                      : o.plant_name || <span className="muted">—</span>}
                  </td>
                  <td data-label="Часть">{o.part || <span className="muted">—</span>}</td>
                  <td data-label="Как получают">{o.extraction || <span className="muted">—</span>}</td>
                  <td data-label="Записей о применении">{fmtInt(o.uses_count || 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>
            {q ? `По запросу «${q}» масел не нашлось. ` : "В справочнике пока нет масел. "}
            Попробуй другое название или открой <Link href="/oils">весь список</Link>.
          </Empty>
        )}
        <Pager page={page} pages={pages} base="/oils" params={{ q }} />
      </section>

      <p className="footnote rf-foot">
        Ароматерапия по книгам, доказательная база слабая: это история применения, а не совет.
      </p>
      <Footer />
    </>
  );
}
