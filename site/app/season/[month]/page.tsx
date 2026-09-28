import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, LeafGlyph, MONTHS_PREP_RU, MONTHS_RU, SourceRef } from "../../../components/common";
import { DEFAULT_OG, SITE_URL, fmtInt, plantHref, pluralRu } from "../../../lib/api";
import { creditShort, displayName } from "../../../lib/api-atlas";
import { canonParts } from "../../../lib/api-plant";
import { getHarvest, monthOfSlug, seasonHref, type HarvestItem } from "../../../lib/season";
import "../season.css";

// «Что собирать в сентябре»: виды, которые по книгам заготавливают в этом месяце, с частью
// растения, сроком, способом и книгой, откуда это взято. Данные те же, что у полки
// «что заготавливают» в приложении (backend/app/routers/showcase.py, _harvest_shelf).

type Props = { params: { month: string } };

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);
const plantsWord = (n: number) => pluralRu(n, "растение", "растения", "растений");

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const m = monthOfSlug(params.month);
  if (!m) return { title: "Месяц не найден", robots: { index: false } };
  const prep = MONTHS_PREP_RU[m - 1];
  const data = await getHarvest(m);
  const n = data?.items.length ?? 0;
  const title = `Что собирать в ${prep}`;
  const description = n
    ? `По травникам и справочникам в ${prep} заготавливают ${fmtInt(n)} ${plantsWord(n)}. Для каждого указано, какую часть собирают, когда и как её сушат, и в какой книге это записано.`
    : `Какие растения заготавливают в ${prep} по старым травникам и справочникам.`;
  const url = SITE_URL + seasonHref(m);
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { title, description, url, type: "website", images: [DEFAULT_OG] },
    robots: n ? undefined : { index: false, follow: true },
  };
}

function Row({ it }: { it: HarvestItem }) {
  const name = displayName(it.name);
  const href = plantHref(it.plant_id, it.latin);
  const parts = Array.from(new Set(canonParts(it.part))).join(", ") || it.part;
  const credit = it.photo ? creditShort(it.photo_attribution) : null;
  const danger = (it.safety_level ?? 0) >= 3;
  return (
    <li className="season-item">
      <Link href={href} className="season-photo" aria-hidden="true" tabIndex={-1}>
        {it.photo && credit ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={it.photo} alt="" loading="lazy" />
        ) : (
          <LeafGlyph />
        )}
      </Link>
      <div className="season-body">
        <div className="season-head">
          <Link href={href} className="season-name">{name}</Link>
          {it.latin ? <span className="latin small">{it.latin}</span> : null}
          {danger ? <span className="tag tag-warn">{(it.safety_level ?? 0) >= 4 ? "смертельно ядовито" : "ядовито"}</span> : null}
        </div>
        <dl className="kv season-kv">
          {parts ? (
            <>
              <dt>Что собирают</dt>
              <dd>{parts}</dd>
            </>
          ) : null}
          {it.season ? (
            <>
              <dt>Когда</dt>
              <dd>{it.season}</dd>
            </>
          ) : null}
          {it.method ? (
            <>
              <dt>Как</dt>
              <dd>{cap(it.method)}</dd>
            </>
          ) : null}
        </dl>
        <div className="season-source">
          <SourceRef book={it.book} bookId={it.book_id} year={it.year} page={it.page} />
        </div>
        {credit ? <div className="tile-credit" title={it.photo_attribution ?? undefined}>Фото {credit}</div> : null}
      </div>
    </li>
  );
}

export default async function SeasonPage({ params }: Props) {
  const m = monthOfSlug(params.month);
  if (!m) notFound();
  const prep = MONTHS_PREP_RU[m - 1];
  const data = await getHarvest(m);
  const items = data?.items ?? [];
  const prev = m === 1 ? 12 : m - 1;
  const next = m === 12 ? 1 : m + 1;

  return (
    <>
      <Header active="/atlas" />
      <Crumbs items={[{ href: "/atlas", label: "Атлас" }, { href: "/season", label: "Что собирать по месяцам" }, { label: cap(MONTHS_RU[m - 1]) }]} />
      <section className="hero-grad atlas-hero atlas-hero-compact">
        <h1>Что собирать в {prep}</h1>
        <p className="lead">
          {items.length
            ? `В ${prep} по книгам заготавливают ${fmtInt(items.length)} ${plantsWord(items.length)}. `
            : ""}
          У каждой записи указано, какую часть растения собирают, когда и как её сушат, и из какой книги это взято.
          Корни и корневища в список не входят.
        </p>
      </section>

      <section className="section">
        {data === null ? (
          <Empty>
            Список сейчас не загрузился. Обнови страницу через минуту или открой <Link href="/atlas">атлас</Link>.
          </Empty>
        ) : items.length ? (
          <ul className="season-list">
            {items.map((it) => (
              <Row key={it.plant_id} it={it} />
            ))}
          </ul>
        ) : (
          <Empty>Записей о сборе в {prep} в книгах атласа нет.</Empty>
        )}
      </section>

      <nav className="season-nav" aria-label="Другие месяцы">
        <Link href={seasonHref(prev)}>← {cap(MONTHS_RU[prev - 1])}</Link>
        <Link href="/season">Все месяцы</Link>
        <Link href={seasonHref(next)}>{cap(MONTHS_RU[next - 1])} →</Link>
      </nav>

      <p className="footnote">
        Сроки сбора взяты из травников и справочников разных лет и записаны так, как их дали авторы. Прежде чем собирать,
        проверь растение по карточке: там сказано, чем оно опасно и с чем его путают.
      </p>
      <Footer />
    </>
  );
}
