import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../ui";
import { Empty, Pager } from "../../components/common";
import { fmtInt, getJson, isUuid, plantHref, pluralRu } from "../../lib/api";

type BookName = { title: string; year?: number | null };
import {
  DOMAIN_RU, DOMAINS, KIND_FACET_RU, KINDS, PAGE_SIZE,
  catalogHref, catalogParams, getPlantName, getRecipeList, getRecipeVocab, inSentence, type CatalogState,
} from "../../lib/api-recipes";
import { RecipeFacets } from "../../components/recipes/RecipeFacets";
import { RecipeRow } from "../../components/recipes/RecipeRow";
import { pageMeta } from "../../components/reference/meta";
import "./recipes.css";

// Каталог живой: фильтры в адресе, данные из кэша fetch на 10 минут.
export const dynamic = "force-dynamic";

type SP = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || undefined;

function parseState(sp: SP): { state: CatalogState; page: number } {
  const kind = one(sp.kind);
  const domain = one(sp.domain);
  const plant = one(sp.plant_id);
  const book = one(sp.book_id);
  const pageRaw = parseInt(one(sp.page) || "1", 10);
  return {
    state: {
      kind: kind && (KINDS as readonly string[]).includes(kind) ? kind : undefined,
      domain: domain && (DOMAINS as readonly string[]).includes(domain) ? domain : undefined,
      category: one(sp.category)?.slice(0, 60),
      q: one(sp.q)?.slice(0, 100),
      step: one(sp.step_by_step) === "true",
      plant_id: plant && isUuid(plant) ? plant.toLowerCase() : undefined,
      book_id: book && isUuid(book) ? book.toLowerCase() : undefined,
    },
    page: Number.isFinite(pageRaw) && pageRaw > 1 ? Math.min(pageRaw, 5000) : 1,
  };
}

const KIND_H1: Record<string, string> = {
  medicinal: "Лечебные рецепты из книг",
  food: "Рецепты еды и напитков из книг",
  cosmetic: "Косметика по рецептам из книг",
  other: "Прочие домашние рецепты из книг",
};
const KIND_DESC: Record<string, string> = {
  medicinal: "Настои, отвары, сборы, мази и капли из травников и лечебников с книгой и годом у каждого рецепта. Это история применения, а не медицинский совет.",
  food: "Салаты, супы, варенья, ликёры и напитки из кулинарных книг и травников. У каждого рецепта есть книга, год и дословный текст.",
  cosmetic: "Косметические рецепты для кожи и волос из старых книг. У каждого рецепта есть книга, год и дословный текст.",
  other: "Домашние рецепты из книг, которые не относятся к еде, лечению или косметике. У каждого рецепта есть книга, год и дословный текст.",
};

function isClean(s: CatalogState) {
  return !s.kind && !s.category && !s.domain && !s.step && !s.plant_id && !s.book_id && !s.q;
}

export async function generateMetadata({ searchParams }: { searchParams: SP }): Promise<Metadata> {
  const { state, page } = parseState(searchParams);
  if (isClean(state) && page === 1) {
    return pageMeta({
      title: "Домашние рецепты из старых книг",
      description: "Настои, отвары, сборы, ликёры, варенья и мази из травников и кулинарных книг. У каждого рецепта есть книга, год и дословный текст, а растения из него ведут в атлас.",
      path: "/recipes",
    });
  }
  const onlyKind = !!state.kind && isClean({ ...state, kind: undefined }) && page === 1;
  if (onlyKind && state.kind) {
    return pageMeta({ title: KIND_H1[state.kind], description: KIND_DESC[state.kind], path: `/recipes?kind=${state.kind}` });
  }
  const plant = state.plant_id ? await getPlantName(state.plant_id) : null;
  const parts = [
    state.kind ? KIND_FACET_RU[state.kind].toLowerCase() : null,
    state.category,
    state.domain ? DOMAIN_RU[state.domain].toLowerCase() : null,
    state.step ? "пошаговые" : null,
    state.plant_id ? `с растением ${plant?.name ? inSentence(plant.name) : "из атласа"}` : null,
    state.book_id ? "из одной книги" : null,
    state.q ? `«${state.q}»` : null,
  ].filter(Boolean);
  // qs() собирает адрес через URLSearchParams, кириллица в нём уже закодирована.
  const base = catalogHref(state);
  const self = page > 1 ? `${base}${base.includes("?") ? "&" : "?"}page=${page}` : base;
  return pageMeta({
    title: `Рецепты${parts.length ? `: ${parts.join(", ")}` : ""}${page > 1 ? `, страница ${page}` : ""}`,
    description: "Домашние рецепты из травников и кулинарных книг с книгой, годом и дословным текстом.",
    path: self,
    index: false,
  });
}

export default async function RecipesPage({ searchParams }: { searchParams: SP }) {
  const { state, page } = parseState(searchParams);
  const [list, vocab, plant, book] = await Promise.all([
    // В адресе фильтр пошаговых зовётся step, а в запросе к API step_by_step.
    getRecipeList({ ...state, step_by_step: state.step }, PAGE_SIZE, (page - 1) * PAGE_SIZE),
    getRecipeVocab(),
    state.plant_id ? getPlantName(state.plant_id) : Promise.resolve(null),
    state.book_id ? getJson<BookName>(`/library/books/${state.book_id}`, 3600, 15000) : Promise.resolve(null),
  ]);
  const items = list?.data ?? [];
  const total = list?.total ?? items.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtered = !isClean(state);
  const activeCount = [state.kind, state.category, state.domain, state.step || undefined, state.plant_id, state.book_id].filter(Boolean).length;
  const onlyKind = !!state.kind && isClean({ ...state, kind: undefined });
  const h1 = onlyKind && state.kind ? KIND_H1[state.kind] : "Домашние рецепты из книг";

  return (
    <>
      <Header active="/recipes" />

      <section className="hero-grad rc-hero">
        <span className="chip">Рецепты</span>
        <h1>{h1}</h1>
        <p className="lead">
          {vocab?.total ? `В каталоге ${fmtInt(vocab.total)} ${pluralRu(vocab.total, "домашний рецепт", "домашних рецепта", "домашних рецептов")} из травников, кулинарных книг и книг о грибах. ` : "Здесь собраны домашние рецепты из травников, кулинарных книг и книг о грибах. "}
          У каждого рецепта указаны книга и год, а растения из него ведут в атлас. Заводские
          прописи и обрывки текста сюда не попали.
        </p>
        <form className="rc-search" action="/recipes" method="get" role="search">
          <input type="search" name="q" defaultValue={state.q ?? ""} placeholder="Крапива, наливка или сбор при кашле" aria-label="Поиск по рецептам" />
          {state.kind ? <input type="hidden" name="kind" value={state.kind} /> : null}
          {state.category ? <input type="hidden" name="category" value={state.category} /> : null}
          {state.domain ? <input type="hidden" name="domain" value={state.domain} /> : null}
          {state.step ? <input type="hidden" name="step_by_step" value="true" /> : null}
          {state.plant_id ? <input type="hidden" name="plant_id" value={state.plant_id} /> : null}
          {state.book_id ? <input type="hidden" name="book_id" value={state.book_id} /> : null}
          <button type="submit" className="btn btn-primary btn-sm">Найти</button>
        </form>
      </section>

      <div className="with-aside rc-layout">
        <aside className="aside rc-filters-d" aria-label="Фильтры рецептов">
          <RecipeFacets vocab={vocab} state={state} />
        </aside>

        <div>
          <details className="card card-tight rc-filters-m">
            <summary>Фильтры{activeCount ? `, выбрано ${activeCount}` : ""}</summary>
            <RecipeFacets vocab={vocab} state={state} />
          </details>

          {state.plant_id ? (
            <div className="card card-soft card-tight rc-banner">
              <span>
                Рецепты с растением{" "}
                <b><Link href={plantHref(state.plant_id, plant?.name_latin)}>{plant?.name || "из атласа"}</Link></b>
              </span>
              <Link href={catalogHref(state, { plant_id: undefined })} className="btn btn-ghost btn-sm">Снять фильтр</Link>
            </div>
          ) : null}

          {state.book_id ? (
            <div className="card card-soft card-tight rc-banner">
              <span>
                Рецепты из книги{" "}
                <b><Link href={`/library/${state.book_id}`}>{book?.title ? `«${book.title}»${book.year ? `, ${book.year}` : ""}` : "из библиотеки"}</Link></b>
              </span>
              <Link href={catalogHref(state, { book_id: undefined })} className="btn btn-ghost btn-sm">Снять фильтр</Link>
            </div>
          ) : null}

          {list ? (
            <div className="toolbar">
              <span>
                {total
                  ? `Нашлось ${fmtInt(total)} ${pluralRu(total, "рецепт", "рецепта", "рецептов")}${pages > 1 ? `, страница ${fmtInt(page)} из ${fmtInt(pages)}` : ""}`
                  : "Ничего не нашлось"}
              </span>
              {filtered ? <Link href="/recipes" className="more">Сбросить фильтры</Link> : null}
            </div>
          ) : null}

          {!list ? (
            <Empty>
              Каталог сейчас не загрузился. Обнови страницу через минуту, а пока загляни в <Link href="/atlas">атлас</Link>.
            </Empty>
          ) : items.length ? (
            <div className="rc-list">
              {items.map((r) => <RecipeRow key={r.id} r={r} />)}
            </div>
          ) : (
            <Empty>
              {state.q && isClean({ ...state, q: undefined })
                ? `По запросу «${state.q}» рецептов нет. Попробуй другое слово или открой `
                : state.q
                  ? `По запросу «${state.q}» с этими фильтрами рецептов нет. Сними часть фильтров или открой `
                  : "С такими фильтрами рецептов нет. Сними часть фильтров или открой "}
              <Link href="/recipes">весь каталог</Link>.
            </Empty>
          )}

          <Pager page={page} pages={pages} base="/recipes" params={catalogParams(state)} />
          <p className="footnote rc-foot">Это рецепты из книг разных лет, а не медицинский совет.</p>
        </div>
      </div>

      <Footer />
    </>
  );
}
