import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Header, Footer } from "../../../../ui";
import { Crumbs, Empty } from "../../../../../components/common";
import { PageFacts } from "../../../../../components/library/PageFacts";
import { PageNav } from "../../../../../components/library/PageNav";
import { DEFAULT_OG, SITE_URL, excerpt, isUuid, pluralRu } from "../../../../../lib/api";
import { accessNote, getBookPage, param, type BookPage } from "../../../../../lib/api-library";
import "../../../library.css";

type Props = { params: { book: string; n: string }; searchParams: { [key: string]: string | string[] | undefined } };

function pageNumber(s: string | undefined): number | null {
  return s && /^\d{1,5}$/.test(s) && Number(s) >= 1 ? Number(s) : null;
}

/** «, 1870 год», если года нет в самом названии книги. */
function yearTail(d: BookPage, word = ""): string {
  const y = d.book.year;
  return y && !d.book.title.includes(String(y)) ? `, ${y}${word}` : "";
}

/** Описание для поисковика: что с этой страницы вошло в атлас. */
function pageSummary(d: BookPage, n: number): string {
  const parts: string[] = [];
  const np = d.plants.length;
  if (np) {
    const shown = d.plants.slice(0, 4).map((p) => p.name).join(", ");
    parts.push(`${np} ${pluralRu(np, "растение", "растения", "растений")} (${shown}${np > 4 ? ` и ещё ${np - 4}` : ""})`);
  }
  const nr = d.recipes.length;
  if (nr) parts.push(`${nr} ${pluralRu(nr, "рецепт", "рецепта", "рецептов")}`);
  return `Скан страницы ${n} из книги «${d.book.title}»${yearTail(d, " год")}. В атласе с этой страницы ${parts.join(" и ")}.`;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const n = pageNumber(params.n);
  if (!isUuid(params.book) || !n) return { title: "Страница не найдена", robots: { index: false } };
  const r = await getBookPage(params.book, n);
  if (!r.data) return { title: "Страница книги", robots: { index: false } };
  const d = r.data;
  const url = `${SITE_URL}/library/${params.book}/p/${n}`;
  const title = `${d.book.title}${yearTail(d)}, страница ${n}`;
  // В индекс идут только открытые страницы, к которым привязаны факты: у остальных
  // нет своего содержания, кроме ссылки на книгу.
  const indexable = d.book.access === "open" && d.facts.length + d.recipes.length > 0;
  const description = indexable ? pageSummary(d, n) : d.citation;
  return {
    title,
    description,
    alternates: { canonical: url },
    robots: indexable ? undefined : { index: false, follow: true },
    openGraph: {
      title, description, url, type: "article",
      images: d.page.has_image ? [{ url: `${url}/image.jpg?size=medium` }] : [DEFAULT_OG],
    },
  };
}

export default async function SourcePage({ params, searchParams }: Props) {
  const n = pageNumber(params.n);
  if (!isUuid(params.book) || !n) notFound();
  const r = await getBookPage(params.book, n);
  if (r.status === 404 || r.status === 422) notFound();
  const d = r.data;
  const bookId = params.book;

  // Форма «к странице №» приходит сюда с ?go=; номер зажимаем в пределы книги.
  const goRaw = param(searchParams.go);
  if (goRaw !== undefined) {
    const go = pageNumber(goRaw);
    if (go) {
      const lo = d?.nav.first ?? 1;
      const hi = d?.nav.last ?? go;
      redirect(`/library/${bookId}/p/${Math.min(Math.max(go, lo), hi)}`);
    }
    redirect(`/library/${bookId}/p/${n}`);
  }

  if (!d) {
    return (
      <>
        <Header active="/library" />
        <Crumbs items={[{ href: "/library", label: "Библиотека" }, { href: `/library/${bookId}`, label: "Книга" }, { label: `стр. ${n}` }]} />
        <Empty>
          Страница сейчас не загрузилась, библиотека не отвечает. Обнови её через минуту или вернись
          к <Link href={`/library/${bookId}`}>книге</Link>.
        </Empty>
        <Footer />
      </>
    );
  }

  const { book, page } = d;
  const open = book.access === "open";
  const closed = book.access === "closed";
  const hl = param(searchParams.hl);
  // Сырой распознанный текст страницы не показываем: в старых книгах в нём ошибки машины
  // и дореформенная орфография. Рядом со сканом стоит то, что со страницы вошло в атлас.
  const imgBase = `/library/${bookId}/p/${n}/image.jpg`;
  const who = [book.author, book.year].filter(Boolean).join(", ");

  return (
    <>
      <Header active="/library" />
      <main className="lib-page">
        <Crumbs
          items={[
            { href: "/library", label: "Библиотека" },
            { href: `/library/${bookId}`, label: excerpt(book.title, 60) },
            { label: `стр. ${n}` },
          ]}
        />
        <div className="lib-pagehead">
          <h1>Страница {n}</h1>
          <div className="lib-pagehead-book">
            <Link href={`/library/${bookId}`}>{book.title}</Link>
            {who ? <span className="muted">, {who}</span> : null}
          </div>
        </div>

        {closed ? (
          <div className="card lib-cited">
            <p>{accessNote(book.access, book.year)}</p>
            <Link href={`/library/${bookId}`} className="btn btn-ghost btn-sm">К карточке книги</Link>
          </div>
        ) : (
          <>
            <PageNav bookId={bookId} n={n} nav={d.nav} />

            {open ? (
              <div className={"scan" + (page.has_image ? "" : " lib-scan-single")}>
                {page.has_image ? (
                  <figure className="scan-img lib-scan-fig">
                    <a href={`${imgBase}?size=full`} title="Открыть скан крупно">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={`${imgBase}?size=medium`} alt={`Скан страницы ${n} книги «${book.title}»`} decoding="async" />
                    </a>
                    <figcaption className="small">
                      <span>Скан страницы {n}</span>
                      <a href={`${imgBase}?size=full`}>открыть крупно</a>
                    </figcaption>
                  </figure>
                ) : null}
                <section className="lib-onpage-col">
                  <h2>Что со страницы {n} вошло в атлас</h2>
                  <PageFacts data={d} hl={hl} />
                </section>
              </div>
            ) : (
              <>
                <div className="card lib-cited">
                  <div className="lib-cited-num">стр. {n}</div>
                  <p>{accessNote(book.access, book.year)}</p>
                  <blockquote className="quote">
                    <div className="quote-text">{d.citation}</div>
                    <span className="source">Так можно сослаться на эту страницу.</span>
                  </blockquote>
                </div>
                <section className="block">
                  <h2>Цитаты с этой страницы, которые вошли в атлас</h2>
                  <PageFacts data={d} hl={hl} />
                </section>
              </>
            )}

            {open ? (
              <section className="block">
                <h2>Как сослаться на эту страницу</h2>
                <blockquote className="quote">
                  <div className="quote-text">{d.citation}</div>
                </blockquote>
              </section>
            ) : null}

            <PageNav bookId={bookId} n={n} nav={d.nav} />

            {d.facts.length || d.recipes.length ? (
              <p className="footnote lib-disclaimer">
                {book.year
                  ? `Это история применения растений по книге ${book.year} года, а не медицинский совет.`
                  : "Это история применения растений по старой книге, а не медицинский совет."}
              </p>
            ) : null}
          </>
        )}
      </main>
      <Footer />
    </>
  );
}
