import type { Metadata } from "next";
import Link from "next/link";
import { getEther, getLeaderboard } from "./lib";
import { Header, Footer, DownloadButtons, LeaderTable, EtherRow } from "./ui";
import { Tile, SectionHead, LeafGlyph, MONTHS_PREP_RU } from "../components/common";
import { RECIPE_KIND_ONE, fmtInt, plantHref, pluralRu, excerpt, realAuthor, titleYear } from "../lib/api";
import { REGIONS, regionBySlug } from "../lib/regions";
import { getSeasonal, getKitchen, getLibraryStats, getOpenBooks, getCorpusCounts, creditLine } from "../lib/api-home";
import "./home.css";

// Живые данные (полка сезона, лента, рейтинг) тянутся с внутреннего backend при каждом
// запросе: иначе Next запечёт главную статически на этапе docker build, где хост
// `backend` недоступен, и в HTML навсегда уедут пустые списки.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export const metadata: Metadata = {
  title: "Что растёт. Атлас растений и грибов, рецепты и библиотека травников",
  description:
    "Атлас растений и грибов, домашние рецепты и библиотека травников с 1790 года. У каждого факта указаны книга, год и страница, а старые книги можно листать целиком.",
  alternates: { canonical: "https://botanik.fun/" },
};

// Адрес страницы берёт показание в именительном падеже, а подпись читается как продолжение
// фразы «Растения, которые применяли…».
const POPULAR: [string, string][] = [
  ["кашель", "при кашле"],
  ["бессонница", "при бессоннице"],
  ["отёки", "при отёках"],
  ["раны", "при ранах"],
  ["желудок", "при болезнях желудка"],
  ["простуда", "при простуде"],
  ["ревматизм", "при ревматизме"],
  ["головная боль", "при головной боли"],
];
const KIND_RU: Record<string, string> = RECIPE_KIND_ONE;

function weekOfYear(d = new Date()): number {
  const start = new Date(d.getFullYear(), 0, 1);
  return Math.floor((d.getTime() - start.getTime()) / (7 * 86400000));
}

export default async function Home({ searchParams }: { searchParams: { region?: string } }) {
  const region = regionBySlug(searchParams.region);
  const [seasonal, kitchen, stats, books, counts, board, ether] = await Promise.all([
    getSeasonal(region, 8),
    getKitchen(6),
    getLibraryStats(),
    getOpenBooks(),
    getCorpusCounts(),
    getLeaderboard("global", { limit: 5 }),
    getEther(24),
  ]);
  const monthPrep = MONTHS_PREP_RU[new Date().getMonth()];
  const harvest = seasonal?.mode === "harvest";
  const shelf = (seasonal?.items ?? []).filter((it) => it.plant_id && it.name);
  const openBooks = books?.items ?? [];
  const bookOfWeek = openBooks.length ? openBooks[weekOfYear() % openBooks.length] : null;

  const seen = new Set<string>();
  const feed = (ether?.events ?? []).filter((e) => {
    const key = `${e.actor?.handle ?? e.actor?.nick}·${e.type === "id" ? e.plant?.id : e.place}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, 7);

  return (
    <>
      <Header active="/" />

      {/* Герой: вопрос, на который отвечает сайт, и три входа */}
      <section className="hero home-hero">
        <div className="hero-cols">
          <div>
            <span className="chip">Продолжение приложения «Что растёт»</span>
            <h1>Что растёт вокруг тебя и что с этим делать</h1>
            <p>
              Про каждое растение собрано, что о нём писали травники, лечебники и справочники
              с 1790 года. У каждого факта указаны книга, год и страница, а скан страницы можно
              открыть и прочитать самому.
            </p>
            <form className="home-search" action="/search" method="get" role="search">
              <input type="search" name="q" placeholder="Крапива, Urtica dioica, водянка или Смельской" aria-label="Поиск по атласу, рецептам и книгам" />
              <button type="submit" className="btn btn-primary">Найти</button>
            </form>
            <div className="chips" style={{ marginTop: 14 }}>
              <span className="muted small" style={{ alignSelf: "center" }}>Растения, которые применяли</span>
              {POPULAR.map(([slug, label]) => (
                <Link key={slug} href={`/atlas/for/${encodeURIComponent(slug)}`} className="chip chip-leaf">{label}</Link>
              ))}
            </div>
          </div>
          <div className="home-entries">
            <Link href="/atlas" className="card home-entry">
              <div className="stat-num">{counts.plants ? fmtInt(counts.plants) : "—"}</div>
              <div>
                <b>{counts.plants ? pluralRu(counts.plants, "вид", "вида", "видов") : "видов"} в атласе.</b>
                <span className="muted"> У каждого есть фотография, очерк и цитаты из книг.</span>
              </div>
            </Link>
            <Link href="/recipes" className="card home-entry">
              <div className="stat-num">{counts.recipes ? fmtInt(counts.recipes) : "—"}</div>
              <div>
                <b>{counts.recipes ? pluralRu(counts.recipes, "пошаговый рецепт", "пошаговых рецепта", "пошаговых рецептов") : "пошаговых рецептов"}.</b>
                <span className="muted"> Их можно повторить дома, каждый взят из книги.</span>
              </div>
            </Link>
            <Link href="/library" className="card home-entry">
              <div className="stat-num">{stats ? fmtInt(stats.books) : "—"}</div>
              <div>
                <b>{stats ? pluralRu(stats.books, "книга", "книги", "книг") : "книг"} в библиотеке.</b>
                <span className="muted">
                  {stats?.year_min ? ` Самая старая издана в ${stats.year_min} году, книги до 1917 года можно листать целиком.` : " Книги до 1917 года можно листать целиком."}
                </span>
              </div>
            </Link>
          </div>
        </div>
      </section>

      {/* Сезонная полка */}
      <section className="section">
        <SectionHead title={harvest ? `Что заготавливают в ${monthPrep}` : `Что сейчас растёт под ${region.ins}`} href="/places" more="все места рядом">
          {/* key: после выбора города адрес меняется, и список закрывается заново собранным элементом */}
          <details className="region-pick" key={region.slug}>
            <summary className="chip">{region.name} ▾</summary>
            <div className="region-list">
              {REGIONS.map((r) => (
                <Link key={r.slug} href={r.slug === REGIONS[0].slug ? "/" : `/?region=${r.slug}`} scroll={false} className={r.slug === region.slug ? "active" : undefined}>{r.name}</Link>
              ))}
            </div>
          </details>
        </SectionHead>
        <p className="section-lead">
          {harvest
            ? `В ${monthPrep} книги советуют собирать эти растения. В карточке каждого написано, какую часть брать и как её сушить.`
            : `Эти виды натуралисты iNaturalist встречают под ${region.ins} в ${monthPrep}. В карточке каждого собрано, что о нём писали старые книги.`}
        </p>
        {shelf.length ? (
          <div className="tiles">
            {shelf.map((it) => (
              <Tile
                key={it.plant_id}
                href={plantHref(it.plant_id, it.latin)}
                name={it.name}
                latin={it.latin}
                photo={it.plant_photo || it.photo}
                credit={it.plant_photo
                  ? creditLine(it.plant_photo_attribution, it.plant_photo_license)
                  : it.photo_attribution ? creditLine(it.photo_attribution, it.photo_license) : "Фото iNaturalist"}
                meta={it.hook ? excerpt(it.hook, 110) : null}
                tags={[
                  ...(it.safety_level === 4 ? [{ label: "смертельно ядовито", warn: true }] : it.safety_level === 3 ? [{ label: "ядовито в больших дозах", warn: true }] : []),
                  ...(it.recipes ? [{ label: `${it.recipes} ${pluralRu(it.recipes, "рецепт", "рецепта", "рецептов")}` }] : []),
                ]}
              />
            ))}
          </div>
        ) : (
          <div className="empty">Для города {region.name} подборка пока пустая. Выбери другой город или открой <Link href="/atlas">атлас</Link>.</div>
        )}
      </section>

      {/* Что приготовить */}
      {kitchen?.items?.length ? (
        <section className="section">
          <SectionHead title={kitchen.title || "Что приготовить"} href="/recipes" more="все рецепты" />
          <p className="section-lead">Пошаговые рецепты из старых книг. Растения из каждого рецепта ведут на свои карточки в атласе.</p>
          <div className="cols-3">
            {kitchen.items.slice(0, 6).map((r) => (
              <Link key={r.id} href={`/recipe/${r.id}`} className="card card-tight recipe-teaser">
                <div className="recipe-teaser-head">
                  <div className="ether-thumb" style={{ width: 44, height: 44, fontSize: 20 }} title={creditLine(r.photo_attribution, r.photo_license) ?? undefined}>
                    {r.photo ? <img src={r.photo} alt="" loading="lazy" /> : <LeafGlyph />}
                  </div>
                  <div>
                    <b>{r.name}</b>
                    <div className="tile-tags" style={{ marginTop: 4 }}>
                      {r.category ? <span className="tag">{r.category}</span> : null}
                      {r.kind && KIND_RU[r.kind] ? <span className="tag">{KIND_RU[r.kind]}</span> : null}
                    </div>
                  </div>
                </div>
                {r.text ? <p className="small" style={{ margin: "8px 0 0", color: "#3f4a43" }}>{excerpt(r.text, 150)}</p> : null}
                <div className="small muted" style={{ marginTop: 6 }}>{r.book ? titleYear(r.book, r.year) : r.year}</div>
              </Link>
            ))}
          </div>
          <p className="footnote" style={{ marginTop: 10 }}>
            Рецепты записаны так, как их дали авторы книг, и не заменяют назначения врача.
            Прежде чем что-то готовить, проверь растение по его карточке.
          </p>
        </section>
      ) : null}

      {/* Книга недели */}
      {bookOfWeek ? (
        <section className="section">
          <SectionHead title="Книга недели" href="/library" more="вся библиотека" />
          <Link href={`/library/${bookOfWeek.id}`} className="card book-week">
            <div className="book-week-cover">
              {bookOfWeek.has_cover ? (
                <img src={`/library/${bookOfWeek.id}/cover.jpg?size=medium`} alt={bookOfWeek.title} loading="lazy" />
              ) : (
                <div className="book-week-typo"><span>{bookOfWeek.title}</span></div>
              )}
            </div>
            <div>
              <div className="chip chip-leaf">можно читать целиком</div>
              <h3 style={{ fontSize: 24, margin: "10px 0 4px" }}>{bookOfWeek.title}</h3>
              <div className="muted">{[realAuthor(bookOfWeek.author), bookOfWeek.year].filter(Boolean).join(", ")}</div>
              <p style={{ margin: "12px 0 0", color: "#3f4a43" }}>
                В книге {fmtInt(bookOfWeek.pages)} {pluralRu(bookOfWeek.pages, "страница", "страницы", "страниц")}.
                {" "}Из неё в атлас вошли {fmtInt(bookOfWeek.plants)} {pluralRu(bookOfWeek.plants, "растение", "растения", "растений")}
                {" "}и {fmtInt(bookOfWeek.uses)} {pluralRu(bookOfWeek.uses, "цитата", "цитаты", "цитат")} о применении.
                {" "}Срок авторских прав на книгу давно истёк, поэтому её можно листать страницу за страницей.
              </p>
            </div>
          </Link>
        </section>
      ) : null}

      {/* Живая активность */}
      <section className="section cols-2">
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ margin: 0, fontSize: 22 }}>Лучшие натуралисты</h2>
            <Link href="/leaderboard" className="navlink-cta" style={{ fontSize: 14 }}>весь рейтинг</Link>
          </div>
          <LeaderTable rows={board?.top ?? []} />
        </div>
        <div className="card">
          <h2 style={{ margin: "0 0 6px", fontSize: 22 }}>Свежие находки</h2>
          {feed.length ? feed.map((e, i) => <EtherRow e={e} key={i} />) : <p className="muted">Пока никто ничего не нашёл. Первая находка появится здесь сразу после определения в приложении.</p>}
        </div>
      </section>

      {/* Приложение */}
      <section className="section card card-soft" style={{ textAlign: "center", padding: "36px 24px" }}>
        <h2 style={{ margin: "4px 0 10px", fontSize: 28 }}>В лесу удобнее с телефоном</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 22px", maxWidth: 560 }}>
          Приложение «Что растёт» узнаёт растение или гриб по фотографии, хранит твои находки и
          подбирает прогулки по местам и сезонам. Карточки видов в нём те же, что в атласе.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>

      <Footer />
    </>
  );
}
