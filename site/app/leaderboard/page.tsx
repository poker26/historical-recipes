import Link from "next/link";
import { getLeaderboard, windowLabelRu } from "../lib";
import { Header, Footer, LeaderTable } from "../ui";

export const metadata = { title: "Рейтинг натуралистов" };

/** Current half-month window label + year (mirrors the app/backend canon). */
function currentWindow(): { window: string; year: number } {
  const d = new Date();
  const half = d.getDate() <= 15 ? "first" : "second";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return { window: `${half}-half-${mm}`, year: d.getFullYear() };
}

type Search = { searchParams: { scope?: string; place_id?: string; window?: string; year?: string } };

export default async function LeaderboardPage({ searchParams }: Search) {
  const cur = currentWindow();
  const scope = (["global", "place", "season"].includes(searchParams.scope || "")
    ? searchParams.scope
    : "global") as "global" | "place" | "season";
  const window = searchParams.window || cur.window;
  const year = parseInt(searchParams.year || "", 10) || cur.year;
  const placeId = searchParams.place_id;

  const board = await getLeaderboard(scope, { place_id: placeId, window, year, limit: 50 });

  const tab = (key: string, label: string, href: string) => (
    <Link
      href={href}
      className="chip"
      style={{ background: scope === key ? "#2e7d32" : "#e8f5e9", color: scope === key ? "#fff" : "#1b5e20" }}
    >
      {label}
    </Link>
  );

  return (
    <>
      <Header />
      <section style={{ marginTop: 16 }}>
        <h1 className="section-title" style={{ fontSize: 26 }}>🏆 Рейтинг натуралистов</h1>
        <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
          {tab("global", "Все время", "/leaderboard")}
          {tab("season", `Сезон: ${windowLabelRu(window, year)}`, `/leaderboard?scope=season&window=${window}&year=${year}`)}
          {placeId ? tab("place", "По месту", `/leaderboard?scope=place&place_id=${placeId}`) : null}
        </div>
        <div className="card">
          <LeaderTable rows={board?.top ?? []} />
        </div>
        <p style={{ color: "#9ca3af", fontSize: 13, marginTop: 12 }}>
          Очки = сумма лучших ступеней значков по местам и сезонам. Считает сервер.
        </p>
      </section>
      <Footer />
    </>
  );
}
