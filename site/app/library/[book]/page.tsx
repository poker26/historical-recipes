import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, SectionHead, Tile } from "../../../components/common";
import { BookCover } from "../../../components/library/BookCover";
import { markQuery } from "../../../components/library/highlight";
import { HighlightedText } from "../../../components/library/HighlightedText";
import { DEFAULT_OG, SITE_URL, excerpt, fmtInt, isUuid, plantHref, pluralRu, realAuthor, titleYear } from "../../../lib/api";
import {
  ACCESS_CHIP, accessNote, domainLabel, firstReadablePage, getBook, param, recipeMeta, recipesTotal, searchBook,
  type BookDetail,
} from "../../../lib/api-library";
import "../library.css";

type Props = { params: { book: string }; searchParams: { [key: string]: string | string[] | undefined } };

const ACCESS_TITLE = { open: "Можно читать целиком", cited: "Фрагменты и ссылки", closed: "Только карточка" } as const;
const TOC_VISIBLE = 16;
const PLANTS_VISIBLE = 12;

function plantTile(p: BookDetail["top_plants"][number]) {
  return (
    <Tile
      key={p.id}
      href={plantHref(p.id, p.name_latin)}
      name={p.name_modern || p.name}
      latin={p.name_latin}
      photo={p.photo_url}
      meta={[
        `${fmtInt(p.mentions)} ${pluralRu(p.mentions, "упоминание", "упоминания", "упоминаний")}`,
        p.uses ? `${fmtInt(p.uses)} ${pluralRu(p.uses, "применение", "применения", "применений")}` : null,
      ].filter(Boolean).join(", ")}
      tags={p.kingdom === "гриб" ? [{ label: "гриб" }] : undefined}
    />
  );
}

/** Цифры книги одной фразой: для description и подзаголовков. */
function extractedLine(b: BookDetail): string {
  const recipes = recipesTotal(b);
  const parts = [
    b.plants ? `${fmtInt(b.plants)} ${pluralRu(b.plants, "растение", "растения", "растений")}` : null,
    b.uses ? `${fmtInt(b.uses)} ${pluralRu(b.uses, "цитата", "цитаты", "цитат")} о применении` : null,
    recipes ? `${fmtInt(recipes)} ${pluralRu(recipes, "рецепт", "рецепта", "рецептов")}` : null,
  ].filter(Boolean) as string[];
  if (!parts.length) return "";
  return parts.length > 1 ? parts.slice(0, -1).join(", ") + " и " + parts[parts.length - 1] : parts[0];
}

/** Заголовки оглавления, которые стоит показать читателю. Разметка разделов местами
 *  подписана машиной по-английски («Title page and front matter») или состоит из одних
 *  номеров страниц: это не заголовки книги, их пропускаем. */
function tocTitles(b: BookDetail): string[] {
  return (b.toc ?? []).map((t) => t.title).filter((t) => /[а-яё]/i.test(t));
}

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  if (!isUuid(params.book)) return { title: "Книга не найдена", robots: { index: false } };
  const r = await getBook(params.book);
  if (!r.data) return { title: "Книга", robots: { index: false } };
  const b = r.data;
  const url = `${SITE_URL}/library/${b.id}`;
  const author = realAuthor(b.author)?.replace(/[.\s]+$/, "") ?? null;
  // Открытую книгу ищут словами «читать онлайн»: они стоят в заголовке.
  const base = titleYear(b.title, b.year);
  const title = (author ? `${author}. ${base}` : base) + (b.access === "open" ? ", читать онлайн" : "");
  const who = [realAuthor(b.author), titleYear(`«${b.title}»`, b.year)].filter(Boolean).join(", ");
  const line = extractedLine(b);
  const description =
    `${who}. ` +
    (b.access === "open" ? "Книгу можно читать онлайн целиком по сканам страниц. " : "") +
    (line ? `Из книги в атлас вошли ${line}.` : b.access === "open" ? "" : "Открыты номера страниц и цитаты, которые вошли в атлас.");
  // Закрытая книга показывает только описание: в индексе ей делать нечего.
  const noindex = !!param(searchParams.q) || b.access === "closed";
  return {
    title,
    description: description.trim(),
    alternates: { canonical: url },
    robots: noindex ? { index: false, follow: true } : undefined,
    openGraph: {
      title, description: description.trim(), url, type: "book",
      ...(b.year ? { releaseDate: String(b.year) } : {}),
      ...(author ? { authors: [author] } : {}),
      images: b.has_cover && b.access !== "closed" ? [{ url: `${url}/cover.jpg?size=medium` }] : [DEFAULT_OG],
    },
  };
}

export default async function BookPage({ params, searchParams }: Props) {
  if (!isUuid(params.book)) notFound();
  const r = await getBook(params.book);
  if (r.status === 404 || r.status === 422) notFound();
  const b = r.data;

  if (!b) {
    return (
      <>
        <Header active="/library" />
        <Crumbs items={[{ href: "/library", label: "Библиотека" }, { label: "Книга" }]} />
        <Empty>
          Книга сейчас не загрузилась, библиотека не отвечает. Обнови страницу через минуту или вернись
          на <Link href="/library">полку</Link>.
        </Empty>
        <Footer />
      </>
    );
  }

  const q = param(searchParams.q)?.slice(0, 80);
  const search = q && q.length >= 3 ? await searchBook(b.id, q) : null;
  const open = b.access === "open";
  const closed = b.access === "closed";
  const first = firstReadablePage(b);
  const url = `${SITE_URL}/library/${b.id}`;
  const domain = domainLabel(b.domain);
  const toc = tocTitles(b);
  const hasPdfPages = open && (b.source_format === "pdf" || b.source_format === "djvu");
  const recipes = recipesTotal(b);

  const stats = [
    { n: b.plants, cap: pluralRu(b.plants, "растение", "растения", "растений") },
    { n: b.uses, cap: pluralRu(b.uses, "цитата", "цитаты", "цитат") + " о применении" },
    { n: recipes, cap: pluralRu(recipes, "рецепт", "рецепта", "рецептов") },
    { n: b.home_recipes, cap: pluralRu(b.home_recipes, "домашний рецепт", "домашних рецепта", "домашних рецептов") },
    { n: b.anchored_facts, cap: pluralRu(b.anchored_facts, "цитата", "цитаты", "цитат") + " с номером страницы" },
  ].filter((x) => x.n > 0);

  const pagesLine = b.pages > 1
    ? hasPdfPages
      ? `${fmtInt(b.pages)} ${pluralRu(b.pages, "страница", "страницы", "страниц")}, каждую можно открыть сканом.`
      : `${fmtInt(b.pages)} ${pluralRu(b.pages, "страница", "страницы", "страниц")}, из них ${b.scan_pages ? fmtInt(b.scan_pages) + " со сканом" : "сканов нет"}.`
    : b.pages === 1 ? "Книга хранится одним текстом, без сканов страниц." : null;

  const ld = {
    "@context": "https://schema.org",
    "@type": "Book",
    name: b.title,
    ...(realAuthor(b.author) ? { author: { "@type": "Person", name: b.author } } : {}),
    ...(b.year ? { datePublished: String(b.year) } : {}),
    inLanguage: "ru",
    url,
    ...(b.access === "open" ? { isAccessibleForFree: true } : {}),
    ...(b.pages > 1 ? { numberOfPages: b.pages } : {}),
    ...(b.has_cover && !closed ? { image: `${url}/cover.jpg?size=medium` } : {}),
  };

  return (
    <>
      <Header active="/library" />
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld).replace(/</g, "\\u003c") }} />
      <main>
        <Crumbs items={[{ href: "/library", label: "Библиотека" }, { label: excerpt(b.title, 70) }]} />

        <section className="lib-head">
          <div className="lib-head-cover">
            <BookCover id={b.id} title={b.title} author={b.author} year={b.year} hasCover={b.has_cover && !closed} size="medium" large />
          </div>
          <div className="lib-head-info">
            {b.author ? <div className="lib-head-author">{b.author}</div> : null}
            <h1>{b.title}</h1>
            <div className="lib-head-year">{b.year ? `${b.year} год` : "Год издания не указан"}</div>
            <div className="chips">
              <span className={`chip chip-${ACCESS_CHIP[b.access].kind}`}>{ACCESS_CHIP[b.access].label}</span>
              {domain ? <Link href={`/library?domain=${encodeURIComponent(b.domain ?? "")}`} className="chip lib-chip-plain">{domain}</Link> : null}
              {/* «Современную» не утверждаем: у части старых книг поле language пока modern_ru. */}
              {b.language === "pre_reform_ru" ? <span className="chip lib-chip-plain">дореформенная орфография</span> : null}
            </div>
            {pagesLine ? <p className="lib-head-pages">{pagesLine}</p> : null}

            <div className={`lib-access lib-access-${b.access}`}>
              <b>{ACCESS_TITLE[b.access]}</b>
              <span>{accessNote(b.access, b.year)}</span>
            </div>

            {open ? (
              <div className="lib-actions">
                <Link href={`/library/${b.id}/p/${first}`} className="btn btn-primary">Читать с первой страницы</Link>
                {b.busiest_pages[0] && b.busiest_pages[0].page !== first ? (
                  <Link href={`/library/${b.id}/p/${b.busiest_pages[0].page}`} className="btn btn-ghost">
                    Открыть страницу, где больше всего цитат
                  </Link>
                ) : null}
              </div>
            ) : null}

            {!closed ? (
              <form className="lib-search lib-search-book" action={`/library/${b.id}`} method="get" role="search">
                <input type="search" name="q" defaultValue={q ?? ""} minLength={3} maxLength={80} placeholder="Найти слово на страницах книги" aria-label="Поиск по тексту книги" />
                <button type="submit" className="btn btn-ghost">Искать</button>
              </form>
            ) : null}

            <blockquote className="quote lib-citation">
              <div className="quote-text">{b.citation}</div>
              <span className="source">Так можно сослаться на эту книгу.</span>
            </blockquote>
          </div>
        </section>

        {q && !closed ? (
          <section className="block" id="search">
            <h2>Поиск по книге, запрос «{q}»</h2>
            {q.length < 3 ? (
              <p className="muted">Напиши хотя бы три буквы, чтобы искать по страницам.</p>
            ) : !search ? (
              <Empty>Поиск сейчас не отвечает. Попробуй ещё раз через минуту.</Empty>
            ) : !search.hits.length ? (
              <Empty>
                На страницах этой книги «{q}» не нашлось. Поиск идёт по распознанному тексту, и старое
                написание может мешать. Попробуй начало слова, например «ромашк» вместо «ромашка».
              </Empty>
            ) : (
              <>
                <p className="section-lead">
                  {open
                    ? `Нашлось на ${fmtInt(search.hits.length)} ${pluralRu(search.hits.length, "странице", "страницах", "страницах")}${search.hits.length >= 20 ? ", показаны первые 20" : ""}.`
                    : `Текст страниц этой книги закрыт, поэтому видны только номера страниц, где встречается «${q}»${search.hits.length >= 20 ? ", первые 20" : ""}.`}
                </p>
                <ul className="lib-hits">
                  {search.hits.map((h) => (
                    <li key={h.page} className="lib-hit">
                      <Link href={`/library/${b.id}/p/${h.page}`}>стр. {h.page}</Link>
                      {open && h.snippet ? (
                        <p>… <HighlightedText segments={markQuery(h.snippet, q)} /> …</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>
        ) : null}

        <section className="block">
          <h2>Что из книги вошло в атлас</h2>
          {stats.length ? (
            <div className="lib-stats">
              {stats.map((s) => (
                <div key={s.cap} className="card stat">
                  <div className="stat-num">{fmtInt(s.n)}</div>
                  <div className="stat-cap">{s.cap}</div>
                </div>
              ))}
            </div>
          ) : (
            <Empty>Из этой книги в атлас пока ничего не вошло.</Empty>
          )}
        </section>

        {b.top_plants.length ? (
          <section className="block">
            <SectionHead title="Растения этой книги" />
            <p className="section-lead">
              Виды, о которых книга пишет больше всего. В карточке вида собраны цитаты из этой и других книг.
            </p>
            <div className="tiles tiles-sm">{b.top_plants.slice(0, PLANTS_VISIBLE).map(plantTile)}</div>
            {b.top_plants.length > PLANTS_VISIBLE ? (
              <details className="lib-more">
                <summary>
                  Ещё {fmtInt(b.top_plants.length - PLANTS_VISIBLE)}{" "}
                  {pluralRu(b.top_plants.length - PLANTS_VISIBLE, "растение", "растения", "растений")}
                </summary>
                <div className="tiles tiles-sm">{b.top_plants.slice(PLANTS_VISIBLE).map(plantTile)}</div>
              </details>
            ) : null}
          </section>
        ) : null}

        {b.recipes.length ? (
          <section className="block">
            <SectionHead title="Рецепты из книги" href={`/recipes?book_id=${b.id}`} more="все домашние рецепты книги" />
            <p className="section-lead">
              {b.recipes.length < recipes
                ? `В книге ${fmtInt(recipes)} ${pluralRu(recipes, "рецепт", "рецепта", "рецептов")}. Здесь показаны ${fmtInt(b.recipes.length)}, первыми идут те, что можно повторить дома.`
                : "Первыми идут рецепты, которые можно повторить дома."}
            </p>
            <ul className="lib-list">
              {b.recipes.map((rc) => (
                <li key={rc.id} className="lib-row">
                  <Link href={`/recipe/${rc.id}`} className="lib-row-name">{rc.name}</Link>
                  <span className="lib-row-meta">
                    {recipeMeta(rc.category, rc.recipe_kind)}
                  </span>
                  {rc.source_page && !closed ? (
                    <Link href={`/library/${b.id}/p/${rc.source_page}`} className="lib-row-page">стр. {rc.source_page}</Link>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {toc.length ? (
          <section className="block">
            <h2>Оглавление</h2>
            <ol className="lib-toc">
              {toc.slice(0, TOC_VISIBLE).map((t, i) => <li key={i}>{t}</li>)}
            </ol>
            {toc.length > TOC_VISIBLE ? (
              <details className="lib-toc-more">
                <summary>Всё оглавление, ещё {fmtInt(toc.length - TOC_VISIBLE)}</summary>
                <ol className="lib-toc" start={TOC_VISIBLE + 1}>
                  {toc.slice(TOC_VISIBLE).map((t, i) => <li key={i}>{t}</li>)}
                </ol>
              </details>
            ) : null}
          </section>
        ) : null}

        {b.busiest_pages.length && !closed ? (
          <section className="block">
            <h2>Страницы, где больше всего цитат</h2>
            <p className="section-lead">
              {open
                ? "С этих страниц в атлас вошло больше всего цитат. С них удобно начать чтение."
                : "С этих страниц в атлас вошло больше всего цитат. У каждой открыты цитаты, которые есть в карточках растений."}
            </p>
            <div className="lib-pages">
              {b.busiest_pages.map((p) => (
                <Link key={p.page} href={`/library/${b.id}/p/${p.page}`} className="chip chip-leaf">
                  стр. {p.page}, {fmtInt(p.facts)} {pluralRu(p.facts, "цитата", "цитаты", "цитат")}
                </Link>
              ))}
            </div>
          </section>
        ) : null}

        {b.uses || recipes ? (
          <p className="footnote lib-disclaimer">
            {b.year
              ? `Это история применения растений по книге ${b.year} года, а не медицинский совет.`
              : "Это история применения растений по старой книге, а не медицинский совет."}
          </p>
        ) : null}
      </main>
      <Footer />
    </>
  );
}
