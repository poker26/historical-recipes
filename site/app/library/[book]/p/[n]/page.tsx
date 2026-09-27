import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { Header, Footer } from "../../../../ui";
import { Crumbs, Empty } from "../../../../../components/common";
import { highlightText, type Segment } from "../../../../../components/library/highlight";
import { HighlightedText } from "../../../../../components/library/HighlightedText";
import { PageFacts } from "../../../../../components/library/PageFacts";
import { PageNav } from "../../../../../components/library/PageNav";
import { SITE_URL, excerpt, isUuid } from "../../../../../lib/api";
import { FACT_KIND_RU, accessNote, getBookPage, param } from "../../../../../lib/api-library";
import "../../../library.css";

type Props = { params: { book: string; n: string }; searchParams: { [key: string]: string | string[] | undefined } };

function pageNumber(s: string | undefined): number | null {
  return s && /^\d{1,5}$/.test(s) && Number(s) >= 1 ? Number(s) : null;
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const n = pageNumber(params.n);
  if (!isUuid(params.book) || !n) return { title: "Страница не найдена", robots: { index: false } };
  const r = await getBookPage(params.book, n);
  if (!r.data) return { title: "Страница книги · Библиотека", robots: { index: false } };
  const d = r.data;
  const url = `${SITE_URL}/library/${params.book}/p/${n}`;
  const title = `${d.book.title}, стр. ${n}`;
  // В индекс идут только открытые страницы, к которым привязаны факты: у остальных
  // нет своего содержания, кроме ссылки на книгу.
  const indexable = d.book.access === "open" && d.facts.length + d.recipes.length > 0;
  return {
    title,
    description: d.citation,
    alternates: { canonical: url },
    robots: indexable ? undefined : { index: false, follow: true },
    openGraph: {
      title, description: d.citation, url, type: "article",
      ...(d.page.has_image ? { images: [{ url: `${url}/image.jpg?size=medium` }] } : {}),
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
          Страница сейчас не загрузилась: библиотека не отвечает. Обнови её через минуту или вернись
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

  // Подсветка: начало каждой цитаты и рецепта ищем в распознанном тексте страницы.
  let segments: Segment[] = [];
  let found = new Set<string>();
  if (open && page.text) {
    const needles = [
      ...d.facts.map((f) => ({ id: f.id, text: f.original_text, strong: f.id === hl })),
      ...d.recipes.map((rc) => ({ id: rc.id, text: rc.original_text, strong: rc.id === hl })),
    ];
    ({ segments, found } = highlightText(page.text, needles));
  }
  const hlInText = !!hl && found.has(hl);
  const titles = new Map<string, string>([
    ...d.facts.map((f): [string, string] => [f.id, `${f.plant_name}: ${FACT_KIND_RU[f.kind] ?? f.kind}`]),
    ...d.recipes.map((rc): [string, string] => [rc.id, `рецепт «${rc.name ?? "без названия"}»`]),
  ]);
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
                      <img src={`${imgBase}?size=medium`} alt={`Скан страницы ${n}: ${book.title}`} decoding="async" />
                    </a>
                    <figcaption className="small">
                      <span>Скан страницы {n}</span>
                      <a href={`${imgBase}?size=full`}>открыть крупно</a>
                    </figcaption>
                  </figure>
                ) : null}
                <div className="lib-textcol">
                  <p className="lib-textcap small muted">
                    Текст распознан машиной и может ошибаться в буквах{page.has_image ? ", сверяйся со сканом" : ""}.
                    {found.size ? " Подсвечены фрагменты, из которых взяты факты атласа, наведи на подсветку, чтобы увидеть какие." : ""}
                  </p>
                  {segments.length ? (
                    <div className="scan-text">
                      <HighlightedText segments={segments} titles={titles} />
                    </div>
                  ) : (
                    <Empty>У этой страницы нет распознанного текста{page.has_image ? ", только скан" : ""}.</Empty>
                  )}
                </div>
              </div>
            ) : (
              <div className="card lib-cited">
                <div className="lib-cited-num">стр. {n}</div>
                <p>{accessNote(book.access, book.year)}</p>
                <blockquote className="quote">
                  <div className="quote-text">{d.citation}</div>
                  <span className="source">так ссылаться на эту страницу</span>
                </blockquote>
              </div>
            )}

            <section className="block">
              <h2>{open ? "На этой странице" : "Фрагменты этой страницы, разобранные в атлас"}</h2>
              <PageFacts data={d} hl={hl} found={found} hlInText={hlInText} />
            </section>

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
