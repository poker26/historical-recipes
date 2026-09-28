import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../ui";
import { Crumbs, Empty, Pager } from "../../components/common";
import { fmtInt, pluralRu } from "../../lib/api";
import {
  COMPOUND_OTHER, compoundClassLabel, compoundClasses, getCompoundVocab,
  type CompoundClass, type CompoundLite,
} from "../../lib/api-reference";
import { pageMeta } from "../../components/reference/meta";
import "../reference/reference.css";

export const dynamic = "force-dynamic";

// Словарь большой (4,3 тыс. терминов), поэтому главная страница показывает крупные
// классы с верхушкой, а полный список класса живёт на /compounds?class=… постранично.
const TOP_CLASSES = 24;
const TOP_TERMS = 10;
const PER_PAGE = 200;

type SP = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || undefined;

function parse(sp: SP) {
  const n = parseInt(one(sp.page) || "1", 10);
  return { cls: one(sp.class)?.slice(0, 80), page: Number.isFinite(n) && n > 1 ? Math.min(n, 100) : 1 };
}

const classHref = (k: string) => `/compounds?class=${encodeURIComponent(k)}`;

export async function generateMetadata({ searchParams }: { searchParams: SP }): Promise<Metadata> {
  const { cls, page } = parse(searchParams);
  if (!cls) {
    return pageMeta({
      title: "Словарь веществ из состава растений",
      description:
        "Алкалоиды, флавоноиды, дубильные вещества, эфирные масла, витамины и другие вещества из состава растений по классам. Каждое вещество ведёт к растениям атласа и совпадениям с действиями.",
      path: "/compounds",
    });
  }
  const label = compoundClassLabel(cls);
  const path = classHref(cls) + (page > 1 ? `&page=${page}` : "");
  return pageMeta({
    title: `${label} в составе растений${page > 1 ? `, страница ${page}` : ""}`,
    description: `Вещества класса «${label.toLowerCase()}». У каждого указано, сколько записей о составе растений к нему относится, и есть ссылка на растения атласа.`,
    path,
    index: page === 1,
  });
}

// Сотни ссылок: обычные <a> вместо Link, чтобы не раздувать данные страницы и не
// запускать предзагрузку каждой ссылки. Ключи по номеру: список статичный.
function TermList({ items, showClass }: { items: (CompoundLite & { cls?: string })[]; showClass?: boolean }) {
  return (
    <ul className="rf-dict-list">
      {items.map((c, i) => (
        <li key={i}>
          <a href={`/compounds/${c.id}`}>{c.name}</a>
          {showClass && c.cls ? <span className="muted"> ({c.cls.replace(/_/g, " ")})</span> : null}
          <span className="n">{fmtInt(c.linked_facts)}</span>
        </li>
      ))}
    </ul>
  );
}

const termsWord = (n: number) => `${fmtInt(n)} ${pluralRu(n, "вещество", "вещества", "веществ")}`;
const factsWord = (n: number) => `${fmtInt(n)} ${pluralRu(n, "запись", "записи", "записей")} о составе`;

function ClassPage({ c, page }: { c: CompoundClass; page: number }) {
  const label = compoundClassLabel(c.key);
  const pages = Math.max(1, Math.ceil(c.list.length / PER_PAGE));
  const items = c.list.slice((page - 1) * PER_PAGE, page * PER_PAGE);
  return (
    <>
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/compounds", label: "Вещества" }, { label }]} />
      <section className="hero-grad rf-hero">
        <span className="chip">Класс веществ</span>
        <h1>{label}</h1>
        <p className="lead">
          {termsWord(c.list.length)} и {factsWord(c.sum)} в словаре.
          {c.key === COMPOUND_OTHER
            ? " В этот список сведены небольшие классы, где меньше трёх веществ, и вещества без класса. Класс написан рядом с названием."
            : ""}{" "}
          Число рядом с веществом показывает, сколько записей о составе растений к нему привязано.
        </p>
      </section>
      <section className="section">
        {pages > 1 ? <div className="toolbar"><span>Страница {fmtInt(page)} из {fmtInt(pages)}</span></div> : null}
        {items.length ? <TermList items={items} showClass={c.key === COMPOUND_OTHER} /> : <Empty>Страницы с таким номером в списке нет. Вернись к <Link href={classHref(c.key)}>началу списка</Link>.</Empty>}
        <Pager page={page} pages={pages} base="/compounds" params={{ class: c.key }} />
        <p className="small muted"><Link href="/compounds">← все классы веществ</Link></p>
      </section>
    </>
  );
}

export default async function CompoundsPage({ searchParams }: { searchParams: SP }) {
  const { cls, page } = parse(searchParams);
  const vocab = await getCompoundVocab();
  const { big, other } = compoundClasses(vocab);

  if (cls) {
    const c = cls === COMPOUND_OTHER ? other : big.find((x) => x.key === cls) ?? null;
    if (vocab && (!c || !c.list.length)) notFound();
    return (
      <>
        <Header active="/reference" />
        {c ? (
          <ClassPage c={c} page={page} />
        ) : (
          <Empty>
            Словарь веществ сейчас не загрузился. Обнови страницу через минуту или открой <Link href="/reference">справочники</Link>.
          </Empty>
        )}
        <Footer />
      </>
    );
  }

  const top = big.slice(0, TOP_CLASSES);
  const rest = big.slice(TOP_CLASSES);
  const terms = vocab?.length ?? 0;

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { label: "Вещества" }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">Вещества</span>
        <h1>Словарь веществ</h1>
        <p className="lead">
          {terms ? `${termsWord(terms)} из состава растений, разбитые по классам. ` : "Вещества из состава растений, разбитые по классам. "}
          Число рядом с названием показывает, сколько записей о составе растений к нему привязано.
          Словарь собран в основном из книг по фитохимии, поэтому в нём много научных названий.
        </p>
        {top.length ? (
          <nav className="toc" aria-label="Крупные классы веществ">
            {top.map((c, i) => <a key={c.key} href={`#cls-${i}`}>{compoundClassLabel(c.key)}</a>)}
          </nav>
        ) : null}
      </section>

      {!vocab ? (
        <Empty>
          Словарь веществ сейчас не загрузился. Обнови страницу через минуту или открой <Link href="/reference">справочники</Link>.
        </Empty>
      ) : null}

      {top.map((c, i) => (
        <section key={c.key} className="rf-dict" id={`cls-${i}`}>
          <h2><a href={classHref(c.key)}>{compoundClassLabel(c.key)}</a></h2>
          <div className="small muted">{termsWord(c.list.length)}, {factsWord(c.sum)}</div>
          <TermList items={c.list.slice(0, TOP_TERMS)} />
          {c.list.length > TOP_TERMS ? (
            <a className="more" href={classHref(c.key)}>ещё {fmtInt(c.list.length - TOP_TERMS)} →</a>
          ) : null}
        </section>
      ))}

      {rest.length || other.list.length ? (
        <section className="rf-dict">
          <h2>Остальные классы</h2>
          <div className="small muted">Открой класс, чтобы увидеть все его вещества.</div>
          <ul className="rf-dict-list">
            {rest.map((c) => (
              <li key={c.key}>
                <a href={classHref(c.key)}>{compoundClassLabel(c.key)}</a>
                <span className="n">{termsWord(c.list.length)}</span>
              </li>
            ))}
            {other.list.length ? (
              <li>
                <a href={classHref(COMPOUND_OTHER)}>{compoundClassLabel(COMPOUND_OTHER)}</a>
                <span className="n">{termsWord(other.list.length)}</span>
              </li>
            ) : null}
          </ul>
        </section>
      ) : null}

      <Footer />
    </>
  );
}
