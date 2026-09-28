// Что атлас взял со страницы книги: растения с их цитатами и рецепты. У открытой книги
// список стоит рядом со сканом, у цитируемой это всё содержание страницы. Ссылка из
// карточки растения (?hl=id#hl) выделяет в списке свою цитату.
import Link from "next/link";
import { Empty, LeafGlyph, Quote } from "../common";
import { excerpt, fmtInt, plantHref, pluralRu } from "../../lib/api";
import { FACT_KIND_RU, recipeMeta, type BookPage, type FactKind, type PageFact } from "../../lib/api-library";
import { canonParts } from "../../lib/api-plant";

const KIND_ORDER: FactKind[] = ["use", "culinary", "harvest", "habitat", "toxicity", "mention"];
const KIND_CHIP: Partial<Record<FactKind, string>> = { toxicity: "chip-danger", mention: "chip-mist" };

/** «ранозаживляющее (кора)»: в подписи бывают свои запятые, поэтому часть растения в скобках. */
function factDetail(f: PageFact): string {
  const raw = Array.isArray(f.part) ? f.part.filter(Boolean).join(", ") : f.part;
  const part = Array.from(new Set(canonParts(raw))).join(", ") || raw || "";
  if (f.kind === "mention") return f.label ? `в книге названо «${f.label}»` : "";
  if (f.label && part) return `${f.label} (${part})`;
  return f.label || part;
}

export function PageFacts({ data, hl }: { data: BookPage; hl?: string }) {
  const bookId = data.book.id;
  // Якорь #hl один на страницу, на выделенной цитате или рецепте.
  const anchorId = (id: string) => (hl === id ? "hl" : undefined);

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
                {r.original_text ? (
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
