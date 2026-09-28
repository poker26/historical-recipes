// Кулинария и что приготовить, затем «с чем сочетают» по совместным рецептам.
import Link from "next/link";
import { Tile } from "../common";
import { RECIPE_KIND_MANY, RECIPE_KIND_ONE, excerpt, fmtInt, plantHref, pluralRu } from "../../lib/api";
import { displayName, recipeMeta, type FieldView, type Pairings, type PlantRecipes, type RecipeKind, type SpeciesCard } from "../../lib/api-plant";

const KIND_NONE: Record<string, string> = {
  medicinal: "Лечебных рецептов",
  food: "Кулинарных рецептов",
  cosmetic: "Косметических рецептов",
  other: "Других рецептов",
};
import { Block, FactList } from "./bits";

export function KitchenBlock({
  card, field, recipes, kind, base,
}: {
  card: SpeciesCard;
  field: FieldView | null;
  recipes: PlantRecipes | null;
  kind: RecipeKind | null;
  base: string;
}) {
  const items = recipes?.items ?? [];
  const kinds = (recipes?.kinds ?? []).filter((k) => RECIPE_KIND_MANY[k]);
  const total = Math.max(field?.recipes_total ?? 0, card.recipesRawTotal);
  const hasCulinary = card.culinary.total > 0;
  const hasRecipes = items.length > 0 || total > 0 || !!kind;
  if (!hasCulinary && !hasRecipes) return null;
  const withIt = card.kingdom === "гриб" ? "с этим грибом" : "с этим растением";
  return (
    <Block id="kitchen" title="Кулинария и что приготовить">
      {hasCulinary ? (
        <>
          <h3>Что пишут о еде</h3>
          <FactList set={card.culinary} />
        </>
      ) : null}
      {hasRecipes ? (
        <div className="pc-recipes-wrap">
          <h3>Что приготовить</h3>
          {kinds.length > 1 || kind ? (
            <nav className="chips pc-kinds" aria-label="Назначение рецепта">
              <Link href={`${base}#kitchen`} className={"chip " + (kind ? "chip-leaf" : "chip-lime")}>все</Link>
              {kinds.map((k) => (
                <Link key={k} href={`${base}?kind=${k}#kitchen`} className={"chip " + (kind === k ? "chip-lime" : "chip-leaf")}>
                  {RECIPE_KIND_MANY[k]}
                </Link>
              ))}
            </nav>
          ) : null}
          {items.length ? (
            <div className="pc-recipes">
              {items.map((r) => (
                <Link key={r.id} href={`/recipe/${r.id}`} className="card card-tight pc-recipe">
                  <div className="pc-recipe-head">
                    <b>{r.name}</b>
                    {r.kind && RECIPE_KIND_ONE[r.kind] ? <span className="chip chip-leaf">{RECIPE_KIND_ONE[r.kind]}</span> : null}
                  </div>
                  <div className="small muted">{recipeMeta(r)}</div>
                  {r.text ? <p className="small pc-recipe-text">{excerpt(r.text, 240)}</p> : null}
                </Link>
              ))}
            </div>
          ) : (
            <p className="muted">
              {kind
                ? `${KIND_NONE[kind] ?? "Таких рецептов"} ${withIt} в книгах пока не нашлось.`
                : `Домашних пошаговых рецептов ${withIt} в книгах пока не нашлось. Все рецепты, где его упоминают, открываются в каталоге.`}
            </p>
          )}
          {total > items.length ? (
            <p className="pc-all">
              <Link href={`/recipes?plant_id=${card.id}`} className="more">
                Все рецепты {withIt}, их {fmtInt(total)} →
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}
    </Block>
  );
}

export function PairingsBlock({ pairings }: { pairings: Pairings | null }) {
  const items = (pairings?.items ?? []).filter((p) => p.plant?.id && p.plant.name).slice(0, 8);
  if (!items.length) return null;
  return (
    <Block
      id="pairings"
      title="С чем сочетают"
      lead="Растения, которые чаще всего попадают с ним в один рецепт. Это частота совместных упоминаний в книгах, а не доказательство пользы сочетания."
    >
      <div className="tiles tiles-sm pc-pairs">
        {items.map((p) => {
          const tags: { label: string; warn?: boolean }[] = [];
          if (p.plant.safety_level === 4) tags.push({ label: "смертельно ядовито", warn: true });
          else if (p.plant.safety_level === 3) tags.push({ label: "ядовито в больших дозах", warn: true });
          if (p.specific) tags.push({ label: "характерная пара" });
          const proofs = (p.recipes ?? []).slice(0, 2);
          return (
            <div key={p.plant.id} className="pc-pair">
              <Tile
                href={plantHref(p.plant.id, p.plant.name_latin)}
                name={displayName(p.plant.name)}
                latin={p.plant.name_latin}
                photo={p.plant.photo_url}
                meta={`в ${fmtInt(p.support)} ${pluralRu(p.support, "рецепте", "рецептах", "рецептах")}`}
                tags={tags}
              />
              {proofs.length ? (
                <ul className="pc-pair-proof">
                  {proofs.map((r) => (
                    <li key={r.id}>
                      <Link href={`/recipe/${r.id}`}>{r.name}</Link>
                      {r.year ? `, ${r.year}` : ""}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
    </Block>
  );
}
