import type { Metadata } from "next";
import Link from "next/link";
import { Header, Footer } from "../ui";
import { Empty, Pager } from "../../components/common";
import { SITE_URL, fmtInt, pluralRu } from "../../lib/api";
import {
  POPULAR_CONDITIONS,
  conditionPhrase,
  getBiotopes,
  getFamilies,
  getPlantFacets,
  listPlants,
  type PlantQuery,
} from "../../lib/api-atlas";
import { activeFilterKeys, atlasHref, pagerParams, parseAtlasParams, type AtlasState, type SearchParams } from "../../components/atlas/params";
import { AtlasFacets, ActiveFilters } from "../../components/atlas/facets";
import { PhotoSourcesNote, PlantGrid, PlantTable, Seg } from "../../components/atlas/plants";
import "./atlas.css";

// Каталог видов: только карточки, прошедшие гейт публикации (растение или гриб, чистое
// русское имя, похожая на правду латынь, фото). Остальные карточки корпуса открываются
// через поиск. Фильтры и страницы живут в адресе, всё на серверном рендере без JS.

const PAGE_SIZE = 36;

type Props = { searchParams: SearchParams };

const onlyFungi = (s: AtlasState) =>
  s.kingdom === "гриб" && activeFilterKeys(s).length === 1 && !s.sort && !s.view && !s.page;
const isClean = (s: AtlasState) => activeFilterKeys(s).length === 0 && !s.sort && !s.view && !s.page;

export async function generateMetadata({ searchParams }: Props): Promise<Metadata> {
  const s = parseAtlasParams(searchParams);
  // Заголовок «Атлас грибов» у любой страницы только с царством «гриб»; в индекс идёт лишь первая.
  const fungi = s.kingdom === "гриб" && activeFilterKeys(s).length === 1;
  const indexable = onlyFungi(s) || isClean(s);
  const title = fungi ? "Атлас грибов" : "Атлас растений и грибов";
  const description = fungi
    ? "Грибы с фотографиями. Для каждого собрано, съедобен ли он и чем опасен, как его применяли по старым книгам и где его находят."
    : "Растения и грибы с фотографиями. Для каждого вида собрано, как его применяли по травникам и справочникам с 1790 года, что в нём содержится, чем он опасен и что из него готовили.";
  const canonical = fungi ? `${SITE_URL}${atlasHref({}, { kingdom: "гриб" })}` : `${SITE_URL}/atlas`;
  return {
    title,
    description,
    alternates: { canonical },
    openGraph: { title, description, url: canonical, type: "website" },
    robots: indexable ? undefined : { index: false, follow: true },
  };
}

export default async function AtlasPage({ searchParams }: Props) {
  const s = parseAtlasParams(searchParams);
  const page = s.page ?? 1;
  const filtered = activeFilterKeys(s).length > 0;
  const landing = isClean(s);
  const fungiOnly = s.kingdom === "гриб" && activeFilterKeys(s).length === 1;

  const filters: Omit<PlantQuery, "limit"> = {
    q: s.q,
    kingdom: s.kingdom,
    family: s.family,
    action: s.action,
    indication: s.indication,
    biotope: s.biotope,
    edibility: s.edibility,
    edible: s.edible === "true" || undefined,
    is_toxic: s.is_toxic === "true" || undefined,
  };
  const withoutKingdom: Omit<PlantQuery, "limit"> = { ...filters, kingdom: undefined };
  const sort: PlantQuery["sort"] = s.sort === "name" ? "name" : s.sort === "uses" ? "uses" : "photo";

  const [list, corpus, atlasAll, facets, families, biotopes, plantsN, fungiN] = await Promise.all([
    listPlants({ ...filters, published: true, sort, limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    // те же фильтры по всему корпусу, без гейта: сколько карточек ещё не дошло до атласа
    listPlants({ ...filters, limit: 1 }),
    filtered ? listPlants({ published: true, limit: 1 }) : Promise.resolve(null),
    getPlantFacets(),
    getFamilies(200),
    getBiotopes(),
    listPlants({ ...withoutKingdom, kingdom: "растение", published: true, limit: 1 }),
    listPlants({ ...withoutKingdom, kingdom: "гриб", published: true, limit: 1 }),
  ]);

  const total = list.total;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const atlasTotal = filtered ? atlasAll?.total ?? 0 : total;
  // Для чистого атласа корпус считаем по растениям и грибам (без карточек веществ и животных).
  const corpusPlantsFungi = (facets?.kingdom ?? [])
    .filter((k) => k.value === "растение" || k.value === "гриб")
    .reduce((sum, k) => sum + k.count, 0);
  const corpusTotal = !filtered && corpusPlantsFungi ? corpusPlantsFungi : corpus.total;

  const from = (page - 1) * PAGE_SIZE + 1;
  const to = Math.min(page * PAGE_SIZE, total);
  const countText =
    total === 0
      ? "Ничего не нашлось"
      : pages > 1
        ? `${fmtInt(from)}–${fmtInt(to)} из ${fmtInt(total)}`
        : `${fmtInt(total)} ${pluralRu(total, "вид", "вида", "видов")}`;

  const hidden = Object.entries({ ...s, q: undefined, page: undefined }).filter(([, v]) => v !== undefined && v !== "");
  const searchHref = s.q ? `/search?q=${encodeURIComponent(s.q)}` : "/search";

  let content;
  if (!list.ok) {
    content = (
      <Empty>
        Атлас сейчас не отвечает. Обнови страницу через минуту или попробуй <Link href="/search">поиск</Link>.
      </Empty>
    );
  } else if (total === 0) {
    content = (
      <Empty>
        {s.q ? <>В атласе пока нет видов с фотографией по запросу «{s.q}». </> : <>По этим фильтрам в атласе пока нет видов с фотографией. </>}
        <Link href={atlasHref({ sort: s.sort, view: s.view })}>Сбрось фильтры</Link>
        {s.q ? (
          <>
            {" "}или <Link href={searchHref}>поищи среди всех карточек</Link>, в том числе без фотографии.
          </>
        ) : null}
      </Empty>
    );
  } else if (!list.items.length) {
    content = (
      <Empty>
        Такой страницы в выдаче нет, всего {fmtInt(pages)} {pluralRu(pages, "страница", "страницы", "страниц")}.{" "}
        <Link href={atlasHref(s, { page: undefined })}>Вернуться к первой</Link>
      </Empty>
    );
  } else {
    content = s.view === "table" ? <PlantTable items={list.items} /> : <PlantGrid items={list.items} />;
  }

  return (
    <>
      <Header active="/atlas" />

      <section className={"hero-grad atlas-hero" + (landing ? "" : " atlas-hero-compact")}>
        <h1>{fungiOnly ? "Атлас грибов" : "Атлас растений и грибов"}</h1>
        {landing && atlasTotal ? (
          <p className="lead">
            В атласе {fmtInt(atlasTotal)} {pluralRu(atlasTotal, "вид", "вида", "видов")} с фотографией. У каждого
            собрано, что о нём писали травники и справочники с 1790 года, и у каждого факта указаны книга и год.
          </p>
        ) : null}
        <form className="atlas-search" action="/atlas" method="get" role="search">
          {hidden.map(([k, v]) => (
            <input key={k} type="hidden" name={k} value={String(v)} />
          ))}
          <input
            type="search"
            name="q"
            defaultValue={s.q ?? ""}
            placeholder="Русское имя, латынь или старое название"
            aria-label="Поиск по атласу"
          />
          <button type="submit" className="btn btn-primary">
            Найти
          </button>
        </form>
        {landing ? (
          <div className="atlas-entries">
            <span className="atlas-entries-lbl">Растения, которые применяли</span>
            {POPULAR_CONDITIONS.map((c) => (
              <Link key={c} href={`/atlas/for/${encodeURIComponent(c)}`} className="chip">
                {conditionPhrase(c, false)}
              </Link>
            ))}
            <Link href="/places" className="chip chip-lime">
              Что растёт рядом сейчас
            </Link>
          </div>
        ) : null}
      </section>

      <div className="with-aside atlas-body">
        <AtlasFacets
          state={s}
          facets={facets}
          families={families?.families ?? null}
          biotopes={biotopes?.biotopes ?? null}
          kingdomCounts={{
            "растение": plantsN.ok ? plantsN.total : null,
            "гриб": fungiN.ok ? fungiN.total : null,
          }}
        />

        <div className="atlas-results">
          <div className="toolbar">
            <span className="atlas-count">{countText}</span>
            <span className="atlas-switches">
              <Seg
                label="Сортировка"
                items={[
                  {
                    label: "по числу записей",
                    title: "Сначала виды, о которых в книгах больше всего записей",
                    href: atlasHref(s, { sort: undefined, page: undefined }),
                    on: s.sort !== "name",
                  },
                  { label: "по имени", href: atlasHref(s, { sort: "name", page: undefined }), on: s.sort === "name" },
                ]}
              />
              <Seg
                label="Вид списка"
                items={[
                  { label: "плитки", href: atlasHref(s, { view: undefined }), on: s.view !== "table" },
                  { label: "таблица", href: atlasHref(s, { view: "table" }), on: s.view === "table" },
                ]}
              />
            </span>
          </div>

          <ActiveFilters state={s} />

          {content}

          <Pager page={page} pages={pages} base="/atlas" params={pagerParams(s)} />

          {list.ok && (filtered ? corpus.total > total : corpusTotal > atlasTotal) ? (
            <p className="atlas-note">
              {filtered ? (
                <>
                  По этим фильтрам в атлас вошли {fmtInt(total)} {pluralRu(total, "карточка", "карточки", "карточек")}, а всего
                  таких карточек {fmtInt(corpus.total)}.{" "}
                </>
              ) : (
                <>
                  В атлас вошли {fmtInt(atlasTotal)} {pluralRu(atlasTotal, "карточка", "карточки", "карточек")} из{" "}
                  {fmtInt(corpusTotal)}, собранных по книгам.{" "}
                </>
              )}
              В атлас попадают виды с фотографией, у которых проверены русское и латинское имя. Остальные карточки
              открываются через <Link href={searchHref}>поиск</Link>.
            </p>
          ) : null}
          {s.view !== "table" && list.items.length ? <PhotoSourcesNote /> : null}
        </div>
      </div>

      <Footer />
    </>
  );
}
