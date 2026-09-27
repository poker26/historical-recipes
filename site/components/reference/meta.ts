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
    openGraph: { title, description, url, type: "website", siteName: "Что растёт", locale: "ru_RU" },
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
