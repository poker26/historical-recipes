import type { Metadata } from "next";
import { getProfile, pluralRu, cap } from "../../lib";
import { Header, Footer, DownloadButtons, BadgeTile, ProfileCrest } from "../../ui";

type Params = { params: { id: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const p = await getProfile(params.id);
  if (!p) return { title: "Натуралист" };
  const badges = p.badges.length;
  const title = `${p.nick} — ${cap(p.level.title)}`;
  const desc =
    `Уровень ${p.level.n} · ${p.level.species} ${pluralRu(p.level.species, "вид", "вида", "видов")}` +
    `${badges ? ` · ${badges} ${pluralRu(badges, "значок", "значка", "значков")}` : ""}` +
    `${p.rank ? ` · #${p.rank} в рейтинге` : ""}. Вот какой я ботаник 🌿`;
  return {
    title,
    description: desc,
    openGraph: { title: `${title} · Что растёт`, description: desc, type: "profile" },
    twitter: { card: "summary", title, description: desc },
  };
}

export default async function ProfilePage({ params }: Params) {
  const p = await getProfile(params.id);

  return (
    <>
      <Header />

      {!p ? (
        <section className="card" style={{ marginTop: 24, textAlign: "center" }}>
          <h1>Натуралист не найден</h1>
          <p style={{ color: "#6b7280" }}>Ссылка устарела или профиль ещё не создан.</p>
        </section>
      ) : (
        <>
          {/* Passport */}
          <section className="hero-grad" style={{ padding: "28px 28px", marginTop: 12, display: "flex", gap: 24, alignItems: "center", flexWrap: "wrap" }}>
            <ProfileCrest avatar={p.avatar} level={p.level.n} />
            <div style={{ flex: 1, minWidth: 240 }}>
              <div style={{ fontSize: 13, color: "#5a6b5f", fontWeight: 600 }}>ПАСПОРТ НАТУРАЛИСТА</div>
              <h1 style={{ fontSize: 32, margin: "6px 0 10px" }}>{p.nick}</h1>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                <span className="chip">🌿 {cap(p.level.title)} · уровень {p.level.n}</span>
                <span className="chip">{p.level.species} {pluralRu(p.level.species, "вид", "вида", "видов")}</span>
                {p.rank ? <span className="chip">#{p.rank} в рейтинге</span> : null}
                <span className="chip">{p.score} очков</span>
              </div>
            </div>
          </section>

          {/* Badge shelf */}
          <section style={{ marginTop: 32 }}>
            <h2 className="section-title">
              Значки {p.badges.length ? `(${p.badges.length})` : ""}
            </h2>
            {p.badges.length ? (
              <div className="card grid-badges">
                {p.badges.map((b, i) => <BadgeTile b={b} key={i} />)}
              </div>
            ) : (
              <p style={{ color: "#6b7280" }}>Пока без значков — но всё впереди!</p>
            )}
          </section>

          {/* CTA */}
          <section className="card" style={{ marginTop: 32, textAlign: "center", background: "#eaf3de", border: "none" }}>
            <h2 style={{ margin: "4px 0 8px" }}>Хочешь так же?</h2>
            <p style={{ color: "#3f4a43", margin: "0 auto 18px", maxWidth: 420 }}>
              Установи «Что растёт», определяй растения и собирай свои значки натуралиста.
            </p>
            <div style={{ display: "flex", justifyContent: "center" }}>
              <DownloadButtons />
            </div>
          </section>
        </>
      )}

      <Footer />
    </>
  );
}
