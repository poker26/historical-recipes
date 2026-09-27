import Link from "next/link";
import { getPlaceSet, getLeaderboard, windowLabelRu, seasonEmoji, seasonAdj } from "../../../lib";
import { Header, Footer, DownloadButtons, LeaderTable, Medallion } from "../../../ui";

type Params = { params: { place: string; window: string } };

export async function generateMetadata({ params }: Params) {
  const set = await getPlaceSet(params.place, params.window);
  const name = set?.place.name || "Квест места";
  return {
    title: `${name} — квест`,
    description: `${seasonAdj(params.window)} квест в «${name}»: найди виды сезона и получи значок. Пошли вместе! 🌿`,
  };
}

export default async function QuestPage({ params }: Params) {
  const [set, board] = await Promise.all([
    getPlaceSet(params.place, params.window),
    getLeaderboard("place", { place_id: params.place, limit: 10 }),
  ]);

  return (
    <>
      <Header />

      {!set ? (
        <section className="card" style={{ marginTop: 24, textAlign: "center" }}>
          <h1>Квест не найден</h1>
          <p style={{ color: "#6b7280" }}>Возможно, набор для этого места и сезона ещё не готов.</p>
        </section>
      ) : (
        <>
          <section className="hero-grad" style={{ padding: "32px 28px", marginTop: 12 }}>
            <span className="chip">{seasonEmoji(params.window)} {seasonAdj(params.window)} квест</span>
            <h1 style={{ fontSize: 30, margin: "12px 0 6px" }}>{set.place.name}</h1>
            <p style={{ color: "#3f4a43", margin: 0 }}>{windowLabelRu(params.window)}</p>
            <div style={{ display: "flex", gap: 12, marginTop: 16, alignItems: "center" }}>
              <Medallion tier={1} size={48} />
              <Medallion tier={2} size={56} />
              <Medallion tier={3} size={48} />
              <span style={{ fontSize: 14, color: "#3f4a43" }}>
                {set.items.length} видов · значок от 3 находок
              </span>
            </div>
          </section>

          <section style={{ marginTop: 32 }}>
            <h2 className="section-title">Что искать здесь</h2>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))", gap: 12 }}>
              {set.items.map((it) => (
                <div className="card" key={it.latin_key} style={{ padding: 10 }}>
                  {it.inat_photo ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={it.inat_photo} alt={it.name} style={{ width: "100%", height: 110, objectFit: "cover", borderRadius: 10 }} />
                  ) : (
                    <div style={{ height: 110, borderRadius: 10, background: "#eef4e7", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 30 }}>🌿</div>
                  )}
                  <div style={{ fontWeight: 600, fontSize: 14, marginTop: 6 }}>{it.name}</div>
                  {it.latin ? <div style={{ fontSize: 12, color: "#9ca3af", fontStyle: "italic" }}>{it.latin}</div> : null}
                </div>
              ))}
            </div>
          </section>

          {board?.top?.length ? (
            <section style={{ marginTop: 32 }}>
              <h2 className="section-title">🏆 Лучшие в этом месте</h2>
              <div className="card"><LeaderTable rows={board.top} /></div>
            </section>
          ) : null}

          <section className="card" style={{ marginTop: 32, textAlign: "center", background: "#eaf3de", border: "none" }}>
            <h2 style={{ margin: "4px 0 8px" }}>Пройди этот квест 🌿</h2>
            <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 440 }}>
              Установи «Что растёт», приходи сюда и отмечай находки камерой. Позови друга — вперёд наперегонки!
            </p>
            <div style={{ display: "flex", justifyContent: "center" }}>
              <DownloadButtons />
            </div>
            <div style={{ marginTop: 10 }}>
              <Link href="/leaderboard" className="navlink-cta" style={{ fontSize: 14 }}>← весь рейтинг</Link>
            </div>
          </section>
        </>
      )}

      <Footer />
    </>
  );
}
