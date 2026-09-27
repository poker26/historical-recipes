// /plant/{uuid} — адрес шэров из приложения (App Links и Universal Links открывают по нему
// приложение). На сайте карточка живёт на /atlas/{слаг}, сюда приходят браузеры без
// приложения: отвечаем 308 на канонический адрес. Метаданные остаются полными, чтобы
// превью ссылки в мессенджере выглядело правильно даже у роботов, которые не идут
// по перенаправлению. Картинку шэра отдаёт соседний card.png, его не трогаем.
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { SITE_URL, excerpt, plantHref } from "../../../lib/api";
import {
  displayName, getFieldView, getPlantCard, largePhoto, queryFrom, quoteOf, resolvePlantParam,
} from "../../../lib/api-plant";

type Props = { params: { id: string }; searchParams: Record<string, string | string[] | undefined> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const r = await resolvePlantParam(params.id);
  if (r.state !== "ok") return { title: "Растение", robots: { index: false, follow: true } };
  const [res, field] = await Promise.all([getPlantCard(r.id), getFieldView(r.id)]);
  const card = res.state === "ok" ? res.card : null;
  const rawName = card?.name || field?.name;
  if (!rawName) return { title: "Растение", robots: { index: false, follow: true } };
  const latin = card ? card.latin : field?.name_latin ?? null;
  const name = displayName(rawName);
  const title = latin ? `${name} (${latin})` : name;
  const lead = field?.verdict?.trim() || quoteOf(field?.lead_fact)?.text || (card?.kind === "species" ? card.firstQuote : null);
  const description = excerpt(lead || `${name}: применение, состав, сбор и рецепты по книгам в атласе «Что растёт».`, 160);
  const photoUrl = (card?.kind === "species" ? card.photo?.url : null) || field?.photo_url || null;
  const image = photoUrl ? largePhoto(photoUrl) : `${SITE_URL}/plant/${r.id}/card.png`;
  const canonical = SITE_URL + plantHref(r.id, latin);
  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title, description, url: canonical, type: "article", siteName: "Что растёт", locale: "ru_RU", images: [{ url: image }] },
    twitter: { card: "summary_large_image", title, description },
    robots: { index: false, follow: true },
  };
}

export default async function PlantShareRedirect({ params, searchParams }: Props) {
  const r = await resolvePlantParam(params.id);
  if (r.state === "missing") notFound();
  const query = queryFrom(searchParams);
  // Справочник не ответил: отправляем на тот же адрес в атласе, там своё честное пустое состояние.
  if (r.state === "error") permanentRedirect(`/atlas/${encodeURIComponent(params.id)}${query}`);
  const res = await getPlantCard(r.id);
  if (res.state === "missing") notFound();
  permanentRedirect((res.state === "ok" ? plantHref(res.card.id, res.card.latin) : `/atlas/${r.id}`) + query);
}
