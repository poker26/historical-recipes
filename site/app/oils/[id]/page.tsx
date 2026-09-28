import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, Quote, Tile } from "../../../components/common";
import { excerpt, fmtInt, isUuid, plantHref, pluralRu } from "../../../lib/api";
import { cap, cleanSynonyms, getOil, getPlantFacets, sameTerm, type OilUse } from "../../../lib/api-reference";
import { pageMeta } from "../../../components/reference/meta";
import "../../reference/reference.css";

export const dynamic = "force-dynamic";

type Params = { params: { id: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) return { title: "Масло не найдено", robots: { index: false } };
  const { data: o } = await getOil(id);
  if (!o) return { title: "Эфирное масло", robots: { index: false } };
  const plant = o.plant?.name || o.source_plant_raw;
  return pageMeta({
    title: `${cap(o.name)} и его применение по книгам`,
    description:
      `${cap(o.name)}${plant ? ` получают из растения ${plant}${o.part ? ` (${o.part})` : ""}` : ""}. ` +
      `${o.aroma_profile ? excerpt(cap(o.aroma_profile), 80) + ". " : ""}` +
      `${o.uses.length ? `${fmtInt(o.uses.length)} ${pluralRu(o.uses.length, "запись", "записи", "записей")} о применении с цитатами из книг.` : "Описание и цитаты из книг."}`,
    path: `/oils/${o.id}`,
  });
}

function UseCard({ u, canon }: { u: OilUse; canon: Set<string> }) {
  const action = (u.action || u.action_raw || "").trim();
  return (
    <article className="card rf-use">
      <h3>
        {action
          ? canon.has(action) ? <Link href={`/actions/${encodeURIComponent(action)}`}>{action}</Link> : action
          : "Применение"}
      </h3>
      <dl className="kv">
        {u.indications ? (<><dt>Показания</dt><dd>{u.indications}</dd></>) : null}
        {u.application ? (<><dt>Как применяют</dt><dd>{u.application}</dd></>) : null}
        {u.dosage ? (<><dt>Дозировка</dt><dd>{u.dosage}</dd></>) : null}
        {u.contraindications ? (<><dt>Противопоказания</dt><dd><span className="chip chip-danger">{u.contraindications}</span></dd></>) : null}
      </dl>
      {u.indication_concepts.length ? (
        <div className="chips rf-chips">
          {u.indication_concepts.map((c) => (
            <Link key={c} href={`/atlas/for/${encodeURIComponent(c)}`} className="chip chip-leaf" title={`Растения атласа при показании «${c}»`}>{c}</Link>
          ))}
        </div>
      ) : null}
      {u.original_text ? <Quote text={u.original_text} /> : null}
    </article>
  );
}

export default async function OilPage({ params }: Params) {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) notFound();
  const { data: o, missing } = await getOil(id);
  if (missing) notFound();
  if (!o) {
    return (
      <>
        <Header active="/reference" />
        <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/oils", label: "Эфирные масла" }]} />
        <Empty>
          Масло сейчас не загрузилось. Обнови страницу через минуту или открой <Link href="/oils">список масел</Link>.
        </Empty>
        <Footer />
      </>
    );
  }

  const facets = await getPlantFacets();
  const canon = new Set((facets?.actions ?? []).map((a) => a.value));
  const synonyms = cleanSynonyms(o.synonyms, [o.name, o.name_latin], 12);
  const latin = o.name_latin && !sameTerm(o.name_latin, o.name) ? o.name_latin : null;
  // Сначала записи с действием или показанием, заметки без них в конце.
  const uses = [...o.uses].sort((a, b) => Number(!(a.action || a.indications)) - Number(!(b.action || b.indications)));

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/oils", label: "Эфирные масла" }, { label: cap(o.name) }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">Эфирное масло</span>
        <h1>{cap(o.name)}</h1>
        {latin ? <div className="latin">{latin}</div> : null}
        {o.description ? <p className="lead rf-lead-gap">{cap(o.description.trim())}</p> : null}
        {o.part || o.extraction || o.aroma_profile || (!o.plant && o.source_plant_raw) ? (
          <dl className="kv rf-facts">
            {!o.plant && o.source_plant_raw ? (<><dt>Растение по книге</dt><dd>{o.source_plant_raw}</dd></>) : null}
            {o.part ? (<><dt>Часть растения</dt><dd>{o.part}</dd></>) : null}
            {o.extraction ? (<><dt>Как получают</dt><dd>{o.extraction}</dd></>) : null}
            {o.aroma_profile ? (<><dt>Аромат</dt><dd>{o.aroma_profile}</dd></>) : null}
          </dl>
        ) : null}
        {synonyms.length ? (
          <>
            <div className="rf-label">Другие названия</div>
            <div className="chips rf-chips">
              {synonyms.map((s) => <span key={s} className="chip">{s}</span>)}
            </div>
          </>
        ) : null}
      </section>

      {o.plant ? (
        <section className="block">
          <h2>Из какого растения</h2>
          <div className="rf-source">
            <Tile href={plantHref(o.plant.id, o.plant.name_latin)} name={o.plant.name} latin={o.plant.name_latin} photo={o.plant.photo_url} meta="карточка в атласе" />
          </div>
        </section>
      ) : null}

      {o.original_text ? (
        <section className="block">
          <h2>Цитата из книги</h2>
          <Quote text={o.original_text} />
          <p className="small muted">Книга для этой цитаты в справочнике масел пока не указана.</p>
        </section>
      ) : null}

      <section className="block">
        <h2>Для чего применяли</h2>
        {uses.length ? (
          <div className="rf-uses">
            {uses.map((u) => <UseCard key={u.id} u={u} canon={canon} />)}
          </div>
        ) : (
          <Empty>
            О применении этого масла в книгах пока нет записей. Загляни в <Link href="/oils">список масел</Link>.
          </Empty>
        )}
      </section>

      <p className="footnote rf-foot">
        Сведения об ароматерапии взяты из книг, и научных подтверждений у них мало. Это история применения, а не совет.
      </p>
      <Footer />
    </>
  );
}
