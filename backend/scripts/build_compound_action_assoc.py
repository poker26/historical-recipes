"""Recompute `compound_action_assoc` — the компонент→действие association engine.

Sibling of the «сочетаемость» (plant_pairings) engine. For each compound (in ≥MIN_PLANTS
plants), which medicinal ACTIONS the plants carrying it are statistically associated with
— ranked by a hypergeometric (Fisher) tail p-value (NOT raw lift: with few plants per
compound lift inflates on coincidences; the p-value down-weights small-sample flukes,
lift+support reported alongside). Powers GET /plants/{id}/compound_insights («почему может
работать» — hypothesis, NOT medical advice).

GROUNDED association via the plants — never a causal claim. Uses the NORMALIZED action
(plant_medicinal_uses.action_id), not the noisy free-text action_raw.

NOTE (quality ceiling): the compound layer is sourced from general herbals that record
composition loosely (классы/нутри-генерики), not rigorous phytochemistry — so many
associations are class-level. Loading phytochemistry references (Растительные ресурсы
СССР, химия классов соединений, ГФ) is the biggest quality unlock for this engine. The
endpoint denylists non-mechanistic «compounds» (витамины/минералы/растворители/плейсхолдеры).

Run (idempotent, ~11s on the full corpus):
    docker compose exec -T backend python < backend/scripts/build_compound_action_assoc.py
"""
import asyncio
import time
from collections import Counter, defaultdict

from sqlalchemy import text

from app.database import async_session
from app.services.associations import _hyper_sf

MIN_PLANTS = 15   # compound must occur in ≥15 base plants for stable stats
SUP = 5           # action support gate (plants with compound AND action)
P_GATE = 0.01     # store only significant
TOPK = 12         # top actions per compound

DDL = [
    """CREATE TABLE IF NOT EXISTS compound_action_assoc(
         compound_id uuid, action_id uuid, action_name text,
         support int, lift real, p_value double precision, n_compound_plants int,
         PRIMARY KEY(compound_id, action_id))""",
    "CREATE INDEX IF NOT EXISTS ix_caa_comp ON compound_action_assoc(compound_id, p_value)",
]


async def main():
    async with async_session() as db:
        t0 = time.time()
        for d in DDL:
            await db.execute(text(d))
        await db.execute(text("TRUNCATE compound_action_assoc"))
        await db.commit()

        async def q(s):
            return (await db.execute(text(s))).fetchall()

        # plant → set(action_id); action names; base = plants with ≥1 normalized action
        pa = defaultdict(set)
        for pid, aid in await q("SELECT plant_id, action_id FROM plant_medicinal_uses "
                                "WHERE action_id IS NOT NULL"):
            pa[pid].add(aid)
        anames = {aid: nm for aid, nm in await q("SELECT id, name FROM medicinal_actions")}
        base = set(pa)
        N = len(base)
        K = Counter()                                  # action → #base plants with it
        for p in base:
            for a in pa[p]:
                K[a] += 1
        cp = defaultdict(set)                           # compound → set(plant_id)
        for cid, pid in await q("SELECT compound_id, plant_id FROM plant_compounds "
                                "WHERE compound_id IS NOT NULL"):
            cp[cid].add(pid)

        ins = comps = 0
        for cid, plants in cp.items():
            src = plants & base
            n = len(src)
            if n < MIN_PLANTS:
                continue
            comps += 1
            A = Counter()
            for p in src:
                for a in pa[p]:
                    A[a] += 1
            rows = []
            for a, cnt in A.items():
                if cnt < SUP:
                    continue
                Kk = K[a]
                if not Kk:
                    continue
                lift = (cnt / n) / (Kk / N)
                pv = _hyper_sf(cnt, N, Kk, n)
                if pv < P_GATE:
                    rows.append((a, cnt, lift, pv))
            rows.sort(key=lambda r: (r[3], -r[2]))
            for a, cnt, lift, pv in rows[:TOPK]:
                await db.execute(text(
                    "INSERT INTO compound_action_assoc"
                    "(compound_id,action_id,action_name,support,lift,p_value,n_compound_plants) "
                    "VALUES(:c,:a,:an,:s,:l,:p,:n) ON CONFLICT DO NOTHING"),
                    dict(c=str(cid), a=str(a), an=anames.get(a), s=cnt, l=lift, p=pv, n=n))
                ins += 1
        await db.commit()
        print(f"compound_action_assoc rebuilt: {comps} compounds, {ins} assoc rows, "
              f"N(base)={N}, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
