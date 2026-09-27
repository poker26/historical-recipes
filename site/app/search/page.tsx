import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { Header, Footer } from "../ui";
import { Empty, LeafGlyph, SectionHead } from "../../components/common";
import { SITE_URL, excerpt, fmtInt, plantHref, pluralRu } from "../../lib/api";
import {
  RECIPE_KIND_RU,
  cleanFragment,
  conditionPhrase,
  creditShort,
  creditSource,
  displayName,
  familyShort,
  getSuggest,
  listPlants,
  modernName,
  norm,
  pickAnswer,
  plantTags,
  rankForQuery,
  searchBooks,
  searchRecipes,
  searchSections,
  type BookHit,
  type PlantSummary,
  type Suggest,
  type SuggestPlant,
} from "../../lib/api-atlas";
import { atlasHref, firstParam, type SearchParams } from "../../components/atlas/params";
import { PlantGrid, TagList, usesLabel } from "../../components/atlas/plants";
import "../atlas/atlas.css";

// Единый поиск сайта: виды (все карточки корпуса, не только атлас), показания и действия,
// домашние рецепты, книги и смысловые фрагменты книг. Быстрые запросы идут параллельно,
// смысловой поиск медленный (несколько секунд) и дорисовывается потоком через Suspense.

type Props = { searchParams: SearchParams };

const readQ = (sp: SearchParams) => (firstParam(sp.q) ?? "").slice(0, 80);

const EXAMPLES = ["крапива", "Urtica dioica", "петровы батоги", "водянка", "бессонница", "Смельской"];

const SYSTEM_RU: Record<string, string> = {
  "дыхание": "органы дыхания",
  "ССС": "сердце и сосуды",
  "ЖКТ": "пищеварение",
  "ЦНС": "нервная система",
  "кожа": "кожа",
  "мочеполовая": "мочеполовая система",
  "инфекции": "инфекции",
  "обмен": "обмен веществ",
};

export async function generateMetadata({ searchParams }: Props): Promise<Metadata> {
  const q = readQ(searchParams);
  const title = q ? `Поиск: ${q}` : "Поиск";
  const description =
    "Поиск по атласу растений и грибов, домашним рецептам и библиотеке травников: русское и латинское имя, старое название, симптом или книга.";
  const canonical = `${SITE_URL}/search`;
  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title, description, url: canonical, type: "website" },
    robots: { index: false, follow: true },
  };
}

/** Карточка из подсказки, когда её нет в списке видов: без подписи фото снимок не показываем. */
const fromSuggest = (s: SuggestPlant): PlantSummary => ({
  id: s.id,
  name: s.name,
  name_latin: s.name_latin,
  name_modern: s.name_modern,
  photo_url: s.photo_url,
  photo_attribution: null,
  kingdom: s.kingdom,
  rank: s.rank,
  uses_count: s.uses ?? 0,
});

function mergeBooks(lib: BookHit[], fromSuggest: Suggest["books"]): BookHit[] {
  const seen = new Set(lib.map((b) => b.id));
  const extra = fromSuggest.filter((b) => !seen.has(b.id)).map((b) => ({ id: b.id, title: b.title, author: b.author, year: b.year }));
  return [...lib, ...extra].slice(0, 8);
}

/** «41 вид, 1 111 домашних рецептов и 1 книга». */
function joinRu(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} и ${parts[parts.length - 1]}`;
}

function AnswerCard({ p }: { p: PlantSummary }) {
  const name = displayName(p.name);
  const credit = p.photo_url ? creditShort(p.photo_attribution) : null;
  const src = creditSource(p.photo_attribution);
  const photo = p.photo_url && credit ? p.photo_url : null;
  const modern = modernName(p);
  const meta = [usesLabel(p.uses_count), familyShort(p)].filter(Boolean).join(" · ");
  return (
    <Link href={plantHref(p.id, p.name_latin)} className="card srch-answer">
      <div>
        <div className="srch-answer-photo">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {photo ? <img src={photo} alt={name} loading="lazy" /> : <LeafGlyph />}
        </div>
        {photo ? (
          <div className="credit" title={p.photo_attribution ?? undefined}>
            Фото: {credit}
            {src ? ` · ${src}` : ""}
          </div>
        ) : null}
      </div>
      <div>
        <span className="chip chip-leaf">Лучшее совпадение</span>
        <h2>{name}</h2>
        {p.name_latin ? <div className="latin">{p.name_latin}</div> : null}
        {modern ? <div className="muted small">совр.: {modern}</div> : null}
        {meta ? <p className="muted srch-answer-meta">{meta}</p> : null}
        <TagList tags={plantTags(p)} />
        <div>
          <span className="btn btn-primary btn-sm">Открыть карточку</span>
        </div>
      </div>
    </Link>
  );
}

/** Смысловой поиск по фрагментам книг: медленный, поэтому дорисовывается отдельно. */
async function Fragments({ q }: { q: string }) {
  const hits = await searchSections(q, 6);
  const seen = new Set<string>();
  const items = (hits ?? []).filter((h) => {
    const p = h.payload;
    if (!p?.book_id || !(p.content || p.title)) return false;
    const key = `${p.book_id}|${p.title ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!items.length) return null;
  return (
    <section className="srch-block">
      <SectionHead title="Фрагменты из книг" />
      <p className="section-lead">Места в книгах, близкие к запросу по смыслу, а не только по словам.</p>
      <div className="srch-frags">
        {items.map((h) => {
          const p = h.payload!;
          const source = [p.author, p.book_title].filter(Boolean).join(". ") + (p.year ? `, ${p.year}` : "");
          return (
            <Link key={h.id} href={`/library/${p.book_id}`} className="card card-tight srch-frag">
              {p.title ? <b>{p.title}</b> : null}
              {p.content ? <p>{cleanFragment(p.content, 200)}</p> : null}
              {source ? <span className="source">{source}</span> : null}
            </Link>
          );
        })}
      </div>
    </section>
  );
}

function SearchHint({ q }: { q: string }) {
  return (
    <>
      <Header active="/atlas" q={q} />
      <section className="card srch-hint">
        <h1>Поиск по атласу, рецептам и книгам</h1>
        <p className="lead">
          {q ? "Для поиска нужно хотя бы две буквы. " : ""}
          Ищи по русскому имени растения или гриба, по латыни, по старому названию из травника, по симптому или по
          названию книги.
        </p>
        <form className="atlas-search" action="/search" method="get" role="search">
          <input
            type="search"
            name="q"
            defaultValue={q}
            placeholder="Крапива, Urtica dioica, водянка или травник"
            aria-label="Поиск по атласу, рецептам и книгам"
          />
          <button type="submit" className="btn btn-primary">
            Найти
          </button>
        </form>
        <div className="atlas-entries">
          <span className="atlas-entries-lbl">Например:</span>
          {EXAMPLES.map((e) => (
            <Link key={e} href={`/search?q=${encodeURIComponent(e)}`} className="chip chip-leaf">
              {e}
            </Link>
          ))}
        </div>
      </section>
      <Footer />
    </>
  );
}

export default async function SearchPage({ searchParams }: Props) {
  const q = readQ(searchParams);
  if (q.length < 2) return <SearchHint q={q} />;

  const [sug, plants, recipes, books] = await Promise.all([
    getSuggest(q, 12),
    listPlants({ q, sort: "photo", limit: 24 }),
    searchRecipes(q, 12),
    searchBooks(q, 8),
  ]);

  // Виды: только растения и грибы; сначала совпадения по имени, потом по старым именам.
  const pool = plants.items.filter((p) => !p.kingdom || p.kingdom === "растение" || p.kingdom === "гриб");
  const best = pickAnswer(sug, q);
  const answer: PlantSummary | null = best ? pool.find((p) => p.id === best.id) ?? fromSuggest(best) : null;
  const ranked = rankForQuery(pool.filter((p) => p.id !== answer?.id), q);
  const species = ranked.map((r) => r.p);
  const notes: Record<string, string> = {};
  for (const r of ranked) if (r.oldName) notes[r.p.id] = `в книгах: «${r.oldName}»`;

  const inds = (sug?.indications ?? []).filter((i) => (i.facts ?? 0) > 0);
  const acts = sug?.actions ?? [];
  const bookList = mergeBooks(books?.items ?? [], sug?.books ?? []);
  const bookTotal = Math.max(books?.total ?? 0, bookList.length);

  const down = !sug && !plants.ok && !recipes.ok && !books;
  const nothing = !answer && !species.length && !inds.length && !acts.length && !recipes.items.length && !bookList.length;

  const summary: string[] = [];
  if (plants.total) summary.push(`${fmtInt(plants.total)} ${pluralRu(plants.total, "вид", "вида", "видов")}`);
  if (recipes.total)
    summary.push(`${fmtInt(recipes.total)} ${pluralRu(recipes.total, "домашний рецепт", "домашних рецепта", "домашних рецептов")}`);
  if (bookTotal) summary.push(`${fmtInt(bookTotal)} ${pluralRu(bookTotal, "книга", "книги", "книг")}`);

  const speciesBlock = species.length ? (
    <section className="srch-block" key="species">
      <SectionHead title="Виды" href={atlasHref({}, { q })} more="искать в атласе" />
      <p className="section-lead">
        {plants.total > pool.length
          ? `Первые ${fmtInt(pool.length)} из ${fmtInt(plants.total)} ${pluralRu(plants.total, "карточки", "карточек", "карточек")}, сначала с фотографией и самые полные. `
          : ""}
        Здесь все карточки корпуса, в том числе ещё без фотографии; в атлас попадают только виды с фото и сверенным
        именем.
      </p>
      <PlantGrid items={species} noPhotoTag notes={notes} />
    </section>
  ) : null;

  const conditionsBlock =
    inds.length || acts.length ? (
      <section className="srch-block" key="conditions">
        <SectionHead title="Показания и действия" />
        <p className="section-lead">
          Состояния и действия из справочника атласа, где встречается «{q}». Старые названия болезней в нём сведены к
          современным.
        </p>
        <div className="srch-conds">
          {inds.map((i) => (
            <div key={i.id} className="card card-tight srch-cond">
              <b>{i.name}</b>
              {i.name_modern && norm(i.name_modern) !== norm(i.name) ? (
                <span className="small">современное название: {i.name_modern}</span>
              ) : null}
              <span className="small muted">
                {[
                  i.system ? SYSTEM_RU[i.system] ?? null : null,
                  i.facts ? `${fmtInt(i.facts)} ${pluralRu(i.facts, "запись", "записи", "записей")} в книгах` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              <div className="srch-cond-links">
                <Link href={`/atlas/for/${encodeURIComponent(i.name)}`}>Растения {conditionPhrase(i.name, false)} →</Link>
                <Link href={`/indications/${i.id}`}>о показании</Link>
              </div>
            </div>
          ))}
          {acts.map((a) => (
            <div key={a.name} className="card card-tight srch-cond">
              <b>{a.name}</b>
              <span className="small muted">
                действие · {fmtInt(a.plants)} {pluralRu(a.plants, "растение", "растения", "растений")} в корпусе
              </span>
              <div className="srch-cond-links">
                <Link href={`/atlas/for/${encodeURIComponent(a.name)}`}>Растения {conditionPhrase(a.name, true)} →</Link>
                <Link href={`/actions/${encodeURIComponent(a.name)}`}>о действии</Link>
              </div>
            </div>
          ))}
        </div>
      </section>
    ) : null;

  return (
    <>
      <Header active="/atlas" q={q} />

      <section className="srch-head">
        <h1>Поиск: «{q}»</h1>
        {summary.length ? <p className="srch-sum">В корпусе нашлось {joinRu(summary)}.</p> : null}
      </section>

      {down ? (
        <Empty>Поиск сейчас не отвечает. Попробуй через минуту или открой <Link href="/atlas">атлас</Link>.</Empty>
      ) : nothing ? (
        <Empty>
          По запросу «{q}» в атласе, рецептах и книгах ничего не нашлось. Попробуй латинское название (например, Urtica
          dioica), старое имя растения или другое написание.
        </Empty>
      ) : null}

      {answer ? <AnswerCard p={answer} /> : null}

      {answer ? [speciesBlock, conditionsBlock] : [conditionsBlock, speciesBlock]}

      {recipes.items.length ? (
        <section className="srch-block">
          <SectionHead
            title="Рецепты"
            href={`/recipes?q=${encodeURIComponent(q)}`}
            more={recipes.total > recipes.items.length ? `все ${fmtInt(recipes.total)}` : "в каталоге"}
          />
          <div className="srch-recipes">
            {recipes.items.map((r) => {
              const year = r.book_year ?? r.year;
              const source = [r.book_author, r.book_title].filter(Boolean).join(". ") + (year ? `, ${year}` : "");
              return (
                <Link key={r.id} href={`/recipe/${r.id}`} className="card card-tight srch-recipe">
                  <b>{r.name || "Рецепт без названия"}</b>
                  <div className="small muted">
                    {[r.category, r.recipe_kind ? RECIPE_KIND_RU[r.recipe_kind] ?? r.recipe_kind : null].filter(Boolean).join(" · ")}
                  </div>
                  {r.excerpt ? <p className="small">{excerpt(r.excerpt, 170)}</p> : null}
                  {source ? <div className="small muted">{source}</div> : null}
                </Link>
              );
            })}
          </div>
        </section>
      ) : null}

      {bookList.length ? (
        <section className="srch-block">
          <SectionHead title="Книги" href={`/library?q=${encodeURIComponent(q)}`} more="в библиотеке" />
          <div className="srch-books">
            {bookList.map((b) => {
              const stats = [
                b.plants ? `${fmtInt(b.plants)} ${pluralRu(b.plants, "растение", "растения", "растений")}` : null,
                b.recipes ? `${fmtInt(b.recipes)} ${pluralRu(b.recipes, "рецепт", "рецепта", "рецептов")}` : null,
                b.pages ? `${fmtInt(b.pages)} ${pluralRu(b.pages, "страница", "страницы", "страниц")}` : null,
              ]
                .filter(Boolean)
                .join(" · ");
              return (
                <Link key={b.id} href={`/library/${b.id}`} className="card card-tight srch-book">
                  <b>{b.title}</b>
                  <div className="small muted">{[b.author, b.year].filter(Boolean).join(", ") || "автор и год пока не указаны"}</div>
                  {stats ? <div className="small muted">{stats}</div> : null}
                  {b.access === "open" ? <span className="chip chip-leaf">можно читать целиком</span> : null}
                </Link>
              );
            })}
          </div>
        </section>
      ) : null}

      <Suspense fallback={<p className="srch-wait">Ищу похожие места в книгах…</p>}>
        <Fragments q={q} />
      </Suspense>

      {inds.length || acts.length || recipes.items.length ? (
        <p className="footnote atlas-disclaimer">
          Показания и рецепты взяты из книг 1790–2020 годов: это история применения, а не медицинский совет.
        </p>
      ) : null}

      <Footer />
    </>
  );
}
