import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, Quote, Safety, SectionHead, SourceRef } from "../../../components/common";
import { excerpt, isUuid, SITE_URL } from "../../../lib/api";
import {
  KIND_CHIP_RU, amountText, getRecipe, getSimilarByCategory, getSimilarByPlant, inSentence,
  recipeTitle, recipeYear, refAuthor, worstIngredient, type Ingredient, type RecipeDetail,
} from "../../../lib/api-recipes";
import { IngredientsTable } from "../../../components/recipes/IngredientsTable";
import { RecipeMini } from "../../../components/recipes/RecipeRow";
import { pageMeta } from "../../../components/reference/meta";
import "../../recipes/recipes.css";

export const dynamic = "force-dynamic";

type Params = { params: { id: string } };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) return { title: "Рецепт не найден", robots: { index: false } };
  const { data: r } = await getRecipe(id);
  if (!r) return { title: "Рецепт", robots: { index: false } };
  const year = recipeYear(r);
  return pageMeta({
    title: `${recipeTitle(r.name)} · рецепт из «${r.book_title || "книги"}»${year ? `, ${year}` : ""}`,
    description: excerpt(r.normalized_text || r.original_text, 160) || `Рецепт из книги «${r.book_title || "без названия"}» с дословным текстом и ингредиентами.`,
    path: `/recipe/${r.id}`,
    // В индекс идут только пошаговые домашние рецепты; заметки о дозах открываются по ссылке.
    index: !!(r.step_by_step && r.home_doable),
  });
}

function ingredientLine(i: Ingredient): string {
  const a = amountText(i);
  const name = (i.name || i.original_name || "").trim();
  return [name, a.book].filter(Boolean).join(", ");
}

function recipeJsonLd(r: RecipeDetail, title: string, year: number | null) {
  return {
    "@context": "https://schema.org",
    "@type": "Recipe",
    name: title,
    url: `${SITE_URL}/recipe/${r.id}`,
    inLanguage: "ru",
    recipeCategory: r.category || undefined,
    recipeIngredient: r.ingredients.map(ingredientLine).filter(Boolean),
    isBasedOn: r.book_title
      ? {
          "@type": "Book",
          name: r.book_title,
          ...(r.book_author ? { author: { "@type": "Person", name: r.book_author } } : {}),
          ...(year ? { datePublished: String(year) } : {}),
        }
      : undefined,
  };
}

export default async function RecipePage({ params }: Params) {
  const id = params.id.toLowerCase();
  if (!isUuid(id)) notFound();
  const { data: r, missing } = await getRecipe(id);
  if (missing) notFound();
  if (!r) {
    return (
      <>
        <Header active="/recipes" />
        <Crumbs items={[{ href: "/recipes", label: "Рецепты" }, { label: "Рецепт" }]} />
        <Empty>
          Рецепт сейчас не загрузился. Обнови страницу через минуту или вернись в <Link href="/recipes">каталог рецептов</Link>.
        </Empty>
        <Footer />
      </>
    );
  }

  const title = recipeTitle(r.name);
  const year = recipeYear(r);
  const linked = r.ingredients.filter((i) => i.plant_id);
  const main = linked[0] ?? null;
  const [byPlant, byCat] = await Promise.all([
    main?.plant_id ? getSimilarByPlant(main.plant_id) : Promise.resolve(null),
    r.category ? getSimilarByCategory(r.category) : Promise.resolve(null),
  ]);
  const plantSimilar = (byPlant ?? []).filter((x) => x.id !== r.id).slice(0, 6);
  const shown = new Set(plantSimilar.map((x) => x.id));
  const catSimilar = (byCat ?? []).filter((x) => x.id !== r.id && !shown.has(x.id)).slice(0, 6);

  const worst = worstIngredient(r.ingredients);
  const original = (r.original_text || "").trim();
  const modern = (r.normalized_text || "").trim();
  const showModern = !!modern && modern !== original;
  const kind = r.recipe_kind ? KIND_CHIP_RU[r.recipe_kind] ?? r.recipe_kind : null;
  const source = { book: r.book_title, author: refAuthor(r.book_author), year, bookId: r.book_id, page: r.source_page };
  const mainName = main ? inSentence(main.plant_name || main.name || "") : "";

  return (
    <>
      <Header active="/recipes" />
      {r.step_by_step ? (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(recipeJsonLd(r, title, year)).replace(/</g, "\\u003c") }}
        />
      ) : null}

      <Crumbs
        items={[
          { href: "/recipes", label: "Рецепты" },
          ...(r.category ? [{ href: `/recipes?category=${encodeURIComponent(r.category)}`, label: r.category.charAt(0).toUpperCase() + r.category.slice(1) }] : []),
          { label: title },
        ]}
      />

      <section className="rc-head">
        <div className="chips">
          {kind ? <Link href={`/recipes?kind=${r.recipe_kind}`} className="chip chip-leaf">{kind}</Link> : null}
          {r.category ? <Link href={`/recipes?category=${encodeURIComponent(r.category)}`} className="chip chip-mist">{r.category}</Link> : null}
          {r.step_by_step ? <span className="chip chip-lime">пошаговый</span> : null}
          {r.book_language === "pre_reform_ru" ? <span className="chip chip-mist">дореформенная орфография</span> : null}
        </div>
        <h1>{title}</h1>
        <SourceRef {...source} />
      </section>

      {worst ? (
        <div className="rc-safety">
          {worst.level === 4 ? (
            <Safety
              level="danger"
              title={`${worst.names.length > 1 ? "В рецепте смертельно ядовитые ингредиенты" : "В рецепте смертельно ядовитый ингредиент"}: ${worst.names.join(", ")}`}
              text="Такие рецепты остались в книгах как история, повторять их дома опасно для жизни."
            />
          ) : (
            <Safety
              level="warn"
              title={`${worst.names.length > 1 ? "В рецепте есть ядовитые растения" : "В рецепте есть ядовитое растение"}: ${worst.names.join(", ")}, дозы старых книг проверяй`}
            />
          )}
        </div>
      ) : null}

      <section className="block" id="ingredients">
        <h2>Ингредиенты</h2>
        {r.ingredients.length ? (
          <>
            <IngredientsTable items={r.ingredients} />
            {!linked.length ? (
              <p className="small muted rc-note">
                Растения этого рецепта ещё не сверены с атласом, поэтому ссылок на карточки пока нет.
              </p>
            ) : null}
          </>
        ) : (
          <p className="muted">Ингредиенты этого рецепта ещё не разобраны по строкам, их видно в тексте ниже.</p>
        )}
      </section>

      <section className="block rc-text" id="text">
        <h2>Как написано в книге</h2>
        {original ? (
          <Quote text={original} source={source} />
        ) : modern ? (
          <Quote text={modern} source={source} />
        ) : (
          <p className="muted">Текст рецепта не сохранился, осталась только ссылка на книгу.</p>
        )}
        {original && showModern ? (
          <details className="rc-modern">
            <summary>Современным языком</summary>
            <div className="prose">{modern}</div>
          </details>
        ) : null}
      </section>

      {plantSimilar.length && main?.plant_id ? (
        <section className="section">
          <SectionHead title={`Ещё рецепты с растением ${mainName}`} href={`/recipes?plant_id=${main.plant_id}`} more="все" />
          <div className="rc-similar">
            {plantSimilar.map((x) => <RecipeMini key={x.id} r={x} />)}
          </div>
        </section>
      ) : null}

      {catSimilar.length && r.category ? (
        <section className="section">
          <SectionHead title={`Другие рецепты из раздела «${r.category}»`} href={`/recipes?category=${encodeURIComponent(r.category)}`} more="все" />
          <div className="rc-similar">
            {catSimilar.map((x) => <RecipeMini key={x.id} r={x} />)}
          </div>
        </section>
      ) : null}

      <p className="footnote rc-foot">
        Это рецепт из книги, а не медицинский совет: дозы и безопасность проверяй по современным источникам.
      </p>

      <Footer />
    </>
  );
}
