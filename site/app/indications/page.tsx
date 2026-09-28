import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../ui";
import { Crumbs, Empty, Pager } from "../../components/common";
import { fmtInt, pluralRu } from "../../lib/api";
import {
  getIndicationVocab, indicationsBySystem, sameTerm, systemLabel, type IndicationTerm,
} from "../../lib/api-reference";
import { pageMeta } from "../../components/reference/meta";
import "../reference/reference.css";

export const dynamic = "force-dynamic";

// Полный список показаний одной системы организма: /indications?system=ЖКТ, постранично.
// Без параметра страница показывает системы с верхушкой, как блок на /reference.
const PER_PAGE = 200;
const TOP = 12;

type SP = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || undefined;

function parse(sp: SP) {
  const n = parseInt(one(sp.page) || "1", 10);
  return { system: one(sp.system)?.slice(0, 60), page: Number.isFinite(n) && n > 1 ? Math.min(n, 100) : 1 };
}

const systemHref = (k: string) => `/indications?system=${encodeURIComponent(k)}`;
const countWord = (n: number) => `${fmtInt(n)} ${pluralRu(n, "показание", "показания", "показаний")}`;

export async function generateMetadata({ searchParams }: { searchParams: SP }): Promise<Metadata> {
  const { system, page } = parse(searchParams);
  if (!system) {
    return pageMeta({
      title: "Показания по системам организма",
      description:
        "От чего применяли растения по книгам. Показания разбиты по системам организма, у болезней указаны старые и современные названия, и каждое показание ведёт к растениям атласа.",
      path: "/indications",
    });
  }
  const label = systemLabel(system);
  return pageMeta({
    title: `${label}: показания по книгам${page > 1 ? `, страница ${page}` : ""}`,
    description: `Все показания раздела «${label}», при которых книги применяли растения. У каждого указаны старые и современные названия, и каждое ведёт к растениям атласа.`,
    path: systemHref(system) + (page > 1 ? `&page=${page}` : ""),
    index: page === 1,
  });
}

// Сотни ссылок: обычные <a> вместо Link, чтобы не раздувать данные страницы.
function IndicationList({ items }: { items: IndicationTerm[] }) {
  return (
    <ul className="rf-dict-list">
      {items.map((i, n) => (
        <li key={n}>
          <a href={`/indications/${i.id}`}>{i.name}</a>
          {i.name_modern && !sameTerm(i.name_modern, i.name) ? <span className="muted"> · {i.name_modern}</span> : null}
          <span className="n">{fmtInt(i.linked_facts)}</span>
        </li>
      ))}
    </ul>
  );
}

export default async function IndicationsPage({ searchParams }: { searchParams: SP }) {
  const { system, page } = parse(searchParams);
  const vocab = await getIndicationVocab();
  const systems = indicationsBySystem(vocab);

  if (system) {
    const s = systems.find((x) => x.key === system) ?? null;
    if (vocab && !s) notFound();
    const label = systemLabel(system);
    const pages = s ? Math.max(1, Math.ceil(s.list.length / PER_PAGE)) : 1;
    const items = s ? s.list.slice((page - 1) * PER_PAGE, page * PER_PAGE) : [];
    return (
      <>
        <Header active="/reference" />
        <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/indications", label: "Показания" }, { label }]} />
        <section className="hero-grad rf-hero">
          <span className="chip">Показания</span>
          <h1>{label}</h1>
          <p className="lead">
            {s ? `Словарь относит сюда ${countWord(s.list.length)}. ` : ""}
            Число рядом с названием показывает, сколько записей о применении растений к нему
            привязано. Серым рядом с названием написано современное имя, если оно другое.
          </p>
        </section>
        <section className="section">
          {!vocab ? (
            <Empty>
              Словарь показаний сейчас не загрузился. Обнови страницу через минуту.
            </Empty>
          ) : (
            <>
              {pages > 1 ? <div className="toolbar"><span>Страница {fmtInt(page)} из {fmtInt(pages)}</span></div> : null}
              {items.length ? <IndicationList items={items} /> : <Empty>Страницы с таким номером в списке нет. Вернись к <Link href={systemHref(system)}>началу списка</Link>.</Empty>}
              <Pager page={page} pages={pages} base="/indications" params={{ system }} />
            </>
          )}
          <p className="small muted"><Link href="/indications">← все системы организма</Link></p>
        </section>
        <p className="footnote rf-foot">Это история применения растений по книгам разных лет, а не медицинский совет.</p>
        <Footer />
      </>
    );
  }

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { label: "Показания" }]} />
      <section className="hero-grad rf-hero">
        <span className="chip">Показания</span>
        <h1>Показания по системам организма</h1>
        <p className="lead">
          От чего применяли растения, по системам организма. В старых книгах болезни называют
          по-своему, и словарь связывает такие имена с современными. Число рядом с названием
          показывает, сколько записей о применении к нему привязано.
        </p>
      </section>
      <section className="section">
        {systems.length ? (
          <div className="rf-systems">
            {systems.map(({ key, list }) => (
              <div key={key} className="card rf-system">
                <h3><a href={systemHref(key)}>{systemLabel(key)}</a></h3>
                <div className="chips rf-chips">
                  {list.slice(0, TOP).map((i) => (
                    <a key={i.id} href={`/indications/${i.id}`} className="chip chip-leaf">
                      {i.name}<span className="n">{fmtInt(i.linked_facts)}</span>
                    </a>
                  ))}
                </div>
                {list.length > TOP ? (
                  <a className="more" href={systemHref(key)}>ещё {fmtInt(list.length - TOP)} →</a>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <Empty>
            Словарь показаний сейчас не загрузился.
            Обнови страницу через минуту или открой <Link href="/atlas">атлас</Link>.
          </Empty>
        )}
      </section>
      <p className="footnote rf-foot">Это история применения растений по книгам разных лет, а не медицинский совет.</p>
      <Footer />
    </>
  );
}
