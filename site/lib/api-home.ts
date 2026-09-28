// Данные главной, мест и прогулок: витрина сезона, кухня, эфир, книга недели, цифры корпуса.
import { getJson, qs } from "./api";
import { creditShort } from "./api-atlas";
import type { Region } from "./regions";

export type ShowcaseItem = {
  plant_id: string; name: string; latin?: string | null; photo?: string | null; hook?: string | null;
  safety_level?: number | null; recipes?: number | null; biotope_match?: boolean | null;
  /** Фото самой карточки с автором и лицензией; `photo` — живое фото таксона из iNat без подписи. */
  plant_photo?: string | null; plant_photo_attribution?: string | null; plant_photo_license?: string | null;
  photo_attribution?: string | null; photo_license?: string | null;
};
export type Showcase = { items: ShowcaseItem[]; biotopes?: string[]; month?: number; mode?: "growing" | "harvest"; title?: string };

export const getSeasonal = (r: Region, limit = 8) =>
  getJson<Showcase>(`/showcase/seasonal${qs({ lat: r.lat, lng: r.lng, limit })}`, 3600, 15000);

export type KitchenItem = {
  id: string; name: string; category?: string | null; kind?: string | null; n_ingredients?: number | null;
  book?: string | null; year?: number | null; step_by_step?: boolean; text?: string | null; truncated?: boolean;
  plant_id?: string | null; plant?: string | null; photo?: string | null; safety_level?: number | null;
  photo_attribution?: string | null; photo_license?: string | null;
};

/** «© автор · CC BY» для подписи снимка; null, если автора нет. */
export function creditLine(attribution?: string | null, license?: string | null): string | null {
  const short = creditShort(attribution);
  if (!short) return null;
  const lic = license ? license.toUpperCase().replace(/^CC-/, "CC ") : "";
  return lic && !short.toUpperCase().includes(lic) ? `${short}, ${lic}` : short;
}
export type Kitchen = { items: KitchenItem[]; title?: string; disclaimer?: string };
export const getKitchen = (limit = 6) => getJson<Kitchen>(`/showcase/kitchen?limit=${limit}`, 3600);

export type LibraryStats = {
  books: number; with_year: number; open_books: number; year_min: number | null; year_max: number | null;
  pages: number; scan_pages: number; books_with_scans: number; domains: { domain: string; count: number }[];
};
export const getLibraryStats = () => getJson<LibraryStats>(`/library/stats`, 3600);

export type BookLite = {
  id: string; title: string; author?: string | null; year?: number | null; domain?: string | null; language?: string | null;
  pages: number; scan_pages: number; plants: number; recipes: number; home_recipes?: number; uses: number;
  access: "open" | "cited" | "closed"; has_cover: boolean;
};
export const getOpenBooks = () =>
  getJson<{ total: number; items: BookLite[] }>(`/library/books?access=open&scans=true&sort=plants&limit=30`, 21600);

/** Сколько карточек прошло гейт атласа и сколько домашних пошаговых рецептов. */
export async function getCorpusCounts(): Promise<{ plants: number | null; recipes: number | null }> {
  const [p, r] = await Promise.all([
    getJson<{ total: number }>(`/plants/sitemap?limit=1`, 3600),
    getJson<{ total: number }>(`/recipes/sitemap?limit=1`, 3600),
  ]);
  return { plants: p?.total ?? null, recipes: r?.total ?? null };
}

// ---- места и прогулки (квесты) ----
export type PlaceNear = {
  id: string; name: string; kind?: string | null; lat?: number; lng?: number; distance_km?: number | null;
  window?: string | null; set_size?: number | null; target?: number | null; matched?: number | null; badge_issued?: boolean | null;
};
export const getPlacesNear = (r: Region, window: string, radiusKm = 40, limit = 40) =>
  getJson<{ places: PlaceNear[] }>(`/quests/places/near${qs({ lat: r.lat, lng: r.lng, radius_km: radiusKm, limit, window })}`, 1800);

export type PlaceSetItem = {
  latin_key: string; name: string; latin?: string | null; species_key?: string | null; inat_photo?: string | null;
  plant_id?: string | null; found?: boolean; confidence?: number | null;
};
export type PlaceSet = {
  place?: { id: string; name: string; window: string; set_size: number; target: number; matched?: number; badge_issued?: boolean;
    out_of_season?: number | boolean; dormant?: boolean; returns_month?: number | null };
  group?: string; safety_notice?: string | null; biotope?: string | null; items?: PlaceSetItem[]; error?: string;
};
export const getPlaceSet = (placeId: string, window: string, group: "plants" | "fungi" = "plants") =>
  getJson<PlaceSet>(`/quests/place/${encodeURIComponent(placeId)}/set${qs({ window, group })}`, 1800);

export const getPlaceParticipants = (placeId: string, window: string, year: number) =>
  getJson<{ count: number; participants: { handle?: string; nick: string; avatar?: string | null; tier?: number }[] }>(
    `/quests/place/${encodeURIComponent(placeId)}/participants${qs({ window, year })}`, 900);

export const getPlaceBiotopes = (placeId: string, window: string) =>
  getJson<{ biotopes: { key: string; group?: string; species_count?: number }[] }>(
    `/quests/place/${encodeURIComponent(placeId)}/biotopes${qs({ window })}`, 3600);

export type Walk = {
  id: string; kind?: string | null; status: string; place?: { id: string; name: string } | null;
  planned_at?: string | null; started_at?: string | null; ended_at?: string | null;
  participants: { handle?: string; nick: string; avatar?: string | null; role?: string | null }[]; participant_count?: number;
};
export const getWalk = (id: string) => getJson<Walk>(`/walks/${encodeURIComponent(id)}`, 60);
