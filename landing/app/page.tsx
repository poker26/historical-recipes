import Link from "next/link";
import { getLeaderboard, getRecentBadges } from "./lib";
import { Header, Footer, DownloadButtons, LeaderTable, FeedRow, Medallion } from "./ui";

export default async function Home() {
  const [board, recent] = await Promise.all([getLeaderboard("global", { limit: 5 }), getRecentBadges(8)]);
  const feed = recent?.badges ?? [];

  return (
    <>
      <Header />

      {/* Hero */}
      <section className="hero-grad" style={{ padding: "44px 28px", marginTop: 8 }}>
        <div className="hero-cols" style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 24, alignItems: "center" }}>
          <div>
            <span className="chip">Полевой натуралист в кармане</span>
            <h1 style={{ fontSize: 38, lineHeight: 1.1, margin: "14px 0 12px" }}>
              Узнай, что растёт<br />вокруг тебя
            </h1>
            <p style={{ fontSize: 17, color: "#3f4a43", maxWidth: 460 }}>
              Сфотографируй растение — приложение определит вид и расскажет о нём.
              Гуляй по паркам и лесам, проходи квесты места и сезона, собирай
              коллекционные значки натуралиста.
            </p>
            <div style={{ marginTop: 22 }}>
              <DownloadButtons />
            </div>
          </div>
          <div style={{ display: "flex", justifyContent: "center", gap: 10 }}>
            <Medallion tier={1} size={84} />
            <Medallion tier={2} size={104} />
            <Medallion tier={3} size={84} />
          </div>
        </div>
      </section>

      {/* How it works */}
      <section style={{ marginTop: 40 }}>
        <div className="cols-3">
          {[
            ["📷", "Сфотографируй", "Наведи камеру на растение — узнаешь вид за секунды."],
            ["🗺️", "Пройди квест", "В каждом парке свой набор видов сезона. Найди — получи значок."],
            ["🏅", "Собирай значки", "Каждая находка — красивый значок натуралиста. Собери коллекцию и покажи друзьям."],
          ].map(([icon, t, d]) => (
            <div className="card" key={t}>
              <div style={{ fontSize: 30 }}>{icon}</div>
              <h3 style={{ margin: "8px 0 4px", fontSize: 16 }}>{t}</h3>
              <p style={{ margin: 0, fontSize: 14, color: "#6b7280" }}>{d}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Live social proof: leaderboard teaser + activity feed */}
      <section className="cols-2" style={{ marginTop: 40 }}>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 className="section-title">🏆 Лучшие натуралисты</h2>
            <Link href="/leaderboard" className="navlink-cta" style={{ fontSize: 14 }}>весь рейтинг →</Link>
          </div>
          <LeaderTable rows={board?.top ?? []} />
        </div>
        <div className="card">
          <h2 className="section-title">🌱 Свежие значки</h2>
          {feed.length ? (
            feed.map((b, i) => <FeedRow b={b} key={i} />)
          ) : (
            <p style={{ color: "#6b7280" }}>Скоро здесь появятся первые находки.</p>
          )}
        </div>
      </section>

      {/* CTA */}
      <section className="card" style={{ marginTop: 40, textAlign: "center", background: "#eaf3de", border: "none" }}>
        <h2 style={{ margin: "4px 0 8px" }}>Пошли в лес 🌿</h2>
        <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 460 }}>
          Установи приложение, пройди первый квест рядом с домом и позови друзей —
          соревноваться интереснее вместе.
        </p>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <DownloadButtons />
        </div>
      </section>

      <Footer />
    </>
  );
}
