import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, SectionHead } from "../../../components/common";
import { fmtInt, pluralRu } from "../../../lib/api";
import {
  cap, cleanSynonyms, getActionVocab, getPlantFacets, getPublishedPlants,
  sameTerm, systemKey, systemLabel, type ActionTerm,
} from "../../../lib/api-reference";
import { PlantGrid } from "../../../components/reference/PlantGrid";
import { decodeParam, pageMeta } from "../../../components/reference/meta";
import "../../reference/reference.css";

export const dynamic = "force-dynamic";

type Params = { params: { name: string } };

function readName(raw: string): string | null {
  const n = decodeParam(raw).replace(/\s+/g, " ").trim();
  return n && n.length <= 80 ? n : null;
}

function findTerm(vocab: ActionTerm[] | null, name: string): ActionTerm | null {
  if (!vocab) return null;
  return vocab.find((a) => a.name === name) ?? vocab.find((a) => sameTerm(a.name, name)) ?? null;
}

/** «мочегонное» → «мочегонные», «возбуждающее аппетит» → «возбуждающие аппетит»,
 *  «кардиотоническое» → «кардиотонические». Если форма не угадывается, null. */
function pluralAdj(name: string): string | null {
  const [first, ...rest] = name.split(" ");
  let p: string | null = null;
  if (/ое$/.test(first)) p = first.slice(0, -2) + (/[кгхжшщч]$/.test(first.slice(0, -2)) ? "ие" : "ые");
  else if (/ее$/.test(first)) p = first.slice(0, -2) + "ие";
  return p ? [p, ...rest].join(" ") : null;
}

function headline(name: string): { h1: string; title: string } {
  const p = pluralAdj(name);
  return p
    ? { h1: `${cap(p)} растения`, title: `${cap(p)} растения по книгам` }
    : { h1: `Действие «${name}»`, title: `Действие «${name}»: растения по книгам` };
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const name = readName(params.name);
  if (!name) return { title: "Действие не найдено", robots: { index: false } };
  const [facets, vocab] = await Promise.all([getPlantFacets(), getActionVocab()]);
  const canon = facets?.actions.find((a) => a.value === name) ?? null;
  const term = findTerm(vocab, name);
  const modern = term?.name_modern && !sameTerm(term.name_modern, name) ? term.name_modern : null;
  return pageMeta({
    title: headline(name).title,
    description:
      `Растения, которым книги приписывают действие «${name}»${modern ? ` (по-современному ${modern})` : ""}` +
      `${canon ? `, всего ${fmtInt(canon.count)} ${pluralRu(canon.count, "вид", "вида", "видов")}` : ""}. ` +
      "У каждого вида есть цитаты из книг с годом и страницей.",
    path: `/actions/${encodeURIComponent(name)}`,
    // В индекс идут 60 канонических действий; остальные термины словаря открываются по ссылке.
    index: !!canon,
  });
}

export default async function ActionPage({ params }: Params) {
  const name = readName(params.name);
  if (!name) notFound();
  const [plants, vocab, facets] = await Promise.all([
    getPublishedPlants({ action: name }, "uses", 48),
    getActionVocab(),
    getPlantFacets(),
  ]);
  const term = findTerm(vocab, name);
  const canon = facets?.actions.find((a) => a.value === name) ?? null;
  const items = plants?.data ?? [];
  const total = plants?.total ?? items.length;
  if (plants && !total && !term && !canon) notFound();

  const { h1 } = headline(name);
  const modern = term?.name_modern && !sameTerm(term.name_modern, name) ? term.name_modern : null;
  const parent = term?.parent_id ? vocab?.find((a) => a.id === term.parent_id) ?? null : null;
  const synonyms = cleanSynonyms(term?.synonyms, [name, modern]);
  const sys = term?.system ? systemKey(term.system) : null;

  // Соседи по системе организма среди 60 канонических действий.
  const related = sys && facets && vocab
    ? facets.actions
        .filter((a) => a.value !== name)
        .filter((a) => {
          const t = findTerm(vocab, a.value);
          return t?.system && systemKey(t.system) === sys;
        })
        .slice(0, 16)
    : [];

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/reference#actions", label: "Действия" }, { label: name }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">Действие растений</span>
        <h1>{h1}</h1>
        <p className="lead">
          {canon
            ? `Книги называют действие «${name}» у ${fmtInt(canon.count)} ${pluralRu(canon.count, "растения", "растений", "растений")}. Ниже виды из атласа, о применении которых записей больше всего.`
            : `Растения, которым книги приписывают действие «${name}». Ниже виды из атласа, о применении которых записей больше всего.`}
        </p>
        {modern || sys || parent ? (
          <dl className="kv rf-facts">
            {modern ? (<><dt>По-современному</dt><dd>{modern}</dd></>) : null}
            {sys ? (<><dt>Система организма</dt><dd>{systemLabel(sys)}</dd></>) : null}
            {parent ? (<><dt>Группа в словаре</dt><dd>{parent.name}</dd></>) : null}
          </dl>
        ) : null}
        {synonyms.length ? (
          <>
            <div className="rf-label">Как ещё пишут в книгах</div>
            <div className="chips rf-chips">
              {synonyms.map((s) => <span key={s} className="chip">{s}</span>)}
            </div>
          </>
        ) : null}
      </section>

      <section className="section">
        <SectionHead
          title={plants ? `${fmtInt(total)} ${pluralRu(total, "вид", "вида", "видов")} в атласе` : "Виды в атласе"}
          href={`/atlas?action=${encodeURIComponent(name)}`}
          more="все в атласе"
        />
        {!plants ? (
          <Empty>Список растений сейчас не загрузился. Обнови страницу через минуту.</Empty>
        ) : items.length ? (
          <PlantGrid plants={items} />
        ) : (
          <Empty>
            В атласе пока нет видов с фотографией, у которых книги называют это действие.
            Загляни в <Link href="/reference#actions">список действий</Link>.
          </Empty>
        )}
      </section>

      {related.length ? (
        <section className="section">
          <SectionHead title="Другие действия на ту же систему" />
          <p className="section-lead">Число рядом с действием показывает, у скольких растений атласа оно упомянуто.</p>
          <div className="chips rf-chips">
            {related.map((a) => (
              <Link key={a.value} href={`/actions/${encodeURIComponent(a.value)}`} className="chip chip-leaf">
                {a.value}<span className="n">{fmtInt(a.count)}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      <p className="footnote rf-foot">Это история применения растений по книгам разных лет, а не медицинский совет.</p>
      <Footer />
    </>
  );
}
