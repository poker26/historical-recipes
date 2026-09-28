import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, SectionHead } from "../../../components/common";
import { fmtInt, isUuid, pluralRu } from "../../../lib/api";
import {
  cap, getIndication, getIndicationCompounds, getPublishedPlants, sameTerm, systemLabel,
} from "../../../lib/api-reference";
import { PlantGrid, PlantNameList } from "../../../components/reference/PlantGrid";
import { AssocTable } from "../../../components/reference/AssocTable";
import { pageMeta } from "../../../components/reference/meta";
import "../../reference/reference.css";

export const dynamic = "force-dynamic";

type Params = { params: { id: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) return { title: "Показание не найдено", robots: { index: false } };
  const { data: ind } = await getIndication(id);
  if (!ind) return { title: "Показание", robots: { index: false } };
  const modern = ind.name_modern && !sameTerm(ind.name_modern, ind.name) ? ind.name_modern : null;
  return pageMeta({
    title: `Какие растения применяли при показании «${ind.name}»`,
    description:
      `Растения, которые книги называют при показании «${ind.name}»${modern ? ` (по-современному ${modern})` : ""}. ` +
      "Для показания собраны старые названия болезни, виды атласа с цитатами и вещества, которые у этих растений встречаются чаще.",
    path: `/indications/${ind.id}`,
    index: ind.plants.length > 0,
  });
}

export default async function IndicationPage({ params }: Params) {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) notFound();
  const { data: ind, missing } = await getIndication(id);
  if (missing) notFound();
  if (!ind) {
    return (
      <>
        <Header active="/reference" />
        <Crumbs items={[{ href: "/reference", label: "Справочники" }, { label: "Показание" }]} />
        <Empty>
          Показание сейчас не загрузилось. Обнови страницу через минуту или открой <Link href="/reference#indications">список показаний</Link>.
        </Empty>
        <Footer />
      </>
    );
  }

  const [plants, assoc] = await Promise.all([
    getPublishedPlants({ indication: ind.name }, "photo", 48),
    getIndicationCompounds(ind.id, 10),
  ]);
  const tiles = plants?.data ?? [];
  const total = plants?.total ?? tiles.length;
  const tileIds = new Set(tiles.map((p) => p.id));
  const tail = ind.plants.filter((p) => !tileIds.has(p.id));
  const modern = ind.name_modern && !sameTerm(ind.name_modern, ind.name) ? ind.name_modern : null;
  const archaic = ind.archaic.filter((a) => a && !sameTerm(a, ind.name));
  const atlasHref = `/atlas/for/${encodeURIComponent(ind.name)}`;

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/reference#indications", label: "Показания" }, { label: ind.name }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">Показание</span>
        <h1>{cap(ind.name)}</h1>
        {ind.definition ? <p className="lead">{cap(ind.definition.replace(/\.?\s*$/, "."))}</p> : null}
        <dl className="kv rf-facts">
          {modern ? (<><dt>По-современному</dt><dd>{modern}</dd></>) : null}
          {ind.system ? (<><dt>Система организма</dt><dd>{systemLabel(ind.system)}</dd></>) : null}
          {ind.parent ? (<><dt>Входит в</dt><dd><Link href={`/indications/${ind.parent.id}`}>{ind.parent.name}</Link></dd></>) : null}
        </dl>
        {archaic.length ? (
          <>
            <div className="rf-label">В старых книгах это называют так</div>
            <div className="chips rf-chips">
              {archaic.map((a) => <span key={a} className="chip">{a}</span>)}
            </div>
          </>
        ) : null}
        {ind.children.length ? (
          <>
            <div className="rf-label">Частные случаи</div>
            <div className="chips rf-chips">
              {ind.children.map((c) => <Link key={c.id} href={`/indications/${c.id}`} className="chip chip-leaf">{c.name}</Link>)}
            </div>
          </>
        ) : null}
      </section>

      <section className="section">
        <SectionHead
          title={plants ? `${fmtInt(total)} ${pluralRu(total, "вид", "вида", "видов")} в атласе` : "Виды в атласе"}
          href={atlasHref}
          more="все в атласе"
        />
        <p className="section-lead">
          Учитываются все записи, где книга упоминает это показание, в том числе под старым
          названием. Первыми идут виды с фотографией и самыми подробными карточками.
        </p>
        {!plants ? (
          <Empty>Список растений сейчас не загрузился. Обнови страницу через минуту.</Empty>
        ) : tiles.length ? (
          <PlantGrid plants={tiles} />
        ) : (
          <Empty>
            В атласе пока нет видов с фотографией для этого показания. Загляни в <Link href="/reference#indications">список показаний</Link>.
          </Empty>
        )}
      </section>

      {tail.length ? (
        <section className="section">
          <SectionHead title="Ещё растения из словаря" />
          <p className="section-lead">
            Эти растения книги тоже связывают с показанием «{ind.name}». У них пока нет фотографии
            или записей о них меньше, чем у видов с плитками.
          </p>
          <PlantNameList items={tail} max={150} />
        </section>
      ) : null}

      <section className="section">
        <SectionHead title="Какие вещества встречаются у этих растений чаще" />
        <p className="rf-caption">
          Это совпадение по книгам, а не механизм действия. В таблице вещества, которые у растений
          при этом показании встречаются чаще, чем у остальных растений атласа. В каждом растении
          десятки веществ, поэтому приписать эффект одному из них нельзя. Строка таблицы подсказывает,
          что стоит проверить, но ничего не доказывает.
        </p>
        {!assoc ? (
          <p className="muted">Расчёт совпадений сейчас не успел завершиться. Обнови страницу через минуту.</p>
        ) : assoc.results?.length ? (
          <AssocTable data={assoc} target="compound" targetLabel="Вещество" sourceLabel="при показании" />
        ) : (
          <p className="muted">Для этого показания данных о составе растений пока слишком мало, чтобы искать совпадения.</p>
        )}
      </section>

      <p className="footnote rf-foot">Это история применения растений по книгам разных лет, а не медицинский совет.</p>
      <Footer />
    </>
  );
}
