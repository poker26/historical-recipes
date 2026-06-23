"""Materialise recipe triage (recipe_triage.classify_recipe) onto `recipes`.

Adds recipe_kind / is_recipe / home_doable so «Что приготовить из этого растения» can
surface only the real, home-doable recipes and the showcase can demote the junk (industrial
procedures, monographs-as-recipe, fragments). Idempotent (recomputes). Run after editing
recipe_triage:
    docker compose exec -T backend python < backend/scripts/materialize_recipe_triage.py
"""
import asyncio
import uuid

from sqlalchemy import text

from app.database import async_session
from app.services.recipe_triage import classify_recipe


async def main():
    async with async_session() as db:
        for ddl in (
            "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS recipe_kind text",
            "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS is_recipe boolean",
            "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS home_doable boolean",
            "ALTER TABLE recipes ADD COLUMN IF NOT EXISTS procedure_score smallint",
            "CREATE INDEX IF NOT EXISTS ix_recipes_doable ON recipes(home_doable)",
        ):
            await db.execute(text(ddl))
        await db.commit()

        rows = (await db.execute(text(
            "SELECT r.id::text, r.name, r.category, r.original_text, "
            "       (SELECT count(*) FROM recipe_ingredients ri WHERE ri.recipe_id=r.id) k "
            "FROM recipes r"))).fetchall()
        await db.execute(text("CREATE TEMP TABLE rtmap(id uuid PRIMARY KEY, kind text, "
                              "is_rec boolean, home boolean, score smallint) ON COMMIT DROP"))
        buf = []

        async def flush():
            if not buf:
                return
            vals = ",".join(f"(:i{j},:k{j},:r{j},:h{j},:s{j})" for j in range(len(buf)))
            params = {}
            for j, (rid, c) in enumerate(buf):
                params[f"i{j}"] = rid
                params[f"k{j}"] = c["kind"]
                params[f"r{j}"] = c["is_recipe"]
                params[f"h{j}"] = c["home_doable"]
                params[f"s{j}"] = c["procedure_score"]
            await db.execute(text(f"INSERT INTO rtmap(id,kind,is_rec,home,score) VALUES {vals}"), params)
            buf.clear()

        for rid, nm, cat, txt, k in rows:
            buf.append((rid, classify_recipe(nm, cat, txt, k)))
            if len(buf) >= 500:
                await flush()
        await flush()
        await db.execute(text(
            "UPDATE recipes r SET recipe_kind=m.kind, is_recipe=m.is_rec, home_doable=m.home, "
            "procedure_score=m.score FROM rtmap m WHERE r.id=m.id"))
        await db.commit()

        dist = await db.execute(text(
            "SELECT recipe_kind, count(*), count(*) FILTER (WHERE home_doable) "
            "FROM recipes GROUP BY recipe_kind ORDER BY 2 DESC"))
        tot = (await db.execute(text("SELECT count(*) FILTER (WHERE home_doable), count(*) FROM recipes"))).first()
        print(f"home_doable {tot[0]}/{tot[1]} ({100*tot[0]//tot[1]}%)")
        for kind, n, h in dist.all():
            print(f"   {n:6} {kind} (home_doable {h})")


if __name__ == "__main__":
    asyncio.run(main())
