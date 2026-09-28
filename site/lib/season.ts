// Страницы «что собирать в сентябре»: адреса месяцев и данные из сроков сбора в книгах.
import { getJson } from "./api";

export const MONTH_SLUGS = [
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
];

/** «september» → 9; неизвестное слово → null. */
export function monthOfSlug(slug: string): number | null {
  const i = MONTH_SLUGS.indexOf(slug.toLowerCase());
  return i >= 0 ? i + 1 : null;
}

export const seasonHref = (month: number) => `/season/${MONTH_SLUGS[month - 1]}`;

export type HarvestItem = {
  plant_id: string;
  name: string;
  latin: string | null;
  photo: string | null;
  photo_attribution: string | null;
  photo_license: string | null;
  safety_level: number | null;
  recipes: number;
  part: string | null;
  season: string | null;
  method: string | null;
  book_id: string | null;
  book: string | null;
  year: number | null;
  page: number | null;
};

/** Что заготавливают в месяце: виды с записью о сборе, срок которой приходится на месяц.
 *  Корни бэкенд не берёт: среди записей о корнях у хорошо описанных трав встречаются чужие. */
export const getHarvest = (month: number) =>
  getJson<{ items: HarvestItem[]; month: number }>(`/showcase/harvest?month=${month}&limit=48`, 21600, 30000);
