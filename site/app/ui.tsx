// Shared visual components for the landing — all server components (no client JS).
import Link from "next/link";
import {
  Badge, EtherEvent, LeaderRow, tierColors, cap, seasonEmoji, seasonAdj, badgeLabel,
  windowLabelRu, agoRu, pluralRu, RUSTORE_URL, APPSTORE_URL,
} from "./lib";

/** A collectible medallion — metal ring by tier + leaf + tier stars. Mirrors the
 *  app's BadgeMedallion so a значок looks the same on phone and web. */
export function Medallion({ tier, size = 64, earned = true }: { tier: number; size?: number; earned?: boolean }) {
  // Настоящий арт значков с сервера (тот же, что в приложении) вместо эмодзи-заглушки.
  // Ярус: 1 бронза, 2 серебро, 3 золото. Ободок из tierColors держит форму, пока
  // грузится картинка, и делает «незаработанный» значок приглушённым.
  const [light, metal] = tierColors(tier);
  const art = ["bronze", "silver", "gold"][Math.min(Math.max(tier, 1), 3) - 1];
  return (
    <div
      style={{
        width: size, height: size, borderRadius: "50%",
        background: earned ? light : "#F6F6F4",
        border: `${earned ? 3 : 2}px solid ${earned ? metal : metal + "73"}`,
        display: "flex", alignItems: "center", justifyContent: "center",
        opacity: earned ? 1 : 0.45, flex: "0 0 auto", overflow: "hidden",
        boxShadow: earned ? `0 2px 8px ${metal}33` : "none",
      }}
    >
      <img
        src={`https://botanik.fun/badges/badge-${art}.png`}
        alt=""
        width={Math.round(size * 0.78)}
        height={Math.round(size * 0.78)}
        style={{ objectFit: "contain" }}
      />
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

/** Строка «Эфира»: кто, что нашёл и примерно где. Это живая лента находок —
 *  она обновляется каждый день, в отличие от значков, которые выдаются редко. */
export function EtherRow({ e }: { e: EtherEvent }) {
  const nick = e.actor?.nick || e.actor?.handle || "натуралист";
  const photo = e.plant?.photo || null;
  return (
    <div className="feed-row">
      {e.type === "id" ? (
        <div className="ether-thumb">
          {photo ? <img src={photo} alt="" /> : <span>🌿</span>}
        </div>
      ) : (
        <Medallion tier={e.tier ?? 1} size={38} />
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14, lineHeight: 1.35 }}>
          <b>{nick}</b>{" "}
          {e.type === "id" ? (
            <>нашёл {e.plant ? <>вид «{e.plant.name}»</> : "растение"}</>
          ) : (
            <>получил значок «{e.place || "место"}»</>
          )}
          {e.place && e.type === "id" ? <> в месте «{e.place}»</> : null}
        </div>
        <div style={{ fontSize: 12, color: "#6b7368" }}>{agoRu(e.at)}</div>
      </div>
    </div>
  );
}

/** Compact leaderboard table. */
export function LeaderTable({ rows, highlight }: { rows: LeaderRow[]; highlight?: string }) {
  if (!rows.length) return <p style={{ color: "#6b7280" }}>В рейтинге пока никого нет. Первый значок места поставит тебя на первую строчку.</p>;
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
          <span style={{ fontWeight: 700 }}>{r.score} {pluralRu(r.score, "очко", "очка", "очков")}</span>
          <span style={{ fontSize: 12, color: "#9ca3af", width: 76, textAlign: "right" }}>
            {r.badges} {pluralRu(r.badges, "значок", "значка", "значков")}
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
        Скачать в RuStore
      </a>
      {APPSTORE_URL ? (
        <a href={APPSTORE_URL} target="_blank" rel="noopener" className="btn btn-ghost">Скачать в App Store</a>
      ) : (
        <span className="btn btn-disabled">Скоро в App Store</span>
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

/** «Приложение» в шапке: меню с обоими магазинами. Без JS, на <details>; прозрачная
 *  подложка под открытым меню (globals.css) закрывает его щелчком мимо. */
function AppMenu() {
  return (
    <details className="header-app">
      <summary>Приложение</summary>
      <div className="header-app-list">
        <a href={RUSTORE_URL} target="_blank" rel="noopener">
          Скачать в RuStore{" "}
          <small>для Android</small>
        </a>
        {APPSTORE_URL ? (
          <a href={APPSTORE_URL} target="_blank" rel="noopener">
            Скачать в App Store{" "}
            <small>для iPhone и iPad</small>
          </a>
        ) : (
          <span className="dis">
            Скоро в App Store{" "}
            <small>для iPhone и iPad</small>
          </span>
        )}
      </div>
    </details>
  );
}

const NAV: { href: string; label: string }[] = [
  { href: "/atlas", label: "Атлас" },
  { href: "/recipes", label: "Рецепты" },
  { href: "/library", label: "Библиотека" },
  { href: "/reference", label: "Справочники" },
  { href: "/places", label: "Прогулки" },
  { href: "/collection", label: "Коллекция" },
];

/** Шапка сайта: разделы, поиск, ссылка на приложение. Серверный компонент без JS;
 *  активный раздел подсвечивается по переданному пути. */
export function Header({ active, q }: { active?: string; q?: string } = {}) {
  return (
    <header className="site-header">
      <div className="wrap">
        <Link href="/" className="brand" aria-label="Что растёт, на главную">
          <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <path d="M52 10C30 10 14 24 12 50c26 2 40-14 40-40Z" fill="#2d5e48" />
            <path d="M14 50C24 38 34 28 50 12" stroke="#fff8ec" strokeWidth="2.5" strokeLinecap="round" />
          </svg>
          Что растёт
          <small>botanik.fun</small>
        </Link>
        <nav className="nav" aria-label="Разделы">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={active === n.href ? "active" : undefined}>{n.label}</Link>
          ))}
        </nav>
        <form className="search" action="/search" method="get" role="search">
          <input type="search" name="q" defaultValue={q ?? ""} placeholder="Растение, гриб, симптом или книга" aria-label="Поиск по атласу, рецептам и книгам" />
          <button type="submit">Найти</button>
        </form>
        <AppMenu />
      </div>
    </header>
  );
}

export function Footer() {
  return (
    <footer className="site-footer">
      <div className="wrap">
        <div>
          <h4>Что растёт</h4>
          <p style={{ margin: "0 0 10px", maxWidth: 420 }}>
            Атлас растений и грибов, домашние рецепты и библиотека травников с 1790 года.
            У каждого факта указаны книга, год и страница, откуда он взят.
          </p>
          <p className="fine" style={{ margin: 0 }}>
            Это справочник по истории применения растений, а не медицинский совет.
            Места находок показаны обобщённо, точные координаты не публикуются.
          </p>
        </div>
        <div>
          <h4>Разделы</h4>
          <Link href="/atlas">Атлас</Link>
          <Link href="/recipes">Рецепты</Link>
          <Link href="/library">Библиотека</Link>
          <Link href="/reference">Справочники</Link>
          <Link href="/places">Прогулки и места</Link>
        </div>
        <div>
          <h4>Сообщество</h4>
          <Link href="/leaderboard">Рейтинг натуралистов</Link>
          <Link href="/collection">Коллекция</Link>
        </div>
        <div>
          <h4>Приложение</h4>
          <a href={RUSTORE_URL} target="_blank" rel="noopener">RuStore</a>
          {APPSTORE_URL ? <a href={APPSTORE_URL} target="_blank" rel="noopener">App Store</a> : null}
          <p className="fine" style={{ margin: "10px 0 0" }}>Фотографии видов взяты из iNaturalist и Викимедиа, автор и лицензия указаны у каждого снимка.</p>
        </div>
      </div>
    </footer>
  );
}

export const WindowLabel = windowLabelRu;
