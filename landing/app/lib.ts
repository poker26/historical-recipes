// Server-side data access + small shared helpers for the botanik.fun landing.
// All fetches run on the server against the INTERNAL backend (same docker network),
// so nothing here is exposed to the browser and no public-API whitelist/CORS is
// needed. GEOPRIVACY: the public endpoints already strip coordinates.

const API = process.env.LANDING_API_BASE || "http://backend:8000/api";

/** GET an internal API path → parsed JSON, or null on any failure / timeout.
 *  Short revalidate so the landing is lively without hammering the backend. */
async function getJson<T>(path: string, revalidate = 45): Promise<T | null> {
  try {
    const res = await fetch(API + path, {
      next: { revalidate },
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

// ---- Types (mirror the backend public shapes) ----
export type LeaderRow = { rank: number | null; nick: string; score: number; badges: number };
export type Leaderboard = { me: LeaderRow | null; top: LeaderRow[] };

export type Badge = {
  badge_id?: string;
  place_id?: string | null;
  place?: string | null;
  window?: string | null;
  year?: number | null;
  tier: number;
  name: string;
  points?: number | null;
  ordinal?: number | null;
  issued_at?: string | null;
  nick?: string; // present in the recent-badges feed
};

export type Profile = {
  device_key: string;
  nick: string;
  level: { n: number; title: string; species: number };
  score: number;
  rank: number | null;
  badges: Badge[];
};

export type WalkCard = {
  latin_key: string;
  name: string;
  latin: string | null;
  inat_photo: string | null;
  plant_id: string | null;
  found?: boolean;
};
export type PlaceSet = {
  place: { name: string; window: string; set_size: number; target: number };
  items: WalkCard[];
};

// ---- Fetchers ----
export const getLeaderboard = (
  scope: "global" | "place" | "season" = "global",
  opts: { place_id?: string; window?: string; year?: number; limit?: number } = {},
) => {
  const q = new URLSearchParams({ scope, limit: String(opts.limit ?? 20) });
  if (opts.place_id) q.set("place_id", opts.place_id);
  if (opts.window) q.set("window", opts.window);
  if (opts.year) q.set("year", String(opts.year));
  return getJson<Leaderboard>(`/quests/leaderboard?${q}`);
};

export const getRecentBadges = (limit = 12, place_id?: string) =>
  getJson<{ badges: Badge[] }>(
    `/quests/recent-badges?limit=${limit}${place_id ? `&place_id=${place_id}` : ""}`,
  );

export const getProfile = (deviceKey: string) =>
  getJson<Profile>(`/quests/profile/${encodeURIComponent(deviceKey)}`);

export const getPlaceSet = (placeId: string, window: string) =>
  getJson<PlaceSet>(
    `/quests/place/${encodeURIComponent(placeId)}/set?window=${encodeURIComponent(window)}`,
  );

// ---- Display helpers (mirror Quest.kt) ----
const MONTHS_GEN = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

/** "second-half-06" + 2026 → "вторая половина июня 2026". */
export function windowLabelRu(window?: string | null, year?: number | null): string {
  if (!window) return "";
  const p = window.split("-");
  const half = p[0] === "first" ? "первая половина" : "вторая половина";
  const mi = parseInt(p[2] ?? "", 10);
  const month = mi >= 1 && mi <= 12 ? MONTHS_GEN[mi - 1] : "";
  return `${half} ${month}${year ? " " + year : ""}`.replace(/\s+/g, " ").trim();
}

function monthOf(window?: string | null): number | null {
  const m = parseInt((window ?? "").split("-")[2] ?? "", 10);
  return Number.isNaN(m) ? null : m;
}
export function seasonEmoji(window?: string | null): string {
  const m = monthOf(window);
  if (m === null) return "🏅";
  if (m >= 3 && m <= 5) return "🌸";
  if (m >= 6 && m <= 8) return "🌿";
  if (m >= 9 && m <= 11) return "🍂";
  return "❄️";
}
export function seasonAdj(window?: string | null): string {
  const m = monthOf(window);
  if (m === null) return "";
  if (m >= 3 && m <= 5) return "Весенний";
  if (m >= 6 && m <= 8) return "Летний";
  if (m >= 9 && m <= 11) return "Осенний";
  return "Зимний";
}

/** [light bg, metal ring, dark text] per tier — matches the app medallions. */
export function tierColors(tier: number): [string, string, string] {
  if (tier === 1) return ["#F3E0CE", "#CD7F32", "#7A4A1E"]; // бронза
  if (tier === 2) return ["#ECECEC", "#9AA0A6", "#5F6368"]; // серебро
  return ["#FFF3C4", "#D4AF37", "#8A6D1B"]; // золото
}
export const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

export function pluralRu(n: number, one: string, few: string, many: string): string {
  const m100 = n % 100, m10 = n % 10;
  if (m100 >= 11 && m100 <= 14) return many;
  if (m10 === 1) return one;
  if (m10 >= 2 && m10 <= 4) return few;
  return many;
}

// Store links (env-overridable). iOS is TestFlight-only for now → marked "скоро".
export const RUSTORE_URL =
  process.env.NEXT_PUBLIC_RUSTORE_URL || "https://www.rustore.ru/catalog/app/ru.begemot.plantid";
export const APPSTORE_URL = process.env.NEXT_PUBLIC_APPSTORE_URL || "";
