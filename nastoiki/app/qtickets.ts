// Server-only: pull upcoming master-class dates from the Qtickets REST API.
// The token lives in the container's server env (QTICKETS_API_TOKEN) and never
// reaches the browser. Cached via ISR (revalidate). Degrades to null on any
// failure so the page falls back to the plain «Записаться» button.

const TOKEN = process.env.QTICKETS_API_TOKEN;
const EVENT_ID = process.env.QTICKETS_EVENT_ID || "150827";
const EVENT_SLUG = "master-klass-po-sozdaniyu-nastoek-po-starinnym-retseptam";

export type MkDate = { iso: string; day: string; weekday: string };
export type MkInfo = {
  buyUrl: string;
  dates: MkDate[];
  name: string;       // event title (for Schema.org Event)
  place: string;      // human place / address
};

const dayFmt = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", timeZone: "Europe/Moscow" });
const wdFmt = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "Europe/Moscow" });

export async function getMasterclassDates(): Promise<MkInfo | null> {
  if (!TOKEN) return null;
  try {
    const res = await fetch(`https://qtickets.ru/api/rest/v1/events/${EVENT_ID}`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      next: { revalidate: 1800 },
    });
    if (!res.ok) return null;
    const json = await res.json();
    let ev = json?.data ?? json;
    if (Array.isArray(ev)) ev = ev[0];
    const shows: any[] = ev?.shows ?? [];
    const now = Date.now();

    const dates: MkDate[] = shows
      .filter((s) => s?.is_active && s?.start_date && s?.sale_finish_date)
      // keep sessions still to come (from the start of today) whose sales are still open
      .filter((s) =>
        new Date(s.start_date).getTime() >= now - 12 * 3600_000 &&
        new Date(s.sale_finish_date).getTime() >= now)
      .sort((a, b) => String(a.start_date).localeCompare(String(b.start_date)))
      .slice(0, 8)
      .map((s) => {
        const d = new Date(s.start_date);
        return { iso: s.start_date, day: dayFmt.format(d), weekday: wdFmt.format(d).replace(".", "") };
      });

    if (!dates.length) return null;
    const slug = ev?.slug || EVENT_SLUG;
    // prefer the branded organizer domain (verified 200) over the generic url field
    const buyUrl = `https://pronino.qtickets.ru/${EVENT_ID}-${slug}`;
    const name = ev?.name || "Мастер-класс по созданию настоек по старинным рецептам";
    const place = [ev?.place_name, ev?.place_address].filter(Boolean).join(", ") ||
      "деревня Пронино, городской округ Серпухов, Московская область";
    return { buyUrl, dates, name, place };
  } catch {
    return null;
  }
}
