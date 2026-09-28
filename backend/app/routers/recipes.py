import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select, or_, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.recipe import Recipe, RecipeIngredient
from app.models.book import Book
from app.models.plant import Plant

router = APIRouter()


@router.get("/")
async def list_recipes(
    response: Response,
    category: str | None = None,
    book_id: uuid.UUID | None = None,
    domain: str | None = None,
    q: str | None = None,
    home_doable: bool | None = None,
    kind: str | None = None,
    step_by_step: bool | None = None,
    plant_id: uuid.UUID | None = None,
    sort: str | None = Query(None, pattern="^(new|name|year|quality)$"),
    brief: bool = False,
    limit: int | None = None,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """List recipes with search and filters.

    ``domain`` filters by the SOURCE book's domain (via the Book join), separating
    culinary recipes (``recipes``) from medicinal preparations harvested out of
    herbalism/fungi books (отвары/настои/сборы). Recipes carry no domain of their
    own — it is the book's — so this is the only way to split the two corpora.

    Site additions: ``step_by_step`` (procedure_score ≥ 2 — a real do-able recipe
    rather than a dosing note), ``plant_id`` (recipes whose ingredients resolved to
    a plant), ``sort`` (new = default created_at desc; name; year; quality =
    procedure_score desc) and ``brief`` (omit the long texts, keep a 220-char excerpt).

    Pagination: pass ``limit``/``offset`` to fetch one page; the full filtered
    count is always returned in the ``X-Total-Count`` header. Omitting ``limit``
    returns every match (the historical behaviour the MCP tools rely on).
    """
    stmt = select(Recipe, Book.title, Book.author, Book.year, Book.domain).join(
        Book, Recipe.book_id == Book.id
    )

    if category:
        stmt = stmt.where(Recipe.category == category)
    if book_id:
        stmt = stmt.where(Recipe.book_id == book_id)
    if domain:
        stmt = stmt.where(Book.domain == domain.strip())
    if home_doable is not None:
        stmt = stmt.where(Recipe.home_doable.is_(home_doable))   # real, do-able recipes (vs junk)
    if kind:
        stmt = stmt.where(Recipe.recipe_kind == kind.strip())
    if step_by_step is not None:
        pred = func.coalesce(Recipe.procedure_score, 0) >= 2
        stmt = stmt.where(pred if step_by_step else ~pred)
    if plant_id:
        stmt = stmt.where(
            Recipe.id.in_(select(RecipeIngredient.recipe_id).where(RecipeIngredient.plant_id == plant_id))
        )
    if q:
        # Two matchers OR'd together:
        #  1. ILIKE substring on name + original_text — the historical behaviour
        #     (prefixes, partial words, content matches).
        #  2. Russian stemmed full-text on the NAME — tolerant to declension and
        #     word order, so «Ночные стражи» (nominative) still finds «Водка ночных
        #     стражей» (genitive). Name-only keeps it cheap (no scan over the long
        #     original_text). Fixes named-recipe recall when the query's grammar
        #     differs from the stored title.
        pattern = f"%{q}%"
        name_tsv = func.to_tsvector("russian", func.coalesce(Recipe.name, ""))
        stmt = stmt.where(
            or_(
                Recipe.name.ilike(pattern),
                Recipe.original_text.ilike(pattern),
                name_tsv.op("@@")(func.plainto_tsquery("russian", q)),
            )
        )

    # Total matching rows (before pagination) → header, so the UI can render
    # "Page X of Y" without a second request.
    total = (await db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )).scalar() or 0
    response.headers["X-Total-Count"] = str(total)

    if sort == "name":
        stmt = stmt.order_by(Recipe.name.nulls_last(), Recipe.created_at.desc())
    elif sort == "year":
        stmt = stmt.order_by(Book.year.nulls_last(), Recipe.name)
    elif sort == "quality":
        stmt = stmt.order_by(func.coalesce(Recipe.procedure_score, 0).desc(), Recipe.home_doable.desc(), Recipe.name)
    else:
        stmt = stmt.order_by(Recipe.created_at.desc())
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.all()

    def excerpt(s: str | None, n: int = 220) -> str | None:
        if not s:
            return None
        t = " ".join(s.split())
        return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"

    return [
        {
            "id": str(r.id),
            "book_id": str(r.book_id),
            "book_title": book_title,
            "book_author": book_author,
            "book_year": book_year,
            "book_domain": book_domain,
            "name": r.name,
            "category": r.category,
            "recipe_kind": r.recipe_kind,
            "home_doable": r.home_doable,
            "step_by_step": (r.procedure_score or 0) >= 2,
            **({"excerpt": excerpt(r.normalized_text or r.original_text)} if brief else
               {"original_text": r.original_text, "normalized_text": r.normalized_text}),
            "year": r.year,
            "indexed_at": r.indexed_at.isoformat() if r.indexed_at else None,
        }
        for (r, book_title, book_author, book_year, book_domain) in rows
    ]


@router.get("/categories")
async def recipe_categories(home_doable: bool | None = True, db: AsyncSession = Depends(get_db)):
    """Словарь для фильтров каталога рецептов: формы (category), виды (recipe_kind),
    домены книг, годы. Счётчики по домашним рецептам по умолчанию."""
    where = "WHERE r.home_doable" if home_doable else ("WHERE NOT r.home_doable" if home_doable is False else "")
    cats = (await db.execute(text(f"""
        SELECT r.category, count(*) FROM recipes r {where} GROUP BY 1 HAVING r.category IS NOT NULL ORDER BY 2 DESC"""))).all()
    kinds = (await db.execute(text(f"""
        SELECT r.recipe_kind, count(*) FROM recipes r {where} GROUP BY 1 ORDER BY 2 DESC"""))).all()
    domains = (await db.execute(text(f"""
        SELECT b.domain, count(*) FROM recipes r JOIN books b ON b.id = r.book_id {where} GROUP BY 1 ORDER BY 2 DESC"""))).all()
    eras = (await db.execute(text(f"""
        SELECT CASE WHEN b.year IS NULL THEN 'unknown' WHEN b.year <= 1917 THEN 'pre1917'
                    WHEN b.year <= 1991 THEN 'soviet' ELSE 'modern' END AS era, count(*)
        FROM recipes r JOIN books b ON b.id = r.book_id {where} GROUP BY 1 ORDER BY 2 DESC"""))).all()
    steps = (await db.execute(text(f"""
        SELECT count(*) FILTER (WHERE coalesce(r.procedure_score,0) >= 2), count(*) FROM recipes r {where}"""))).first()
    return {
        "categories": [{"value": c, "count": n} for c, n in cats],
        "kinds": [{"value": k, "count": n} for k, n in kinds if k],
        "domains": [{"value": d, "count": n} for d, n in domains],
        "eras": [{"value": e, "count": n} for e, n in eras],
        "step_by_step": steps[0], "total": steps[1],
    }


@router.get("/sitemap")
async def recipes_sitemap(offset: int = 0, limit: int = Query(5000, ge=1, le=20000),
                          min_text: int = Query(0, ge=0, le=5000), db: AsyncSession = Depends(get_db)):
    """Рецепты для карты сайта: домашние, пошаговые, с именем. ``min_text`` отсекает
    короткие записи: у них нет своего содержания, и сайт ставит им noindex."""
    where = ("home_doable AND coalesce(procedure_score, 0) >= 2 AND name IS NOT NULL "
             "AND length(coalesce(original_text, '')) >= :mt")
    rows = (await db.execute(text(f"""
        SELECT id, name, category, created_at FROM recipes WHERE {where}
        ORDER BY created_at, id LIMIT :lim OFFSET :off"""), {"lim": limit, "off": offset, "mt": min_text})).all()
    total = (await db.execute(text(f"SELECT count(*) FROM recipes WHERE {where}"), {"mt": min_text})).scalar()
    return {"total": total, "items": [
        {"id": str(i), "name": n, "category": c, "created_at": t.isoformat() if t else None}
        for i, n, c, t in rows]}


@router.get("/{recipe_id}")
async def get_recipe(recipe_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get recipe details with ingredients."""
    result = await db.execute(
        select(Recipe)
        .options(selectinload(Recipe.ingredients))
        .where(Recipe.id == recipe_id)
    )
    recipe = result.scalar_one_or_none()
    if not recipe:
        raise HTTPException(status_code=404, detail="Recipe not found")

    book = (
        await db.execute(select(Book).where(Book.id == recipe.book_id))
    ).scalar_one_or_none()

    # Resolve plant names for ingredients linked to the herbarium, so the recipe
    # page can render each as an active link to the plant monograph.
    plant_ids = {ing.plant_id for ing in recipe.ingredients if ing.plant_id}
    plants: dict[uuid.UUID, tuple] = {}
    if plant_ids:
        rows = (await db.execute(
            select(Plant.id, Plant.name, Plant.name_latin, Plant.photo_url, Plant.safety_level, Plant.is_toxic)
            .where(Plant.id.in_(plant_ids))
        )).all()
        plants = {r[0]: r for r in rows}

    # Страница источника (привязка фактов к страницам, миграция 035) лежит вне ORM.
    anchor = (await db.execute(text(
        "SELECT source_page, anchor_method FROM recipes WHERE id = :id"), {"id": recipe_id})).first()

    return {
        "id": str(recipe.id),
        "book_id": str(recipe.book_id),
        "book_title": book.title if book else None,
        "book_author": book.author if book else None,
        "book_year": book.year if book else None,
        "book_domain": book.domain if book else None,
        "book_language": book.language if book else None,
        "name": recipe.name,
        "category": recipe.category,
        "recipe_kind": recipe.recipe_kind,
        "home_doable": recipe.home_doable,
        "step_by_step": (recipe.procedure_score or 0) >= 2,
        "source_page": anchor.source_page if anchor else None,
        "anchor_method": anchor.anchor_method if anchor else None,
        "original_text": recipe.original_text,
        "normalized_text": recipe.normalized_text,
        "year": recipe.year,
        "indexed_at": recipe.indexed_at.isoformat() if recipe.indexed_at else None,
        "ingredients": [
            {
                "id": str(ing.id),
                "name": ing.name,
                "original_name": ing.original_name,
                "amount": ing.amount,
                "unit": ing.unit,
                "amount_modern": ing.amount_modern,
                "unit_modern": ing.unit_modern,
                "plant_id": str(ing.plant_id) if ing.plant_id else None,
                "plant_name": plants[ing.plant_id][1] if ing.plant_id in plants else None,
                "plant_latin": plants[ing.plant_id][2] if ing.plant_id in plants else None,
                "plant_photo": plants[ing.plant_id][3] if ing.plant_id in plants else None,
                "plant_safety_level": plants[ing.plant_id][4] if ing.plant_id in plants else None,
                "plant_is_toxic": plants[ing.plant_id][5] if ing.plant_id in plants else None,
            }
            for ing in recipe.ingredients
        ],
    }
