import type { Metadata } from "next";
import Link from "next/link";
import { getEther, getLeaderboard } from "./lib";
import { Header, Footer, DownloadButtons, LeaderTable, EtherRow } from "./ui";
import { Tile, SectionHead, LeafGlyph, MONTHS_GEN_RU } from "../components/common";
import { fmtInt, plantHref, pluralRu, excerpt } from "../lib/api";
import { REGIONS, regionBySlug } from "../lib/regions";
import { getSeasonal, getKitchen, getLibraryStats, getOpenBooks, getCorpusCounts, creditLine } from "../lib/api-home";
import "./home.css";

// Живые данные (полка сезона, лента, рейтинг) тянутся с внутреннего backend при каждом
// запросе: иначе Next запечёт главную статически на этапе docker build, где хост
// `backend` недоступен, и в HTML навсегда уедут пустые списки.
export const dynamic = "force-dynamic";
export const revalidate = 0;

export const metadata: Metadata = {
  title: "Что растёт — атлас растений и грибов, рецепты и библиотека травников",
  description:
    "Что растёт рядом с тобой сейчас и что с этим делать: атлас видов, домашние рецепты и страницы травников с 1790 года. Каждый факт стоит на книге, годе и странице.",
  alternates: { canonical: "https://botanik.fun/" },
};

const POPULAR = ["кашель", "бессонница", "отёки", "раны", "желудок", "простуда", "ревматизм", "головная боль"];
const KIND_RU: Record<string, string> = { medicinal: "лечебное", food: "еда", cosmetic: "косметика", other: "прочее" };

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
  const month = MONTHS_GEN_RU[new Date().getMonth()];
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
              Атлас растений и грибов, домашние рецепты и библиотека травников с 1790 года.
              Каждый факт здесь стоит на книге, годе и странице: можно открыть скан и прочитать
              самому.
            </p>
            <form className="home-search" action="/search" method="get" role="search">
              <input type="search" name="q" placeholder="Крапива, Urtica dioica, водянка или «травник 1870»" aria-label="Поиск" />
              <button type="submit" className="btn btn-primary">Найти</button>
            </form>
            <div className="chips" style={{ marginTop: 14 }}>
              <span className="muted small" style={{ alignSelf: "center" }}>Растения при:</span>
              {POPULAR.map((p) => (
                <Link key={p} href={`/atlas/for/${encodeURIComponent(p)}`} className="chip chip-leaf">{p}</Link>
              ))}
            </div>
          </div>
          <div className="home-entries">
            <Link href="/atlas" className="card home-entry">
              <div className="stat-num">{counts.plants ? fmtInt(counts.plants) : "—"}</div>
              <div><b>Атлас</b><span className="muted"> · виды с фотографией, очерком и цитатами</span></div>
            </Link>
            <Link href="/recipes" className="card home-entry">
              <div className="stat-num">{counts.recipes ? fmtInt(counts.recipes) : "—"}</div>
              <div><b>Рецепты</b><span className="muted"> · пошаговые домашние рецепты из книг</span></div>
            </Link>
            <Link href="/library" className="card home-entry">
              <div className="stat-num">{stats ? fmtInt(stats.books) : "—"}</div>
              <div><b>Библиотека</b><span className="muted">{stats?.year_min ? ` · книги с ${stats.year_min} по ${stats.year_max} год` : " · книги и сканы страниц"}</span></div>
            </Link>
          </div>
        </div>
      </section>

      {/* Сезонная полка */}
      <section className="section">
        <SectionHead title={seasonal?.title || `Сейчас в лесу под ${region.gen}`} href="/places" more="все места рядом">
          <details className="region-pick">
            <summary className="chip">{region.name} ▾</summary>
            <div className="region-list">
              {REGIONS.map((r) => (
                <Link key={r.slug} href={r.slug === REGIONS[0].slug ? "/" : `/?region=${r.slug}`} className={r.slug === region.slug ? "active" : undefined}>{r.name}</Link>
              ))}
            </div>
          </details>
        </SectionHead>
        <p className="section-lead">
          {seasonal?.mode === "harvest"
            ? `Что заготавливают в ${month}: виды из книг о сборе и сушке, которые сейчас в поре.`
            : `Виды, которых натуралисты iNaturalist встречают в ${month} рядом с ${region.gen}, и что о каждом написано в книгах.`}
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
                  : it.photo_attribution ? creditLine(it.photo_attribution, it.photo_license) : "фото: iNaturalist"}
                meta={it.hook ? excerpt(it.hook, 110) : it.recipes ? `${it.recipes} ${pluralRu(it.recipes, "рецепт", "рецепта", "рецептов")}` : null}
                tags={[
                  ...(it.safety_level === 4 ? [{ label: "смертельно ядовито", warn: true }] : it.safety_level === 3 ? [{ label: "осторожно", warn: true }] : []),
                  ...(it.recipes ? [{ label: `${it.recipes} ${pluralRu(it.recipes, "рецепт", "рецепта", "рецептов")}` }] : []),
                ]}
              />
            ))}
          </div>
        ) : (
          <div className="empty">Для этого региона полка сезона пока не собралась. Загляни в <Link href="/atlas">атлас</Link> или выбери другой город.</div>
        )}
      </section>

      {/* Что приготовить */}
      {kitchen?.items?.length ? (
        <section className="section">
          <SectionHead title={kitchen.title || "Что приготовить"} href="/recipes" more="все рецепты" />
          <p className="section-lead">Пошаговые рецепты из книг, у которых ингредиенты сверены с атласом.</p>
          <div className="cols-3">
            {kitchen.items.slice(0, 6).map((r) => (
              <Link key={r.id} href={`/recipe/${r.id}`} className="card card-tight recipe-teaser">
                <div className="recipe-teaser-head">
                  <div className="ether-thumb" style={{ width: 44, height: 44, fontSize: 20 }} title={creditLine(r.photo_attribution, r.photo_license) ?? undefined}>
                    {r.photo ? <img src={r.photo} alt="" loading="lazy" /> : <LeafGlyph />}
                  </div>
                  <div>
                    <b>{r.name}</b>
                    <div className="small muted">{[r.category, r.kind ? KIND_RU[r.kind] ?? r.kind : null].filter(Boolean).join(" · ")}</div>
                  </div>
                </div>
                {r.text ? <p className="small" style={{ margin: "8px 0 0", color: "#3f4a43" }}>{excerpt(r.text, 150)}</p> : null}
                <div className="small muted" style={{ marginTop: 6 }}>{[r.book, r.year].filter(Boolean).join(", ")}</div>
              </Link>
            ))}
          </div>
          {kitchen.disclaimer ? <p className="footnote" style={{ marginTop: 10 }}>{kitchen.disclaimer}</p> : null}
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
              <div className="muted">{[bookOfWeek.author, bookOfWeek.year].filter(Boolean).join(", ")}</div>
              <p style={{ margin: "12px 0 0", color: "#3f4a43" }}>
                {fmtInt(bookOfWeek.pages)} {pluralRu(bookOfWeek.pages, "страница", "страницы", "страниц")} скана,
                {" "}{fmtInt(bookOfWeek.plants)} {pluralRu(bookOfWeek.plants, "растение", "растения", "растений")} в атласе,
                {" "}{fmtInt(bookOfWeek.uses)} {pluralRu(bookOfWeek.uses, "цитата", "цитаты", "цитат")} о применении.
                Книга свободна от авторских прав, поэтому её страницы можно листать прямо на сайте.
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
          {feed.length ? feed.map((e, i) => <EtherRow e={e} key={i} />) : <p className="muted">Скоро здесь появятся первые находки.</p>}
        </div>
      </section>

      {/* Приложение */}
      <section className="section card card-soft" style={{ textAlign: "center", padding: "36px 24px" }}>
        <h2 style={{ margin: "4px 0 10px", fontSize: 28 }}>В лесу удобнее с телефоном</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 22px", maxWidth: 560 }}>
          Приложение «Что растёт» определяет вид по фотографии, ведёт коллекцию находок и
          собирает прогулки по местам и сезонам. Сайт показывает то же и добавляет книги целиком.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>

      <Footer />
    </>
  );
}
