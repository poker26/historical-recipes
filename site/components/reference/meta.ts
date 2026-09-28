// Метаданные страниц рецептов и справочников: заголовок, описание, канонический адрес,
// карточка для соцсетей и запрет индексации для комбинаций фильтров.
import type { Metadata } from "next";
import { SITE_URL } from "../../lib/api";

/** path уже закодирован (кириллица через encodeURIComponent), начинается с «/». */
export function pageMeta({ title, description, path, index = true }: { title: string; description: string; path: string; index?: boolean }): Metadata {
  const url = SITE_URL + path;
  return {
    title,
    description,
    alternates: { canonical: url },
    // Своё поле openGraph у страницы заменяет общее целиком, поэтому картинку по
    // умолчанию (app/opengraph-image.tsx) повторяем здесь явно.
    openGraph: { title, description, url, type: "website", siteName: "Что растёт", locale: "ru_RU", images: [{ url: `${SITE_URL}/opengraph-image`, width: 1200, height: 630 }] },
    ...(index ? {} : { robots: { index: false, follow: true } }),
  };
}

/** Безопасно раскодировать сегмент адреса: Next отдаёт его то закодированным, то нет. */
export function decodeParam(s: string): string {
  try {
    return decodeURIComponent(s);
  } catch {
    return s;
  }
}
