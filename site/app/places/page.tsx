import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer, DownloadButtons } from "../ui";
import { SectionHead, Empty, MONTHS_PREP_RU } from "../../components/common";
import { pluralRu } from "../../lib/api";
import { REGIONS, regionBySlug } from "../../lib/regions";
import { getPlacesNear } from "../../lib/api-home";
import "./places.css";

export const dynamic = "force-dynamic";

export async function generateMetadata({ searchParams }: { searchParams: { region?: string } }): Promise<Metadata> {
  const r = regionBySlug(searchParams.region);
  return {
    title: `Места для прогулок рядом с ${r.ins}`,
    description: `Парки и леса рядом с ${r.ins}, для которых приложение «Что растёт» собирает наборы видов на каждый месяц. Открой место и посмотри, что там можно найти сейчас.`,
    alternates: { canonical: searchParams.region ? `https://botanik.fun/places?region=${r.slug}` : "https://botanik.fun/places" },
  };
}

const KIND_RU: Record<string, string> = { park: "Парк", forest: "Лес", garden: "Сад", custom: "Место", nature_reserve: "Заповедник", wood: "Лес", meadow: "Луг" };

function monthWindow(d = new Date()): string {
  return `month-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function distanceRu(km?: number | null): string | null {
  if (km == null) return null;
  const v = km < 10 ? km.toLocaleString("ru-RU", { maximumFractionDigits: 1 }) : String(Math.round(km));
  return `${v} км от центра города`;
}

export default async function PlacesPage({ searchParams }: { searchParams: { region?: string } }) {
  const region = regionBySlug(searchParams.region);
  const window = monthWindow();
  const res = await getPlacesNear(region, window, 45, 60);
  const places = (res?.places ?? [])
    .filter((p) => p.name)
    .sort((a, b) => (b.set_size ?? 0) - (a.set_size ?? 0) || (a.distance_km ?? 0) - (b.distance_km ?? 0));
  const monthPrep = MONTHS_PREP_RU[new Date().getMonth()];

  return (
    <>
      <Header active="/places" />
      <section className="hero-grad" style={{ padding: "36px 32px", marginTop: 16 }}>
        <span className="chip">Прогулки</span>
        <h1 style={{ margin: "12px 0 8px" }}>Что растёт рядом с {region.ins}</h1>
        <p className="lead" style={{ maxWidth: 640 }}>
          Для каждого парка и леса приложение собирает набор видов, которые там можно встретить
          в этом месяце. Набор строится по наблюдениям натуралистов iNaturalist. Открой место,
          чтобы посмотреть, что искать в {monthPrep}. Находка засчитывается, когда ты
          фотографируешь растение в приложении прямо на месте.
        </p>
        <div className="chips" style={{ marginTop: 16 }}>
          {REGIONS.map((r) => (
            <Link key={r.slug} href={r.slug === REGIONS[0].slug ? "/places" : `/places?region=${r.slug}`} scroll={false}
              className={"chip" + (r.slug === region.slug ? " chip-leaf" : "")}>{r.name}</Link>
          ))}
        </div>
      </section>

      <section className="section">
        <SectionHead title={places.length
          ? `${places.length} ${pluralRu(places.length, "место", "места", "мест")} рядом с ${region.ins}`
          : `Места рядом с ${region.ins}`} />
        {places.length ? (
          <div className="places-grid">
            {places.map((p) => (
              <Link key={p.id} href={`/place/${p.id}`} className="card card-tight place-card">
                <div className="place-kind">
                  {KIND_RU[p.kind ?? ""] ?? "Место"}{distanceRu(p.distance_km) ? `, ${distanceRu(p.distance_km)}` : ""}
                </div>
                <h3 style={{ margin: "4px 0 6px", fontSize: 19 }}>{p.name}</h3>
                <div className="small muted">
                  {p.set_size
                    ? `В наборе месяца ${p.set_size} ${pluralRu(p.set_size, "вид", "вида", "видов")}.`
                    : "Набор на этот месяц ещё собирается."}
                  {p.target ? ` Значок дают за ${p.target} ${pluralRu(p.target, "находку", "находки", "находок")}.` : ""}
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <Empty>
            Рядом с {region.ins} пока нет мест с наборами. Приложение заводит их там, где люди
            гуляют. Выбери другой город или создай своё место в приложении.
          </Empty>
        )}
      </section>

      <section className="section card card-soft" style={{ textAlign: "center", padding: "32px 24px" }}>
        <h2 style={{ margin: "4px 0 8px", fontSize: 24 }}>Прогулка начинается в приложении</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 520 }}>
          Приложение «Что растёт» показывает, что искать в выбранном месте, узнаёт находки по
          фотографии и выдаёт значки за собранные наборы. Друга можно позвать ссылкой на общую
          прогулку.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>
      <Footer />
    </>
  );
}
