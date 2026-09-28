// Строка рецепта в каталоге и маленькая карточка «похожего рецепта».
// Карточка не одна ссылка: внутри ссылки на рецепт, на форму и на книгу.
import Link from "next/link";
import { SourceRef } from "../common";
import { excerpt } from "../../lib/api";
import { KIND_CHIP_RU, recipeTitle, recipeYear, refAuthor, type RecipeBrief } from "../../lib/api-recipes";

export function RecipeRow({ r }: { r: RecipeBrief }) {
  const kind = r.recipe_kind ? KIND_CHIP_RU[r.recipe_kind] ?? r.recipe_kind : null;
  return (
    <article className="card card-tight rc-row">
      <h3 className="rc-row-title">
        <Link href={`/recipe/${r.id}`}>{recipeTitle(r.name)}</Link>
      </h3>
      <div className="chips rc-row-chips">
        {kind ? <Link href={`/recipes?kind=${r.recipe_kind}`} className="chip chip-leaf">{kind}</Link> : null}
        {r.category ? <Link href={`/recipes?category=${encodeURIComponent(r.category)}`} className="chip chip-mist">{r.category}</Link> : null}
        {r.step_by_step ? <span className="chip chip-lime">пошаговый</span> : null}
      </div>
      {r.excerpt ? <p className="rc-row-excerpt">{excerpt(r.excerpt, 220)}</p> : null}
      <SourceRef book={r.book_title} author={refAuthor(r.book_author)} year={recipeYear(r)} bookId={r.book_id} />
    </article>
  );
}

/** Карточка для блоков «похожие рецепты»: имя, форма и книга, без выдержки. */
export function RecipeMini({ r }: { r: RecipeBrief }) {
  const year = recipeYear(r);
  return (
    <Link href={`/recipe/${r.id}`} className="card card-tight rc-mini">
      <b>{recipeTitle(r.name)}</b>
      <span className="small muted">
        {[r.category, r.book_title ? `«${r.book_title}»${year ? `, ${year}` : ""}` : null].filter(Boolean).join(", ")}
      </span>
    </Link>
  );
}
