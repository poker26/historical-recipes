// Регион посетителя для сезонной полки и мест. Определять по IP на сервере пока не
// стали: точка по городу читается человеком и переключается ссылкой (`?region=`).
export type Region = { slug: string; name: string; gen: string; lat: number; lng: number };

export const REGIONS: Region[] = [
  { slug: "moskva", name: "Москва", gen: "Москвы", lat: 55.75, lng: 37.62 },
  { slug: "spb", name: "Санкт-Петербург", gen: "Петербурга", lat: 59.93, lng: 30.31 },
  { slug: "kazan", name: "Казань", gen: "Казани", lat: 55.79, lng: 49.11 },
  { slug: "nn", name: "Нижний Новгород", gen: "Нижнего Новгорода", lat: 56.3, lng: 44.0 },
  { slug: "ekb", name: "Екатеринбург", gen: "Екатеринбурга", lat: 56.84, lng: 60.6 },
  { slug: "nsk", name: "Новосибирск", gen: "Новосибирска", lat: 55.03, lng: 82.92 },
  { slug: "krasnodar", name: "Краснодар", gen: "Краснодара", lat: 45.04, lng: 38.98 },
  { slug: "sochi", name: "Сочи", gen: "Сочи", lat: 43.6, lng: 39.73 },
  { slug: "rostov", name: "Ростов-на-Дону", gen: "Ростова", lat: 47.22, lng: 39.72 },
  { slug: "samara", name: "Самара", gen: "Самары", lat: 53.2, lng: 50.15 },
  { slug: "ufa", name: "Уфа", gen: "Уфы", lat: 54.73, lng: 55.95 },
  { slug: "perm", name: "Пермь", gen: "Перми", lat: 58.01, lng: 56.23 },
  { slug: "voronezh", name: "Воронеж", gen: "Воронежа", lat: 51.66, lng: 39.2 },
  { slug: "chelyabinsk", name: "Челябинск", gen: "Челябинска", lat: 55.16, lng: 61.4 },
  { slug: "krasnoyarsk", name: "Красноярск", gen: "Красноярска", lat: 56.01, lng: 92.87 },
  { slug: "irkutsk", name: "Иркутск", gen: "Иркутска", lat: 52.29, lng: 104.28 },
  { slug: "vladivostok", name: "Владивосток", gen: "Владивостока", lat: 43.12, lng: 131.89 },
  { slug: "kaliningrad", name: "Калининград", gen: "Калининграда", lat: 54.71, lng: 20.51 },
  { slug: "tyumen", name: "Тюмень", gen: "Тюмени", lat: 57.15, lng: 65.53 },
  { slug: "minsk", name: "Минск", gen: "Минска", lat: 53.9, lng: 27.56 },
];

export function regionBySlug(slug?: string | null): Region {
  return REGIONS.find((r) => r.slug === slug) ?? REGIONS[0];
}

/** Текущее полумесячное окно сезона, как его считают приложение и сервер. */
export function currentWindow(d = new Date()): { window: string; year: number; month: number } {
  const half = d.getDate() <= 15 ? "first" : "second";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return { window: `${half}-half-${mm}`, year: d.getFullYear(), month: d.getMonth() + 1 };
}
