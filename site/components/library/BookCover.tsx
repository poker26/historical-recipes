// Обложки книг библиотеки. Серверные компоненты без клиентского JS.
import { LeafGlyph } from "../common";

/** Типографская обложка для книги без скана: кремовый титульный лист в рамке,
 *  название набрано Literata. Декоративная: название книги всегда есть рядом текстом. */
export function TypoCover({ title, author, year, large }: { title: string; author?: string | null; year?: number | null; large?: boolean }) {
  return (
    <div className={"lib-typo" + (large ? " lib-typo-lg" : "")} aria-hidden="true">
      {author ? <div className="lib-typo-author">{author}</div> : null}
      <div className="lib-typo-title">{title}</div>
      <div className="lib-typo-orn"><LeafGlyph /></div>
      <div className="lib-typo-year">{year ?? "год не указан"}</div>
    </div>
  );
}

/** Обложка 3:4. Скан ложится поверх типографской обложки: пока он грузится или если
 *  не загрузится вовсе, читатель видит название книги, а не значок битой картинки. */
export function BookCover({
  id, title, author, year, hasCover, size = "thumb", large,
}: {
  id: string; title: string; author?: string | null; year?: number | null; hasCover: boolean;
  size?: "thumb" | "medium"; large?: boolean;
}) {
  return (
    <div className={"lib-cover" + (large ? " lib-cover-lg" : "")}>
      <TypoCover title={title} author={author} year={year} large={large} />
      {hasCover ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={`/library/${id}/cover.jpg?size=${size}`} alt="" loading={large ? undefined : "lazy"} decoding="async" />
      ) : null}
    </div>
  );
}
