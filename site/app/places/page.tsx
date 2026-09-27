import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer, DownloadButtons } from "../ui";
import { SectionHead, Empty, MONTHS_GEN_RU } from "../../components/common";
import { pluralRu } from "../../lib/api";
import { REGIONS, regionBySlug } from "../../lib/regions";
import { getPlacesNear } from "../../lib/api-home";
import "./places.css";

export const dynamic = "force-dynamic";

export async function generateMetadata({ searchParams }: { searchParams: { region?: string } }): Promise<Metadata> {
  const r = regionBySlug(searchParams.region);
  return {
    title: `Прогулки: что растёт рядом с ${r.name}`,
    description: `Парки и леса рядом с ${r.name}, где приложение «Что растёт» собирает сезонные наборы видов. Открой место и посмотри, что можно найти в этом месяце.`,
    alternates: { canonical: searchParams.region ? `https://botanik.fun/places?region=${r.slug}` : "https://botanik.fun/places" },
  };
}

const KIND_RU: Record<string, string> = { park: "парк", forest: "лес", garden: "сад", custom: "место", nature_reserve: "заповедник", wood: "лес", meadow: "луг" };

function monthWindow(d = new Date()): string {
  return `month-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export default async function PlacesPage({ searchParams }: { searchParams: { region?: string } }) {
  const region = regionBySlug(searchParams.region);
  const window = monthWindow();
  const res = await getPlacesNear(region, window, 45, 60);
  const places = (res?.places ?? [])
    .filter((p) => p.name)
    .sort((a, b) => (b.set_size ?? 0) - (a.set_size ?? 0) || (a.distance_km ?? 0) - (b.distance_km ?? 0));
  const month = MONTHS_GEN_RU[new Date().getMonth()];

  return (
    <>
      <Header active="/places" />
      <section className="hero-grad" style={{ padding: "36px 32px", marginTop: 16 }}>
        <span className="chip">Прогулки</span>
        <h1 style={{ margin: "12px 0 8px" }}>Что растёт рядом с {region.gen}</h1>
        <p className="lead" style={{ maxWidth: 640 }}>
          У каждого парка и леса есть свой набор видов на этот месяц: его собирают
          наблюдения натуралистов и книги атласа. Открой место, чтобы увидеть, кого можно
          встретить в {month}, и приходи с приложением, чтобы зачесть находки.
        </p>
        <div className="chips" style={{ marginTop: 16 }}>
          {REGIONS.map((r) => (
            <Link key={r.slug} href={r.slug === REGIONS[0].slug ? "/places" : `/places?region=${r.slug}`}
              className={"chip" + (r.slug === region.slug ? " chip-leaf" : "")}>{r.name}</Link>
          ))}
        </div>
      </section>

      <section className="section">
        <SectionHead title={`Места рядом: ${places.length}`} />
        {places.length ? (
          <div className="places-grid">
            {places.map((p) => (
              <Link key={p.id} href={`/place/${p.id}`} className="card card-tight place-card">
                <div className="place-kind">{KIND_RU[p.kind ?? ""] ?? p.kind ?? "место"}{p.distance_km != null ? ` · ${p.distance_km < 10 ? p.distance_km.toFixed(1) : Math.round(p.distance_km)} км от центра` : ""}</div>
                <h3 style={{ margin: "4px 0 6px", fontSize: 19 }}>{p.name}</h3>
                <div className="small muted">
                  {p.set_size ? `${p.set_size} ${pluralRu(p.set_size, "вид", "вида", "видов")} в наборе месяца` : "набор месяца ещё собирается"}
                  {p.target ? ` · значок от ${p.target} находок` : ""}
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <Empty>Рядом с {region.name} пока нет мест с наборами. Приложение создаёт их там, где люди гуляют: выбери другой город или заведи своё место в приложении.</Empty>
        )}
      </section>

      <section className="section card card-soft" style={{ textAlign: "center", padding: "32px 24px" }}>
        <h2 style={{ margin: "4px 0 8px", fontSize: 24 }}>Прогулка начинается в приложении</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 520 }}>
          Приложение «Что растёт» ведёт по месту, узнаёт находки по фотографии и выдаёт значки
          за сезонные наборы. Друзей можно позвать ссылкой на общую прогулку.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>
      <Footer />
    </>
  );
}
