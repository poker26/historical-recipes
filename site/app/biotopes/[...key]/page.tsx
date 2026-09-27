import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, SectionHead } from "../../../components/common";
import { fmtInt, pluralRu } from "../../../lib/api";
import {
  BIOTOPE_GROUP_RU, BIOTOPE_GROUP_TEXT, biotopeHref, biotopeLabel, cap, getBiotopes, getPublishedPlants, sameTerm,
  type Biotope,
} from "../../../lib/api-reference";
import { PlantGrid } from "../../../components/reference/PlantGrid";
import { decodeParam, pageMeta } from "../../../components/reference/meta";
import "../../reference/reference.css";

export const dynamic = "force-dynamic";

// Ключи биотопов содержат косую черту («опушки/поляны/вырубки/редколесье»), поэтому
// страница ловит все сегменты: работают и /biotopes/опушки/поляны/…, и вариант с %2F.
type Params = { params: { key: string[] } };

function readKey(parts: string[] | undefined): string | null {
  const k = (parts ?? []).map(decodeParam).join("/").replace(/\s+/g, " ").trim();
  return k && k.length <= 80 ? k : null;
}

function findBiotope(list: Biotope[] | undefined, key: string): Biotope | null {
  if (!list) return null;
  return list.find((b) => b.key === key) ?? list.find((b) => sameTerm(b.key, key)) ?? null;
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const key = readKey(params.key);
  if (!key) return { title: "Биотоп не найден", robots: { index: false } };
  const res = await getBiotopes();
  const b = findBiotope(res?.biotopes, key);
  if (!b) return { title: "Биотоп", robots: { index: false } };
  const label = biotopeLabel(b.key);
  return pageMeta({
    title: `Что растёт: ${label}`,
    description:
      `${fmtInt(b.count)} ${pluralRu(b.count, "вид", "вида", "видов")} атласа, которые книги и наблюдения связывают с местом «${label}». ` +
      (BIOTOPE_GROUP_TEXT[b.group] ?? ""),
    path: biotopeHref(b.key),
  });
}

export default async function BiotopePage({ params }: Params) {
  const key = readKey(params.key);
  if (!key) notFound();
  const res = await getBiotopes();
  const b = findBiotope(res?.biotopes, key);
  if (res && !b) notFound();
  const realKey = b?.key ?? key;
  const label = biotopeLabel(realKey);
  const plants = await getPublishedPlants({ biotope: realKey }, "photo", 48);
  const tiles = plants?.data ?? [];
  const total = plants?.total ?? tiles.length;
  const siblings = b ? (res?.biotopes ?? []).filter((x) => x.group === b.group && x.key !== b.key) : [];

  return (
    <>
      <Header active="/reference" />
      <Crumbs items={[{ href: "/reference", label: "Справочники" }, { href: "/reference#biotopes", label: "Где растут" }, { label: cap(label) }]} />

      <section className="hero-grad rf-hero">
        <span className="chip">{b ? BIOTOPE_GROUP_RU[b.group] ?? cap(b.group) : "Биотоп"}</span>
        <h1>Что растёт: {label}</h1>
        <p className="lead">
          {b && BIOTOPE_GROUP_TEXT[b.group] ? `${BIOTOPE_GROUP_TEXT[b.group]} ` : ""}
          Ниже виды атласа, которые книги и наблюдения связывают с этим местом.
        </p>
      </section>

      <section className="section">
        <SectionHead title={plants ? `${fmtInt(total)} ${pluralRu(total, "вид", "вида", "видов")} в атласе` : "Виды в атласе"} />
        <p className="section-lead">
          Сначала виды с фотографией и самыми подробными карточками. Метки на плитках предупреждают
          о ядовитых видах: прежде чем что-то собирать, открой карточку.
        </p>
        {!plants ? (
          <Empty>Список растений сейчас не загрузился. Обнови страницу через минуту.</Empty>
        ) : tiles.length ? (
          <PlantGrid plants={tiles} />
        ) : (
          <Empty>
            В атласе пока нет видов с фотографией для этого места. Загляни в <Link href="/reference#biotopes">другие биотопы</Link>.
          </Empty>
        )}
      </section>

      {siblings.length ? (
        <section className="section">
          <SectionHead title="Похожие места" href="/reference#biotopes" more="все биотопы" />
          <p className="section-lead">Число рядом с местом показывает, сколько видов атласа с ним связано.</p>
          <div className="chips rf-chips">
            {siblings.map((s) => (
              <Link key={s.key} href={biotopeHref(s.key)} className="chip chip-leaf">
                {biotopeLabel(s.key)}<span className="n">{fmtInt(s.count)}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      <section className="section card card-soft rf-cta">
        <h2>Такие места рядом с тобой</h2>
        <p>
          В разделе <Link href="/places">«Прогулки»</Link> собраны парки и леса с наборами видов на
          этот месяц. Там видно, что можно найти сейчас и куда за этим идти.
        </p>
      </section>

      <Footer />
    </>
  );
}
