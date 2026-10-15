import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer, DownloadButtons } from "../ui";
import { Crumbs } from "../../components/common";
import { DEFAULT_OG, SITE_URL, fmtInt, getJson, pluralRu } from "../../lib/api";
import { getLibraryStats } from "../../lib/api-library";
import "./about.css";

// О проекте: что есть в атласе, как сверить запись с книгой, какие книги открыты и чему атлас
// не учит. Без внутренней кухни (как устроен разбор книг, модели, проверки): см. навык ui-canon.
// Числа берутся из бэкенда, чтобы страница не устаревала.

const title = "О проекте";
const description =
  "«Что растёт» это атлас растений и грибов по старым травникам, лечебникам и справочникам: у каждого факта указаны книга, год и страница, а старые книги можно листать целиком.";

export const metadata: Metadata = {
  title,
  description,
  alternates: { canonical: `${SITE_URL}/about` },
  openGraph: { title, description, url: `${SITE_URL}/about`, type: "article", images: [DEFAULT_OG] },
};

export default async function AboutPage() {
  const [stats, plants, recipes] = await Promise.all([
    getLibraryStats(),
    getJson<{ total: number }>(`/plants/sitemap?limit=1`, 21600, 30000),
    getJson<{ total: number }>(`/recipes/sitemap?limit=1`, 21600, 30000),
  ]);
  const books = stats?.books ?? null;
  const years = stats?.year_min && stats?.year_max ? ` с ${stats.year_min} по ${stats.year_max} год` : "";

  return (
    <>
      <Header />
      <Crumbs items={[{ label: title }]} />
      <main className="prose about">
        <h1>«Что растёт»</h1>

        <p className="lead">
          «Что растёт» это атлас растений и грибов, собранный из старых и не очень старых книг.
          {books ? ` В его основе ${fmtInt(books)} ${pluralRu(books, "книга", "книги", "книг")}${years}: ` : " В его основе "}
          травники, народные лечебники, поваренные книги, определители и справочники по химическому составу растений.
        </p>

        <h2>Что есть в атласе</h2>
        <p>
          О каждом растении собрано, от чего его применяли, какие вещества в нём находили, когда и какую часть его
          собирали, чем оно ядовито и что из него готовили.
          {plants?.total ? ` Сейчас в атласе ${fmtInt(plants.total)} ${pluralRu(plants.total, "вид", "вида", "видов")}.` : ""}
          {recipes?.total ? ` Рядом лежат ${fmtInt(recipes.total)} ${pluralRu(recipes.total, "домашний рецепт", "домашних рецепта", "домашних рецептов")}, записанных по шагам.` : ""}
        </p>
        <p>
          У каждого факта указаны книга и год, а у большинства ещё и страница. Ссылка на страницу открывает её в{" "}
          <Link href="/library">библиотеке</Link>, и запись можно прочитать в самой книге.
        </p>
        <p>
          Растение можно найти по любому из его названий: по старому, по местному или по латинскому. Болезни тоже
          ищутся по старым названиям: растения, которые применяли от водянки, найдутся и по слову «отёки».
        </p>
        <p>
          У каждого вида указано, можно ли его есть, от «нет данных» до «смертельно ядовито». Если у съедобного
          растения есть ядовитый двойник, он назван в карточке.
        </p>

        <h2>Какие книги открыты целиком</h2>
        <p>
          Книги, изданные до 1917 года, можно листать целиком по сканам страниц. У остальных открыты описание,
          номера страниц и цитаты, которые вошли в атлас.
          {stats?.open_books ? ` Сейчас целиком открыты ${fmtInt(stats.open_books)} ${pluralRu(stats.open_books, "книга", "книги", "книг")}.` : ""}
        </p>

        <h2>Чему атлас не учит</h2>
        <p>
          Атлас рассказывает, как растения применяли по книгам, и не советует, чем лечиться. Многие старые средства
          оказались бесполезными или опасными.
        </p>
        <p>
          Если нужен совет о лечении, спрашивай врача. Если собираешься есть найденное растение или гриб, сначала
          открой его карточку: там сказано, чем он опасен и с чем его путают.
        </p>

        <h2>Приложение</h2>
        <p>
          Тот же атлас есть в приложении «Что растёт». Оно определяет растение по фотографии и показывает, что растёт
          рядом.
        </p>
        <DownloadButtons />

        <p className="footnote">
          Разделы сайта: <Link href="/atlas">атлас</Link>, <Link href="/recipes">рецепты</Link>,{" "}
          <Link href="/library">библиотека</Link>, <Link href="/reference">справочники</Link> и{" "}
          <Link href="/season">календарь сбора</Link>.
        </p>
      </main>
      <Footer />
    </>
  );
}
