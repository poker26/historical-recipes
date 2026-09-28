// Плитка книги на полке библиотеки: обложка, автор, название, год, чипы и цифры.
import Link from "next/link";
import { fmtInt, pluralRu } from "../../lib/api";
import { ACCESS_CHIP, domainLabel, type BookItem } from "../../lib/api-library";
import { BookCover } from "./BookCover";

export function BookTile({ b }: { b: BookItem }) {
  const access = ACCESS_CHIP[b.access];
  const domain = domainLabel(b.domain);
  // Нули не пишем: «рецептов 0» читается как ошибка. Одна «страница» у книг из
  // текстового файла означает весь текст целиком, это тоже не число страниц.
  const meta = [
    b.pages > 1 ? `${fmtInt(b.pages)} ${pluralRu(b.pages, "страница", "страницы", "страниц")}` : null,
    b.plants ? `${fmtInt(b.plants)} ${pluralRu(b.plants, "растение", "растения", "растений")}` : null,
    b.recipes ? `${fmtInt(b.recipes)} ${pluralRu(b.recipes, "рецепт", "рецепта", "рецептов")}` : null,
  ].filter(Boolean).join(", ");
  return (
    <Link href={`/library/${b.id}`} className="lib-book">
      <BookCover id={b.id} title={b.title} author={b.author} year={b.year} hasCover={b.has_cover} />
      <div className="lib-book-body">
        {b.author ? <div className="lib-book-author">{b.author}</div> : null}
        <div className="lib-book-title">{b.title}</div>
        <div className="lib-book-year">{b.year ?? "год не указан"}</div>
        <div className="lib-book-chips">
          <span className={`chip chip-${access.kind}`}>{access.label}</span>
          {domain ? <span className="chip lib-chip-plain">{domain}</span> : null}
          {b.language === "pre_reform_ru" ? <span className="chip lib-chip-plain">дореформенная орфография</span> : null}
        </div>
        {meta ? <div className="lib-book-meta">{meta}</div> : null}
      </div>
    </Link>
  );
}
