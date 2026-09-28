import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, Quote, SectionHead } from "../../../components/common";
import { excerpt, fmtInt, isUuid, pluralRu } from "../../../lib/api";
import {
  cap, cleanSynonyms, getCompound, getCompoundAssociations, getCompoundVocab, getPublishedPlants, sameTerm,
} from "../../../lib/api-reference";
import { PlantGrid, PlantNameList } from "../../../components/reference/PlantGrid";
import { AssocTable } from "../../../components/reference/AssocTable";
import { pageMeta } from "../../../components/reference/meta";
import "../../reference/reference.css";

export const dynamic = "force-dynamic";

type Params = { params: { id: string } };

const CHILDREN_SHOWN = 60;

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) return { title: "Вещество не найдено", robots: { index: false } };
  const { data: c } = await getCompound(id);
  if (!c) return { title: "Вещество", robots: { index: false } };
  return pageMeta({
    title: `В каких растениях встречается вещество «${c.name}»`,
    description:
      `${c.definition ? excerpt(c.definition, 110) + " " : ""}` +
      `Растения атласа, в составе которых книги находят «${c.name}», и действия, с которыми это вещество совпадает по книгам.`,
    path: `/compounds/${c.id}`,
    index: c.plants.length > 0,
  });
}

export default async function CompoundPage({ params }: Params) {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) notFound();
  const { data: c, missing } = await getCompound(id);
  if (missing) notFound();
  if (!c) {
    return (
      <>
        <Header active="/reference" />
        <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/compounds", label: "Вещества" }]} />
        <Empty>
          Вещество сейчас не загрузилось. Обнови страницу через минуту или открой <Link href="/compounds">словарь веществ</Link>.
        </Empty>
        <Footer />
      </>
    );
  }

  const [plants, assoc, vocab] = await Promise.all([
    getPublishedPlants({ compound: c.name }, "photo", 48),
    getCompoundAssociations(c.id, 10),
    c.children.length ? getCompoundVocab() : Promise.resolve(null),
  ]);
  const tiles = plants?.data ?? [];
  const total = plants?.total ?? tiles.length;
  const tileIds = new Set(tiles.map((p) => p.id));
  const tail = c.plants.filter((p) => !tileIds.has(p.id));
  const latin = c.name_latin && !sameTerm(c.name_latin, c.name) ? c.name_latin : null;
  const synonyms = cleanSynonyms(c.synonyms, [c.name, c.name_latin]);

  // Дочерние термины: по числу записей, если словарь под рукой; без записей не показываем.
  const counts = new Map((vocab ?? []).map((v) => [v.id, v.linked_facts]));
  const children = vocab
    ? c.children.filter((ch) => counts.has(ch.id)).sort((a, b) => (counts.get(b.id) ?? 0) - (counts.get(a.id) ?? 0))
    : c.children;

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/compounds", label: "Вещества" }, { label: c.name }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">Вещество</span>
        <h1>{cap(c.name)}</h1>
        {latin ? <div className="latin">{latin}</div> : null}
        {c.definition ? <p className="lead rf-lead-gap">{cap(c.definition.trim())}</p> : null}
        <dl className="kv rf-facts">
          {c.compound_class && !sameTerm(c.compound_class, c.name) && !sameTerm(c.compound_class, c.parent?.name) ? (<><dt>Класс</dt><dd>{c.compound_class.replace(/_/g, " ")}</dd></>) : null}
          {c.parent ? (<><dt>Входит в</dt><dd><Link href={`/compounds/${c.parent.id}`}>{c.parent.name}</Link></dd></>) : null}
          {c.plants.length ? (<><dt>Растений с этим веществом</dt><dd>{fmtInt(c.plants.length)}</dd></>) : null}
        </dl>
        {synonyms.length ? (
          <>
            <div className="rf-label">Как ещё называют</div>
            <div className="chips rf-chips">
              {synonyms.map((s) => <span key={s} className="chip">{s}</span>)}
            </div>
          </>
        ) : null}
      </section>

      {c.original_text ? (
        <section className="block">
          <h2>Цитата из книги</h2>
          <Quote text={c.original_text} />
          <p className="small muted">Книга для этой цитаты в словаре веществ пока не указана.</p>
        </section>
      ) : null}

      {children.length ? (
        <section className="block">
          <h2>Что входит в эту группу</h2>
          <div className="chips rf-chips">
            {children.slice(0, CHILDREN_SHOWN).map((ch) => (
              <Link key={ch.id} href={`/compounds/${ch.id}`} className="chip chip-leaf">
                {ch.name}{counts.get(ch.id) ? <span className="n">{fmtInt(counts.get(ch.id) ?? 0)}</span> : null}
              </Link>
            ))}
          </div>
          {children.length > CHILDREN_SHOWN ? (
            <p className="small muted">
              И ещё {fmtInt(children.length - CHILDREN_SHOWN)} {pluralRu(children.length - CHILDREN_SHOWN, "вещество", "вещества", "веществ")} в <Link href="/compounds">словаре</Link>.
            </p>
          ) : null}
        </section>
      ) : null}

      <section className="section">
        <SectionHead title={plants ? `${fmtInt(total)} ${pluralRu(total, "вид", "вида", "видов")} в атласе` : "Виды в атласе"} />
        <p className="section-lead">
          Виды, в составе которых книги находят это вещество. Сначала виды с фотографией и самыми
          подробными карточками.
        </p>
        {!plants ? (
          <Empty>Список растений сейчас не загрузился. Обнови страницу через минуту.</Empty>
        ) : tiles.length ? (
          <PlantGrid plants={tiles} />
        ) : (
          <Empty>
            В атласе пока нет видов с фотографией, в составе которых записано это вещество.
            Загляни в <Link href="/compounds">словарь веществ</Link>.
          </Empty>
        )}
      </section>

      {tail.length ? (
        <section className="section">
          <SectionHead title="Ещё растения с этим веществом" />
          <p className="section-lead">
            В этих растениях вещество тоже записано, но у них пока нет фотографии или записей о них
            меньше. Рядом с именем указано, в какой части растения его нашли.
          </p>
          <PlantNameList items={tail} max={150} />
        </section>
      ) : null}

      <section className="section">
        <SectionHead title="С какими действиями совпадает" />
        <p className="rf-caption">
          Это совпадение по книгам, а не механизм действия. Растения, в составе которых есть это
          вещество, книги чаще других называют так, как указано в таблице. В каждом растении десятки
          веществ, поэтому строка остаётся гипотезой. Она подсказывает, что стоит проверить, но не
          доказывает, что вещество действует само.
        </p>
        {!assoc ? (
          <p className="muted">Расчёт совпадений сейчас не успел завершиться. Обнови страницу через минуту.</p>
        ) : assoc.results?.length ? (
          <AssocTable data={assoc} target="action" targetLabel="Действие" sourceLabel="с веществом" />
        ) : (
          <p className="muted">Для этого вещества растений пока слишком мало, чтобы искать совпадения.</p>
        )}
      </section>

      <p className="footnote rf-foot">Это история применения растений по книгам разных лет, а не медицинский совет.</p>
      <Footer />
    </>
  );
}
