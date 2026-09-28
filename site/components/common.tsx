// Общие серверные компоненты сайта botanik.fun: без клиентского JS.
// Разделы (атлас, рецепты, библиотека, справочники) собираются из этих кирпичей,
// чтобы плитка вида, строка источника и плашка безопасности выглядели одинаково везде.
import Link from "next/link";
import type { ReactNode } from "react";
import { creditShort } from "../lib/api-atlas";
import { realAuthor } from "../lib/api";

/** Лист-заглушка для карточек без фотографии. */
export function LeafGlyph() {
  return (
    <svg viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M52 10C30 10 14 24 12 50c26 2 40-14 40-40Z" fill="currentColor" opacity=".55" />
      <path d="M14 50C24 38 34 28 50 12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

export function Crumbs({ items }: { items: { href?: string; label: string }[] }) {
  return (
    <nav className="crumbs" aria-label="Путь">
      {items.map((it, i) => (
        <span key={i}>
          {i > 0 ? <span style={{ margin: "0 4px" }}>·</span> : null}
          {it.href ? <Link href={it.href}>{it.label}</Link> : <span>{it.label}</span>}
        </span>
      ))}
    </nav>
  );
}

export function SectionHead({ title, href, more, children }: { title: string; href?: string; more?: string; children?: ReactNode }) {
  return (
    <div className="section-head">
      <h2>{title}</h2>
      {children}
      {href ? <Link href={href} className="more">{more ?? "все"} →</Link> : null}
    </div>
  );
}

export type TileProps = {
  href: string;
  name: string;
  latin?: string | null;
  photo?: string | null;
  /** Гравюра вместо фото: рисуем на кремовом фоне без обрезки. */
  plate?: boolean;
  meta?: string | null;
  tags?: { label: string; warn?: boolean }[];
  /** Короткая подпись фото («© автор · лицензия»); полный текст прав уходит в title. */
  credit?: string | null;
  /** Всплывающая подсказка у всей плитки, например полное имя с синонимами. */
  title?: string | null;
};

/** Плитка вида, рецепта или книги: фото или гравюра, имя, латынь, подпись, чипы. */
export function Tile({ href, name, latin, photo, plate, meta, tags, credit, title }: TileProps) {
  return (
    <Link href={href} className="tile" title={title ?? undefined}>
      <div className={"tile-photo" + (plate ? " plate" : "")}>
        {photo ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={photo} alt={name} loading="lazy" />
        ) : (
          <LeafGlyph />
        )}
      </div>
      <div className="tile-name">{name}</div>
      {latin ? <div className="tile-latin">{latin}</div> : null}
      {meta ? <div className="tile-meta">{meta}</div> : null}
      {tags?.length ? (
        <div className="tile-tags">
          {tags.map((t, i) => (
            <span key={i} className={"tag" + (t.warn ? " tag-warn" : "")}>{t.label}</span>
          ))}
        </div>
      ) : null}
      {credit ? <div className="tile-credit" title={credit}>{credit}</div> : null}
    </Link>
  );
}

export type SourceRefProps = {
  book?: string | null;
  bookId?: string | null;
  year?: number | null;
  author?: string | null;
  page?: number | null;
  /** id факта: страница источника подсветит именно эту цитату (?hl=…#hl). */
  hl?: string | null;
};

/** Строка источника: «Смельской. Царство врачебных трав, 1870 · стр. 41» со ссылкой на страницу. */
export function SourceRef({ book, bookId, year, author, page, hl }: SourceRefProps) {
  if (!book && !bookId) return null;
  // «Соловьёв П.В.» + «. » давало двойную точку: хвостовую точку автора снимаем.
  const who = realAuthor(author) ? author!.replace(/[.\s]+$/, "") : null;
  // Год бывает уже в названии: «Атлас лекарственных растений СССР (1962)».
  const label = [who, book].filter(Boolean).join(". ") + (year && !(book ?? "").includes(String(year)) ? `, ${year}` : "");
  const bookHref = bookId ? `/library/${bookId}` : null;
  const pageHref = bookId && page ? `/library/${bookId}/p/${page}${hl ? `?hl=${encodeURIComponent(hl)}#hl` : ""}` : null;
  return (
    <span className="source">
      {bookHref ? <Link href={bookHref}>{label || "книга"}</Link> : <span>{label}</span>}
      {page ? (
        <>
          {", "}
          {pageHref ? <Link href={pageHref}>стр. {page}</Link> : <span>стр. {page}</span>}
        </>
      ) : null}
    </span>
  );
}

/** Дословная цитата из книги с источником под ней. */
export function Quote({ text, source }: { text: string; source?: SourceRefProps }) {
  return (
    <blockquote className="quote">
      <div className="quote-text">{text}</div>
      {source ? <SourceRef {...source} /> : null}
    </blockquote>
  );
}

export type SafetyLevel = "danger" | "warn" | "ok" | "unknown";

/** Плашка безопасности. Молчание источников показываем как молчание, не как «безопасно». */
export function Safety({ level, title, text }: { level: SafetyLevel; title: string; text?: string | null }) {
  const icon = level === "danger" ? "⚠" : level === "warn" ? "!" : level === "ok" ? "✓" : "?";
  return (
    <div className={`safety safety-${level}`} role="note">
      <span aria-hidden="true" style={{ fontWeight: 700, fontSize: 18, lineHeight: 1.2 }}>{icon}</span>
      <div>
        <b>{title}</b>
        {text ? <span>{text}</span> : null}
      </div>
    </div>
  );
}

export function Chips({ items }: { items: { href?: string; label: string; kind?: "leaf" | "lime" | "danger" | "mist" }[] }) {
  if (!items.length) return null;
  return (
    <div className="chips">
      {items.map((c, i) => {
        const cls = "chip" + (c.kind ? ` chip-${c.kind}` : "");
        return c.href ? <Link key={i} href={c.href} className={cls}>{c.label}</Link> : <span key={i} className={cls}>{c.label}</span>;
      })}
    </div>
  );
}

/** Пейджер на query-параметре `page`; остальные параметры сохраняются. */
export function Pager({ page, pages, base, params }: { page: number; pages: number; base: string; params?: Record<string, string | undefined> }) {
  if (pages <= 1) return null;
  const href = (p: number) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params ?? {})) if (v) q.set(k, v);
    if (p > 1) q.set("page", String(p));
    const s = q.toString();
    return s ? `${base}?${s}` : base;
  };
  const around = [page - 2, page - 1, page, page + 1, page + 2].filter((p) => p >= 1 && p <= pages);
  return (
    <nav className="pager" aria-label="Страницы">
      {page > 1 ? <Link href={href(page - 1)}>← назад</Link> : <span className="dis">← назад</span>}
      {around[0] > 1 ? <Link href={href(1)}>1</Link> : null}
      {around[0] > 2 ? <span className="dis">…</span> : null}
      {around.map((p) => (p === page ? <span key={p} className="cur">{p}</span> : <Link key={p} href={href(p)}>{p}</Link>))}
      {around[around.length - 1] < pages - 1 ? <span className="dis">…</span> : null}
      {around[around.length - 1] < pages ? <Link href={href(pages)}>{pages}</Link> : null}
      {page < pages ? <Link href={href(page + 1)}>дальше →</Link> : <span className="dis">дальше →</span>}
    </nav>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Подпись автора и лицензии фотографии. Фото iNaturalist без подписи показывать нельзя. */
export function PhotoCredit({ attribution, license, source }: { attribution?: string | null; license?: string | null; source?: string | null }) {
  if (!attribution && !license) return null;
  // У iNaturalist лицензия уже стоит в строке автора: «(c) Имя, some rights reserved (CC BY-NC)».
  const licInAuthor = !!attribution && /\(\s*CC[^)]*\)|\bCC0\b|public domain/i.test(attribution);
  const lic = license && !licInAuthor ? license.toUpperCase().replace(/^CC-/, "CC ") : null;
  const src = source?.startsWith("inaturalist") ? "iNaturalist" : source?.startsWith("wikimedia") ? "Викимедиа" : null;
  // Сырые строки прав бывают английскими («(c) Имя, some rights reserved (CC BY)»):
  // показываем короткую форму, полную оставляем во всплывающей подсказке.
  const who = creditShort(attribution) ?? "автор не указан";
  return (
    <div className="credit" title={attribution ?? undefined}>
      Фото {who}{lic && !who.toUpperCase().includes(lic) ? `, ${lic}` : ""}{src && !who.includes(src) ? `, ${src}` : ""}.
    </div>
  );
}

export const MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
export const MONTHS_GEN_RU = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
/** Предложный падеж для «в сентябре». */
export const MONTHS_PREP_RU = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];
