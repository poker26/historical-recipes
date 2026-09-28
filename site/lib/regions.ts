// Город посетителя для сезонной полки и мест. По IP на сервере его пока не определяем:
// человек выбирает город ссылкой (`?region=`). Для каждого города записаны падежи,
// потому что в тексте он встаёт в разные конструкции: «под Москвой», «в Москве».
export type Region = {
  slug: string;
  /** Именительный: «Москва». */
  name: string;
  /** Творительный для «рядом с…» и «под…»: «Москвой». */
  ins: string;
  /** Предложный вместе с предлогом: «в Москве», «во Владивостоке». */
  inPrep: string;
  lat: number;
  lng: number;
};

export const REGIONS: Region[] = [
  { slug: "moskva", name: "Москва", ins: "Москвой", inPrep: "в Москве", lat: 55.75, lng: 37.62 },
  { slug: "spb", name: "Санкт-Петербург", ins: "Санкт-Петербургом", inPrep: "в Санкт-Петербурге", lat: 59.93, lng: 30.31 },
  { slug: "kazan", name: "Казань", ins: "Казанью", inPrep: "в Казани", lat: 55.79, lng: 49.11 },
  { slug: "nn", name: "Нижний Новгород", ins: "Нижним Новгородом", inPrep: "в Нижнем Новгороде", lat: 56.3, lng: 44.0 },
  { slug: "ekb", name: "Екатеринбург", ins: "Екатеринбургом", inPrep: "в Екатеринбурге", lat: 56.84, lng: 60.6 },
  { slug: "nsk", name: "Новосибирск", ins: "Новосибирском", inPrep: "в Новосибирске", lat: 55.03, lng: 82.92 },
  { slug: "krasnodar", name: "Краснодар", ins: "Краснодаром", inPrep: "в Краснодаре", lat: 45.04, lng: 38.98 },
  { slug: "sochi", name: "Сочи", ins: "Сочи", inPrep: "в Сочи", lat: 43.6, lng: 39.73 },
  { slug: "rostov", name: "Ростов-на-Дону", ins: "Ростовом-на-Дону", inPrep: "в Ростове-на-Дону", lat: 47.22, lng: 39.72 },
  { slug: "samara", name: "Самара", ins: "Самарой", inPrep: "в Самаре", lat: 53.2, lng: 50.15 },
  { slug: "ufa", name: "Уфа", ins: "Уфой", inPrep: "в Уфе", lat: 54.73, lng: 55.95 },
  { slug: "perm", name: "Пермь", ins: "Пермью", inPrep: "в Перми", lat: 58.01, lng: 56.23 },
  { slug: "voronezh", name: "Воронеж", ins: "Воронежем", inPrep: "в Воронеже", lat: 51.66, lng: 39.2 },
  { slug: "chelyabinsk", name: "Челябинск", ins: "Челябинском", inPrep: "в Челябинске", lat: 55.16, lng: 61.4 },
  { slug: "krasnoyarsk", name: "Красноярск", ins: "Красноярском", inPrep: "в Красноярске", lat: 56.01, lng: 92.87 },
  { slug: "irkutsk", name: "Иркутск", ins: "Иркутском", inPrep: "в Иркутске", lat: 52.29, lng: 104.28 },
  { slug: "vladivostok", name: "Владивосток", ins: "Владивостоком", inPrep: "во Владивостоке", lat: 43.12, lng: 131.89 },
  { slug: "kaliningrad", name: "Калининград", ins: "Калининградом", inPrep: "в Калининграде", lat: 54.71, lng: 20.51 },
  { slug: "tyumen", name: "Тюмень", ins: "Тюменью", inPrep: "в Тюмени", lat: 57.15, lng: 65.53 },
  { slug: "minsk", name: "Минск", ins: "Минском", inPrep: "в Минске", lat: 53.9, lng: 27.56 },
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
