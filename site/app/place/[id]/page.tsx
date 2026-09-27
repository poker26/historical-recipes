import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer, DownloadButtons, LeaderTable, Medallion } from "../../ui";
import { getLeaderboard } from "../../lib";
import { Tile, SectionHead, Crumbs, MONTHS_RU } from "../../../components/common";
import { isUuid, plantHref, pluralRu } from "../../../lib/api";
import { getPlaceSet, getPlaceParticipants, getPlaceBiotopes } from "../../../lib/api-home";
import "../../places/places.css";

export const dynamic = "force-dynamic";

type Params = { params: { id: string } };

function monthWindow(d = new Date()): string {
  return `month-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  if (!isUuid(params.id)) return { title: "Место" };
  const set = await getPlaceSet(params.id, monthWindow());
  const name = set?.place?.name;
  if (!name) return { title: "Место", robots: { index: false } };
  const month = MONTHS_RU[new Date().getMonth()];
  return {
    title: `${name}: что растёт в ${month === "май" ? "мае" : month.replace(/ь$/, "е").replace(/й$/, "е")}`,
    description: `Набор видов месяца для места «${name}»: ${set?.place?.set_size ?? 0} растений, которых здесь можно встретить сейчас, и кто их уже нашёл.`,
    alternates: { canonical: `https://botanik.fun/place/${params.id}` },
  };
}

export default async function PlacePage({ params }: Params) {
  if (!isUuid(params.id)) notFound();
  const window = monthWindow();
  const year = new Date().getFullYear();
  const [plants, fungi, people, biotopes, board] = await Promise.all([
    getPlaceSet(params.id, window, "plants"),
    getPlaceSet(params.id, window, "fungi"),
    getPlaceParticipants(params.id, window, year),
    getPlaceBiotopes(params.id, window),
    getLeaderboard("place", { place_id: params.id, limit: 10 }),
  ]);
  const place = plants?.place ?? fungi?.place;
  if (!place) notFound();
  const month = MONTHS_RU[new Date().getMonth()];
  const items = (plants?.items ?? []).filter((it) => it.name);
  const fungiItems = (fungi?.items ?? []).filter((it) => it.name);

  const group = (title: string, list: typeof items, notice?: string | null) => (
    <section className="section">
      <SectionHead title={title} />
      {notice ? <p className="section-lead">{notice}</p> : null}
      {list.length ? (
        <div className="tiles tiles-sm">
          {list.map((it) => {
            const href = it.plant_id ? plantHref(it.plant_id, it.latin) : `/search?q=${encodeURIComponent(it.latin || it.name)}`;
            return <Tile key={it.latin_key} href={href} name={it.name} latin={it.latin} photo={it.inat_photo} meta={it.plant_id ? "есть в атласе" : "пока без карточки"} />;
          })}
        </div>
      ) : (
        <div className="empty">Набор на этот месяц ещё не собрался.</div>
      )}
    </section>
  );

  return (
    <>
      <Header active="/places" />
      <Crumbs items={[{ href: "/places", label: "Прогулки" }, { label: place.name }]} />
      <section className="hero-grad" style={{ padding: "32px 28px" }}>
        <span className="chip">Место · {month}</span>
        <h1 style={{ margin: "12px 0 6px" }}>{place.name}</h1>
        <p className="lead" style={{ maxWidth: 640 }}>
          {place.set_size
            ? `В наборе этого месяца ${place.set_size} ${pluralRu(place.set_size, "вид", "вида", "видов")}. Найди ${place.target} из них с приложением и получи значок места.`
            : "Набор видов для этого места ещё собирается."}
          {typeof place.out_of_season === "number" && place.out_of_season > 0 ? ` ${place.out_of_season} ${pluralRu(place.out_of_season, "вид сейчас не в сезоне", "вида сейчас не в сезоне", "видов сейчас не в сезоне")}.` : ""}
        </p>
        <div style={{ display: "flex", gap: 12, marginTop: 16, alignItems: "center", flexWrap: "wrap" }}>
          <Medallion tier={1} size={44} /><Medallion tier={2} size={52} /><Medallion tier={3} size={44} />
          {biotopes?.biotopes?.length ? (
            <span className="chips">
              {biotopes.biotopes.map((b) => (
                <Link key={b.key} href={`/biotopes/${encodeURIComponent(b.key)}`} className="chip">{b.key}{b.species_count ? ` · ${b.species_count}` : ""}</Link>
              ))}
            </span>
          ) : null}
        </div>
      </section>

      {group("Что искать здесь в этом месяце", items, plants?.safety_notice)}
      {fungiItems.length ? group("Грибы этого места", fungiItems, fungi?.safety_notice ?? "Грибы определяй только по надёжным признакам и никогда не пробуй незнакомые.") : null}

      {people?.participants?.length ? (
        <section className="section">
          <SectionHead title={`Здесь уже гуляли: ${people.count}`} />
          <div className="people">
            {people.participants.slice(0, 24).map((p, i) => (
              <Link key={i} href={p.handle ? `/p/${p.handle}` : "#"} className="person" style={{ textDecoration: "none", color: "var(--ink)" }}>
                {p.avatar ? <img src={`/avatars/${p.avatar}.png`} alt="" loading="lazy" /> : <span className="person-dot" />}
                <span>{p.nick}</span>
              </Link>
            ))}
          </div>
        </section>
      ) : null}

      {board?.top?.length ? (
        <section className="section">
          <SectionHead title="Лучшие в этом месте" href={`/leaderboard?scope=place&place_id=${params.id}`} more="полный список" />
          <div className="card"><LeaderTable rows={board.top} /></div>
        </section>
      ) : null}

      <section className="section card card-soft" style={{ textAlign: "center", padding: "32px 24px" }}>
        <h2 style={{ margin: "4px 0 8px", fontSize: 24 }}>Пройти это место с приложением</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 480 }}>
          Приложение узнаёт находки по фотографии и зачитывает их в набор месяца. Позови друга:
          общую прогулку можно начать ссылкой.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>
      <Footer />
    </>
  );
}
