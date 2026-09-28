import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Header, Footer } from "../../ui";
import { Crumbs, Empty, Quote, Safety, SectionHead, SourceRef } from "../../../components/common";
import { DEFAULT_OG, excerpt, isUuid, realAuthor, SITE_URL } from "../../../lib/api";
import { largePhoto } from "../../../lib/api-plant";
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
  const name = recipeTitle(r.name);
  return pageMeta({
    title: year ? `${name}, рецепт ${year} года` : `${name}, рецепт из книги «${excerpt(r.book_title || "без названия", 40)}»`,
    description: recipeDescription(r, name, year),
    path: `/recipe/${r.id}`,
    // В индекс идут пошаговые домашние рецепты с текстом от 200 знаков. Заметки о дозах и
    // короткие записи открываются по ссылке. Карта сайта отбирает рецепты по тому же
    // правилу (/recipes/sitemap?min_text=200).
    index: !!(r.step_by_step && r.home_doable) && (r.original_text ?? "").length >= 200,
  });
}

/** Описание для поисковика: начало рецепта без номера и повтора названия, потом источник. */
function recipeDescription(r: RecipeDetail, name: string, year: number | null): string {
  let body = (r.normalized_text || r.original_text || "").replace(/\s+/g, " ").trim();
  body = body.replace(/^(№\s*)?\d{1,4}\s*[.)]\s*/, "");
  if (name && body.toLowerCase().startsWith(name.toLowerCase())) {
    body = body.slice(name.length).replace(/^[\s.,:;!?)(—–-]+/, "");
  }
  const book = r.book_title ? `Рецепт из книги «${excerpt(r.book_title, 50)}»${year ? `, ${year}` : ""}.` : "";
  if (!body) return book || "Рецепт с дословным текстом из старой книги и ингредиентами.";
  return [excerpt(body, Math.max(70, 158 - book.length)), book].filter(Boolean).join(" ");
}

/** Шаги для разметки Recipe: текст по предложениям, короткие обрывки не считаются шагом. */
function recipeSteps(text: string): { "@type": "HowToStep"; text: string }[] {
  const parts = text
    .split(/\n+|(?<=[.!?])\s+(?=[А-ЯЁA-Z])/)
    .map((s) => s.replace(/\s+/g, " ").trim())
    .filter((s) => s.length >= 12);
  return (parts.length ? parts : [text.trim()]).slice(0, 20).map((t) => ({ "@type": "HowToStep", text: t }));
}

function ingredientLine(i: Ingredient): string {
  const a = amountText(i);
  const name = (i.name || i.original_name || "").trim();
  return [name, a.book].filter(Boolean).join(", ");
}

function recipeJsonLd(r: RecipeDetail, title: string, year: number | null) {
  // Картинка обязательна для расширенного сниппета рецепта. Фото блюда в старых книгах нет,
  // поэтому берём фото растения из ингредиентов, а без него общую картинку сайта.
  const photo = r.ingredients.find((i) => i.plant_photo)?.plant_photo;
  const text = (r.normalized_text || r.original_text || "").trim();
  return {
    "@context": "https://schema.org",
    "@type": "Recipe",
    name: title,
    url: `${SITE_URL}/recipe/${r.id}`,
    inLanguage: "ru",
    image: [photo ? largePhoto(photo) : DEFAULT_OG.url],
    description: recipeDescription(r, title, year),
    ...(realAuthor(r.book_author) ? { author: { "@type": "Person", name: r.book_author } } : {}),
    ...(year ? { datePublished: String(year) } : {}),
    recipeCategory: r.category || undefined,
    recipeIngredient: r.ingredients.map(ingredientLine).filter(Boolean),
    recipeInstructions: text ? recipeSteps(text) : undefined,
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
  const kind = r.recipe_kind ? KIND_CHIP_RU[r.recipe_kind] ?? null : null;
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
              title={worst.names.length > 1 ? "В рецепте смертельно ядовитые ингредиенты" : "В рецепте смертельно ядовитый ингредиент"}
              text={`Это ${worst.names.join(", ")}. Такие рецепты остались в книгах как история, повторять их дома опасно для жизни.`}
            />
          ) : (
            <Safety
              level="warn"
              title={worst.names.length > 1 ? "В рецепте есть ядовитые растения" : "В рецепте есть ядовитое растение"}
              text={`Это ${worst.names.join(", ")}. Дозы в старых книгах бывают опасными, сверяй их с современными справочниками.`}
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
                Растения этого рецепта ещё не связаны с карточками атласа, поэтому ссылок на них пока нет.
              </p>
            ) : null}
          </>
        ) : (
          <p className="muted">Ингредиенты этого рецепта отдельно пока не выписаны, их можно прочитать в тексте рецепта.</p>
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
          <SectionHead title={`Ещё рецепты с растением «${mainName}»`} href={`/recipes?plant_id=${main.plant_id}`} more="все" />
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
        Это рецепт из книги, а не медицинский совет. Дозы и безопасность проверяй по современным справочникам.
      </p>

      <Footer />
    </>
  );
}
