"""Recompute the `plant_pairings` table — the «сочетаемость» (co-occurrence) engine.

The flagship paid-segment feature: which plants historically pair with which, mined from
the recipe corpus, sliced by recipe form (настойка/отвар/чай/…), with evidence recipes.
GROUNDED — every pairing is real co-occurrence in digitised recipes, not LLM invention.

Метрика: support (в скольких рецептах вместе) + lift (support·N / count_a·count_b — во
сколько раз чаще случайного). Выдача ранжируется `support·ln(lift)` (общие «друзья»
сверху, специфичные — с флагом). Роллап вид→род в расчёте консолидирует сигнал.

Run (idempotent — TRUNCATEs and rebuilds; ~4s on the full corpus):
    docker compose exec -T backend python < backend/scripts/build_pairings.py
Or wrap in a Temporal activity for refresh-on-corpus-change.
"""
import asyncio
import time

from sqlalchemy import text

from app.database import async_session

MIN_ALL = 4      # support gate, overall pairs
MIN_CAT = 3      # support gate, per-category pairs
MAX_BASKET = 20  # ignore giant сборы (k^2 blow-up + noise)
NOISE_CATEGORIES = ("другое",)

DDL = [
    """CREATE TABLE IF NOT EXISTS plant_pairings(
         plant_a uuid, plant_b uuid, category text NOT NULL,
         support int, lift real, conf_ab real, sample_recipe_ids uuid[],
         PRIMARY KEY (plant_a, plant_b, category))""",
    "CREATE INDEX IF NOT EXISTS ix_pair_a ON plant_pairings(plant_a, category, lift DESC)",
]


async def main():
    async with async_session() as db:
        t0 = time.time()
        for d in DDL:
            await db.execute(text(d))
        await db.execute(text("TRUNCATE plant_pairings"))

        # Canonical ingredient set: a species with a genus parent rolls up to the genus
        # hub (consolidates the signal split across Дягиль/Дудник-style identity rows).
        await db.execute(text("""CREATE TEMP TABLE ci ON COMMIT DROP AS
          SELECT DISTINCT ri.recipe_id,
                 CASE WHEN par.rank='genus' THEN par.id ELSE p.id END AS cid,
                 r.category
          FROM recipe_ingredients ri
          JOIN plants p ON p.id=ri.plant_id AND p.kingdom IN ('растение','гриб')
          LEFT JOIN plants par ON par.id=p.parent_id
          JOIN recipes r ON r.id=ri.recipe_id"""))
        await db.execute(text("CREATE INDEX ix_ci ON ci(recipe_id)"))
        await db.execute(text(f"""DELETE FROM ci WHERE recipe_id IN (
          SELECT recipe_id FROM ci GROUP BY recipe_id HAVING count(*)>{MAX_BASKET})"""))
        N = (await db.execute(text("SELECT count(DISTINCT recipe_id) FROM ci"))).scalar()
        await db.execute(text("CREATE TEMP TABLE cnt ON COMMIT DROP AS "
                              "SELECT cid, count(DISTINCT recipe_id) n FROM ci GROUP BY cid"))
        await db.execute(text("CREATE INDEX ix_cnt ON cnt(cid)"))

        # Overall pairs (category sentinel '__all__')
        await db.execute(text(f"""INSERT INTO plant_pairings
            (plant_a,plant_b,category,support,lift,conf_ab,sample_recipe_ids)
          SELECT a.cid, b.cid, '__all__', count(DISTINCT a.recipe_id), 0, 0,
                 (array_agg(DISTINCT a.recipe_id))[1:5]
          FROM ci a JOIN ci b ON b.recipe_id=a.recipe_id AND b.cid<>a.cid
          GROUP BY a.cid,b.cid HAVING count(DISTINCT a.recipe_id)>={MIN_ALL}"""))
        await db.execute(text(f"""UPDATE plant_pairings pp SET
            lift=(pp.support::real*{N})/(ca.n*cb.n), conf_ab=pp.support::real/ca.n
          FROM cnt ca, cnt cb
          WHERE pp.category='__all__' AND ca.cid=pp.plant_a AND cb.cid=pp.plant_b"""))

        # Per-category pairs (skip noise categories)
        noise = ",".join(f"'{c}'" for c in NOISE_CATEGORIES)
        await db.execute(text(f"CREATE TEMP TABLE cntc ON COMMIT DROP AS "
            f"SELECT cid,category,count(DISTINCT recipe_id) n FROM ci "
            f"WHERE category IS NOT NULL AND category NOT IN ({noise}) GROUP BY cid,category"))
        await db.execute(text("CREATE INDEX ix_cntc ON cntc(cid,category)"))
        await db.execute(text(f"CREATE TEMP TABLE ncat ON COMMIT DROP AS "
            f"SELECT category, count(DISTINCT recipe_id) n FROM ci "
            f"WHERE category IS NOT NULL AND category NOT IN ({noise}) GROUP BY category"))
        await db.execute(text(f"""INSERT INTO plant_pairings
            (plant_a,plant_b,category,support,lift,conf_ab,sample_recipe_ids)
          SELECT a.cid, b.cid, a.category, count(DISTINCT a.recipe_id), 0, 0,
                 (array_agg(DISTINCT a.recipe_id))[1:5]
          FROM ci a JOIN ci b ON b.recipe_id=a.recipe_id AND b.cid<>a.cid
          WHERE a.category IS NOT NULL AND a.category NOT IN ({noise})
          GROUP BY a.cid,b.cid,a.category HAVING count(DISTINCT a.recipe_id)>={MIN_CAT}"""))
        await db.execute(text("""UPDATE plant_pairings pp SET
            lift=(pp.support::real*nc.n)/(ca.n*cb.n), conf_ab=pp.support::real/ca.n
          FROM cntc ca, cntc cb, ncat nc
          WHERE pp.category<>'__all__' AND ca.cid=pp.plant_a AND ca.category=pp.category
            AND cb.cid=pp.plant_b AND cb.category=pp.category AND nc.category=pp.category"""))
        await db.commit()

        total = (await db.execute(text("SELECT count(*) FROM plant_pairings"))).scalar()
        cov = (await db.execute(text("SELECT count(DISTINCT plant_a) FROM plant_pairings"))).scalar()
        print(f"plant_pairings rebuilt: {total} rows, {cov} plants with pairings, "
              f"N={N} recipes, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
