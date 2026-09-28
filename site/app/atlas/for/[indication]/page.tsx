import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../../ui";
import { Crumbs, Empty } from "../../../../components/common";
import { SITE_URL, fmtInt, pluralRu } from "../../../../lib/api";
import {
  POPULAR_CONDITIONS,
  conditionPhrase,
  getIndication,
  getPlantFacets,
  getSuggest,
  listPlantsAnySpelling,
  mergePlants,
  norm,
  type IndicationDetail,
  type PlantSummary,
  type SuggestIndication,
} from "../../../../lib/api-atlas";
import { atlasHref, firstParam, type SearchParams } from "../../../../components/atlas/params";
import { PhotoSourcesNote, PlantGrid, PlantTable, Seg } from "../../../../components/atlas/plants";
import "../../atlas.css";

// «Растения при кашле»: виды атласа, которые книги связывают с состоянием. Как инструмент
// plants_for_condition: сначала ось показаний (со старыми названиями, «водянка» → «отёки»),
// а если видов меньше шести, добавляем ось действий и объединяем без дублей. Если запрос сам
// является каноническим действием («мочегонное»), главная ось наоборот действие.

const LIMIT = 48;

type Props = { params: { indication: string }; searchParams: SearchParams };

type ForData = {
  isAction: boolean;
  phrase: string;
  items: PlantSummary[];
  total: number;
  ok: boolean;
  axis: "indication" | "action";
  axisValue: string;
  merged: boolean;
  ind: SuggestIndication | null;
  detail: IndicationDetail | null;
};

function readParam(raw: string): string {
  let x = raw;
  try {
    x = decodeURIComponent(raw);
  } catch {
    // уже раскодировано или битая %-последовательность: берём как есть
  }
  return x.replace(/\s+/g, " ").trim().slice(0, 80);
}

async function loadFor(x: string): Promise<ForData> {
  const nx = norm(x);
  const base = { published: true, sort: "match" as const, limit: LIMIT };
  const [sug, byInd, byAct] = await Promise.all([
    getSuggest(x, 12),
    listPlantsAnySpelling(base, "indication", x),
    listPlantsAnySpelling(base, "action", x),
  ]);
  // Подсказки могли не ответить: тогда сверяемся с каноническими действиями из фасетов.
  const isAction = sug
    ? (sug.actions ?? []).some((a) => norm(a.name) === nx)
    : ((await getPlantFacets())?.actions ?? []).some((a) => norm(a.value) === nx);
  const primary = isAction ? byAct : byInd;
  const secondary = isAction ? byInd : byAct;
  const merged = primary.items.length < 6 && secondary.items.length > 0;
  let items = primary.items;
  let total = primary.total;
  if (merged) {
    items = mergePlants([primary, secondary], LIMIT);
    const ids = new Set(primary.items.map((p) => p.id));
    const overlap = secondary.items.filter((p) => ids.has(p.id)).length;
    total = Math.max(items.length, primary.total + secondary.total - overlap);
  }

  // Показание под заголовком: точное совпадение имени; иначе первое из подсказок,
  // если запрос стоит среди его старых имён или синонимов.
  let ind: SuggestIndication | null = null;
  let detail: IndicationDetail | null = null;
  if (!isAction) {
    const inds = (sug?.indications ?? []).filter((i) => (i.facts ?? 0) > 0 || norm(i.name) === nx);
    const exact = inds.find((i) => norm(i.name) === nx || norm(i.name_modern) === nx);
    if (exact) {
      ind = exact;
      detail = await getIndication(exact.id);
    } else if (inds[0]) {
      const d = await getIndication(inds[0].id);
      if (d && [...(d.archaic ?? []), ...(d.synonyms ?? [])].some((a) => norm(a) === nx)) {
        ind = inds[0];
        detail = d;
      }
    }
  }

  return {
    isAction,
    phrase: conditionPhrase(x, isAction),
    items,
    total,
    ok: byInd.ok || byAct.ok,
    axis: isAction ? "action" : "indication",
    axisValue: primary.value,
    merged,
    ind,
    detail,
  };
}

const speciesWord = (n: number) => pluralRu(n, "вид", "вида", "видов");

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const x = readParam(params.indication);
  if (!x) return { title: "Растения при состоянии", robots: { index: false, follow: true } };
  const d = await loadFor(x);
  const title = `Растения ${d.phrase}`;
  const description = d.total
    ? d.isAction
      ? `${fmtInt(d.total)} ${speciesWord(d.total)} с фотографиями, у которых травники и справочники отмечают ${x.toLowerCase()} действие. Для каждого собраны цитаты из книг, сведения о безопасности и рецепты. Это история применения, а не медицинский совет.`
      : `Какие растения применяли ${d.phrase} по травникам и справочникам с 1790 года. В атласе ${fmtInt(d.total)} ${speciesWord(d.total)} с фотографиями, цитатами из книг и сведениями о безопасности. Это история применения, а не медицинский совет.`
    : `В атласе пока нет видов, которые книги связывают с «${x}». Попробуй старое, современное или латинское название.`;
  const canonical = `${SITE_URL}/atlas/for/${encodeURIComponent(x.toLowerCase())}`;
  const indexable = d.total > 0 && !firstParam(searchParams.view);
  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title, description, url: canonical, type: "website" },
    robots: indexable ? undefined : { index: false, follow: true },
  };
}

const capFirst = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export default async function AtlasForPage({ params, searchParams }: Props) {
  const x = readParam(params.indication);
  if (!x) notFound();
  const d = await loadFor(x);
  const table = firstParam(searchParams.view) === "table";
  const title = `Растения ${d.phrase}`;
  const self = `/atlas/for/${encodeURIComponent(x)}`;
  const nx = norm(x);

  // Современное имя, определение и старые названия показания.
  const indName = d.ind ? norm(d.ind.name) : "";
  const modern =
    d.ind?.name_modern && norm(d.ind.name_modern) !== nx && norm(d.ind.name_modern) !== indName ? d.ind.name_modern : null;
  const archaic = Array.from(
    new Set(
      (d.detail?.archaic ?? [])
        .map((a) => a.replace(/\s+/g, " ").trim())
        .filter((a) => a && norm(a) !== nx && norm(a) !== indName && !(indName && norm(a).includes(indName))),
    ),
  ).slice(0, 8);
  const definition = d.detail?.definition?.replace(/\s+/g, " ").trim() || null;

  const others = POPULAR_CONDITIONS.filter((c) => norm(c) !== nx);
  const allHref = atlasHref({}, d.axis === "action" ? { action: d.axisValue } : { indication: d.axisValue });

  let content;
  if (!d.ok) {
    content = (
      <Empty>
        Атлас сейчас не отвечает. Обнови страницу через минуту или попробуй{" "}
        <Link href={`/search?q=${encodeURIComponent(x)}`}>поиск</Link>.
      </Empty>
    );
  } else if (!d.items.length) {
    content = (
      <Empty>
        В атласе пока нет видов с фотографией, которые книги связывают с «{x}». Попробуй другое название, старое или
        современное, или <Link href={`/search?q=${encodeURIComponent(x)}`}>поищи по всему сайту</Link>: там есть и
        рецепты, и фрагменты книг.
      </Empty>
    );
  } else {
    content = table ? <PlantTable items={d.items} /> : <PlantGrid items={d.items} />;
  }

  return (
    <>
      <Header active="/atlas" />
      <Crumbs items={[{ href: "/atlas", label: "Атлас" }, { label: title }]} />

      <section className="atlas-for-head">
        <h1>{title}</h1>
        {d.total ? (
          <p className="lead">
            {d.isAction ? (
              <>
                В атласе {fmtInt(d.total)} {speciesWord(d.total)} с фотографией, у которых книги отмечают{" "}
                {x.toLowerCase()} действие.
              </>
            ) : (
              <>
                В атласе {fmtInt(d.total)} {speciesWord(d.total)} с фотографией, которые книги называют средством{" "}
                {d.phrase}.
              </>
            )}{" "}
            {d.isAction
              ? "Первыми идут виды, у которых книги чаще всего называют это действие."
              : "Первыми идут виды, которые книги чаще всего советуют именно при этом."}
          </p>
        ) : null}

        {d.ind ? (
          <div className="card card-tight card-soft atlas-ind">
            {indName !== nx ? (
              <p>
                В справочнике это показание называется «{d.ind.name}».
              </p>
            ) : null}
            {modern ? (
              <p>
                По-современному это <b>{modern}</b>.
              </p>
            ) : null}
            {definition ? (
              <p className="muted">
                {capFirst(definition)}
                {/[.!?…]$/.test(definition) ? "" : "."}
              </p>
            ) : null}
            {archaic.length ? (
              <p>
                В старых книгах это называли{" "}
                {archaic.map((a, i) => (
                  <span key={a}>
                    {i ? (i === archaic.length - 1 ? " или " : ", ") : ""}
                    <Link href={`/atlas/for/${encodeURIComponent(a)}`}>{a}</Link>
                  </span>
                ))}
                .
              </p>
            ) : null}
            <p>
              <Link href={`/indications/${d.ind.id}`}>Всё о показании «{d.ind.name}» →</Link>
            </p>
          </div>
        ) : d.isAction ? (
          <div className="card card-tight card-soft atlas-ind">
            <p>
              <Link href={`/actions/${encodeURIComponent(x.toLowerCase())}`}>Всё о действии «{x.toLowerCase()}» →</Link>
            </p>
          </div>
        ) : null}
      </section>

      {d.items.length ? (
        <div className="toolbar">
          <span className="atlas-count">
            {d.items.length < d.total
              ? `Первые ${fmtInt(d.items.length)} из ${fmtInt(d.total)}`
              : `${fmtInt(d.total)} ${speciesWord(d.total)}`}
          </span>
          <Seg
            label="Вид списка"
            items={[
              { label: "плитки", href: self, on: !table },
              { label: "таблица", href: `${self}?view=table`, on: table },
            ]}
          />
        </div>
      ) : null}

      {content}

      {d.total > d.items.length && !d.merged ? (
        <p className="atlas-more">
          <Link href={allHref}>
            Все {fmtInt(d.total)} {speciesWord(d.total)} в атласе с фильтрами →
          </Link>
        </p>
      ) : null}
      {!table && d.items.length ? <PhotoSourcesNote /> : null}

      <section className="section">
        <h2 className="section-title">Растения при других состояниях</h2>
        <div className="chips">
          {others.map((c) => (
            <Link key={c} href={`/atlas/for/${encodeURIComponent(c)}`} className="chip chip-leaf">
              {c}
            </Link>
          ))}
        </div>
      </section>

      <p className="footnote atlas-disclaimer">
        Это история применения растений по книгам 1790–2020 годов, а не медицинский совет.
      </p>

      <Footer />
    </>
  );
}
