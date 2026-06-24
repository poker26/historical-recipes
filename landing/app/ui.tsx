// Shared visual components for the landing — all server components (no client JS).
import Link from "next/link";
import {
  Badge, LeaderRow, tierColors, cap, seasonEmoji, seasonAdj, badgeLabel,
  windowLabelRu, RUSTORE_URL, APPSTORE_URL,
} from "./lib";

/** A collectible medallion — metal ring by tier + leaf + tier stars. Mirrors the
 *  app's BadgeMedallion so a значок looks the same on phone and web. */
export function Medallion({ tier, size = 64, earned = true }: { tier: number; size?: number; earned?: boolean }) {
  const [light, metal] = tierColors(tier);
  return (
    <div
      style={{
        width: size, height: size, borderRadius: "50%",
        background: earned ? light : "#F6F6F4",
        border: `${earned ? 3 : 2}px solid ${earned ? metal : metal + "73"}`,
        display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
        opacity: earned ? 1 : 0.55, flex: "0 0 auto",
        boxShadow: earned ? `0 2px 8px ${metal}33` : "none",
      }}
    >
      <span style={{ fontSize: size * 0.34, lineHeight: 1 }}>🌿</span>
      <span style={{ fontSize: size * 0.17, color: metal, letterSpacing: 1 }}>{"★".repeat(tier)}</span>
    </div>
  );
}

/** One earned-badge tile (medallion + «тир · место/биотоп» + ordinal). */
export function BadgeTile({ b }: { b: Badge }) {
  const sub = b.kind === "biotope"
    ? "значок мастерства"
    : `${seasonEmoji(b.window)} ${seasonAdj(b.window).toLowerCase()} сезон`;
  return (
    <div style={{ width: 124, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
      <Medallion tier={b.tier} />
      <div style={{ fontWeight: 600, fontSize: 13, lineHeight: 1.2 }}>{badgeLabel(b)}</div>
      <div style={{ fontSize: 12, color: "#6b7280" }}>{sub}</div>
      {b.ordinal ? <div style={{ fontSize: 11, color: "#9ca3af" }}>№ {b.ordinal}</div> : null}
    </div>
  );
}

/** Activity-feed line for the recent-badges feed. */
export function FeedRow({ b }: { b: Badge }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 0", borderBottom: "1px solid #eef0ea" }}>
      <Medallion tier={b.tier} size={38} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14 }}>
          <b>{b.nick}</b> — значок «{b.place || "место"}»
        </div>
        <div style={{ fontSize: 12, color: "#6b7280" }}>
          {cap(b.name)} · {seasonAdj(b.window).toLowerCase()} сезон{b.ordinal ? ` · № ${b.ordinal}` : ""}
        </div>
      </div>
    </div>
  );
}

/** Compact leaderboard table. */
export function LeaderTable({ rows, highlight }: { rows: LeaderRow[]; highlight?: string }) {
  if (!rows.length) return <p style={{ color: "#6b7280" }}>Пока никого — будь первым!</p>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {rows.map((r, i) => (
        <div
          key={i}
          style={{
            display: "flex", alignItems: "center", gap: 12, padding: "8px 12px", borderRadius: 10,
            background: highlight && r.nick === highlight ? "#FFF8E1" : i % 2 ? "#fbfdf8" : "transparent",
          }}
        >
          <span style={{ width: 28, fontWeight: 700, color: r.rank && r.rank <= 3 ? "#B8860B" : "#9ca3af" }}>
            {r.rank ?? "—"}
          </span>
          <span style={{ flex: 1 }}>{r.nick}</span>
          <span style={{ fontWeight: 700 }}>{r.score}</span>
          <span style={{ fontSize: 12, color: "#9ca3af", width: 64, textAlign: "right" }}>
            {r.badges} знач.
          </span>
        </div>
      ))}
    </div>
  );
}

/** Download buttons (RuStore live; App Store «скоро» until public release). */
export function DownloadButtons() {
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      <a href={RUSTORE_URL} target="_blank" rel="noopener" className="btn btn-primary">
        📲 Скачать в RuStore
      </a>
      {APPSTORE_URL ? (
        <a href={APPSTORE_URL} target="_blank" rel="noopener" className="btn btn-ghost"> Скачать в App Store</a>
      ) : (
        <span className="btn btn-disabled"> App Store — скоро</span>
      )}
    </div>
  );
}

/** Avatar disc framed by the level wreath, for the profile page (browser, not OG). */
export function ProfileCrest({ avatar, level, size = 168 }: { avatar?: string | null; level: number; size?: number }) {
  const a = Math.round(size * 0.6);
  const off = (size - a) / 2;
  const w = Math.min(5, Math.max(1, level));
  return (
    <div style={{ position: "relative", width: size, height: size, flex: "0 0 auto" }}>
      {avatar ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`/avatars/${avatar}.png`} width={a} height={a} alt="" style={{ position: "absolute", top: off, left: off, borderRadius: a / 2 }} />
      ) : (
        <div style={{ position: "absolute", top: off, left: off, width: a, height: a, borderRadius: a / 2, background: "#cfe3cf" }} />
      )}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`/wreaths/${w}.png`} width={size} height={size} alt="" style={{ position: "absolute", top: 0, left: 0 }} />
    </div>
  );
}

export function Header() {
  return (
    <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 0" }}>
      <Link href="/" style={{ display: "flex", alignItems: "center", gap: 8, textDecoration: "none", color: "#1B5E20" }}>
        <span style={{ fontSize: 26 }}>🌿</span>
        <b style={{ fontSize: 18 }}>Что растёт</b>
      </Link>
      <nav style={{ display: "flex", gap: 18 }}>
        <Link href="/leaderboard" className="navlink">Рейтинг</Link>
        <a href={RUSTORE_URL} target="_blank" rel="noopener" className="navlink navlink-cta">Скачать</a>
      </nav>
    </header>
  );
}

export function Footer() {
  return (
    <footer style={{ marginTop: 56, padding: "24px 0", borderTop: "1px solid #eef0ea", color: "#9ca3af", fontSize: 13 }}>
      <div>«Что растёт» — определяй растения, проходи квесты, собирай значки.</div>
      <div style={{ marginTop: 4 }}>Места и наблюдения — обобщённо; точные координаты не публикуются.</div>
    </footer>
  );
}

export const WindowLabel = windowLabelRu;
