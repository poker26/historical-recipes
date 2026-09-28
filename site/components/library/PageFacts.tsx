// Что атлас взял со страницы книги: растения с их фактами и рецепты.
// Для открытых книг у каждого найденного в тексте фрагмента есть ссылка «показать на
// странице» (?hl=id#hl); для цитируемых книг это и есть всё, что мы показываем.
import Link from "next/link";
import { Empty, LeafGlyph, Quote } from "../common";
import { excerpt, fmtInt, plantHref, pluralRu } from "../../lib/api";
import { FACT_KIND_RU, recipeMeta, type BookPage, type FactKind, type PageFact } from "../../lib/api-library";

const KIND_ORDER: FactKind[] = ["use", "culinary", "harvest", "habitat", "toxicity", "mention"];
const KIND_CHIP: Partial<Record<FactKind, string>> = { toxicity: "chip-danger", mention: "chip-mist" };

function factDetail(f: PageFact): string {
  const part = Array.isArray(f.part) ? f.part.filter(Boolean).join(", ") : f.part;
  if (f.kind === "mention") return f.label ? `в книге названо «${f.label}»` : "";
  return [f.label, part].filter(Boolean).join(" · ");
}

export function PageFacts({ data, hl, found, hlInText }: { data: BookPage; hl?: string; found: Set<string>; hlInText: boolean }) {
  const bookId = data.book.id;
  const n = data.page.number;
  const open = data.book.access === "open";
  const showHref = (id: string) => `/library/${bookId}/p/${n}?hl=${encodeURIComponent(id)}#hl`;
  // Якорь #hl один на страницу: на подсветке в тексте, а если фрагмент в тексте
  // не нашёлся (или текст не показываем), то на самом факте в списке.
  const anchorId = (id: string) => (hl === id && !hlInText ? "hl" : undefined);

  const byPlant = new Map<string, PageFact[]>();
  for (const f of data.facts) {
    const arr = byPlant.get(f.plant_id);
    if (arr) arr.push(f);
    else byPlant.set(f.plant_id, [f]);
  }
  byPlant.forEach((arr) => arr.sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind)));

  if (!data.facts.length && !data.recipes.length) {
    return (
      <Empty>
        С этой страницы в атлас пока не вошло ни одной цитаты. <Link href={`/library/${bookId}`}>Вернись к книге</Link> или
        открой соседнюю страницу.
      </Empty>
    );
  }

  const summary = [
    data.plants.length ? `${fmtInt(data.plants.length)} ${pluralRu(data.plants.length, "растение", "растения", "растений")}` : null,
    data.facts.length ? `${fmtInt(data.facts.length)} ${pluralRu(data.facts.length, "цитата", "цитаты", "цитат")}` : null,
    data.recipes.length ? `${fmtInt(data.recipes.length)} ${pluralRu(data.recipes.length, "рецепт", "рецепта", "рецептов")}` : null,
  ].filter(Boolean);

  return (
    <div className="lib-onpage">
      <p className="section-lead">
        С этой страницы в атлас вошли {summary.length > 1 ? summary.slice(0, -1).join(", ") + " и " + summary[summary.length - 1] : summary[0]}.
        {open ? " Нажми «показать на странице», чтобы увидеть фрагмент в тексте." : ""}
      </p>

      {data.plants.map((p) => (
        <div key={p.id} className="lib-plant">
          <Link href={plantHref(p.id, p.name_latin)} className="lib-plant-row">
            <span className="lib-plant-photo">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {p.photo_url ? <img src={p.photo_url} alt="" loading="lazy" /> : <LeafGlyph />}
            </span>
            <span className="lib-plant-names">
              <b>{p.name}</b>
              {p.name_latin ? <span className="latin small">{p.name_latin}</span> : null}
            </span>
            <span className="lib-plant-n">{fmtInt(p.facts)} {pluralRu(p.facts, "цитата", "цитаты", "цитат")}</span>
          </Link>
          <ul className="lib-facts">
            {(byPlant.get(p.id) ?? []).map((f) => {
              const detail = factDetail(f);
              return (
                <li key={f.id} id={anchorId(f.id)} className={"lib-fact" + (hl === f.id ? " is-hl" : "")}>
                  <div className="lib-fact-head">
                    <span className={"chip " + (KIND_CHIP[f.kind] ?? "chip-leaf")}>{FACT_KIND_RU[f.kind] ?? f.kind}</span>
                    {detail ? <span className="small muted">{detail}</span> : null}
                    {open && found.has(f.id) ? <Link href={showHref(f.id)} className="lib-show">показать на странице</Link> : null}
                  </div>
                  {f.original_text ? <Quote text={f.original_text} /> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ))}

      {data.recipes.length ? (
        <div className="lib-onpage-recipes">
          <h3>Рецепты с этой страницы</h3>
          <ul className="lib-list">
            {data.recipes.map((r) => (
              <li key={r.id} id={anchorId(r.id)} className={"lib-row" + (hl === r.id ? " is-hl" : "")}>
                <Link href={`/recipe/${r.id}`} className="lib-row-name">{r.name || "Рецепт без названия"}</Link>
                <span className="lib-row-meta">
                  {recipeMeta(r.category, r.recipe_kind)}
                </span>
                {open && found.has(r.id) ? <Link href={showHref(r.id)} className="lib-show">показать на странице</Link> : null}
                {!open && r.original_text ? (
                  <div className="lib-row-quote"><Quote text={excerpt(r.original_text, 320)} /></div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
