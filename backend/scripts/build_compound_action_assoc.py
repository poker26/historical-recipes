"""Recompute `compound_action_assoc` — the компонент→действие association engine.

Sibling of the «сочетаемость» (plant_pairings) engine. For each compound FAMILY (keyed by
``compound_merge_key`` — OCR-Greek-letter + plural + junk normalised so β-ситостерин's ~10
fragments consolidate), the medicinal ACTIONS its carrier-plants are statistically
associated with, ranked by a hypergeometric (Fisher) tail p-value (robust to small samples
— so a low plant-count gate is safe). Powers GET /plants/{id}/compound_insights («почему
может работать» — hypothesis, NOT medical advice). GROUNDED via the plants; never causal.

Normalisation note: the phytochemistry IS richly extracted (Растительные ресурсы СССР etc.),
but molecules fragment under OCR and most specific molecules are inherently sparse — so the
engine is strongest at class + common-molecule level. Junk «compounds» (%, numbers, garble)
are dropped by the merge-key; nutritional/mineral generics are denylisted at the endpoint.

Run (idempotent, ~15s):
    docker compose exec -T backend python < backend/scripts/build_compound_action_assoc.py
"""
import asyncio
import time
from collections import Counter, defaultdict

from sqlalchemy import text

from app.database import async_session
from app.services.associations import _hyper_sf
from app.services.compound_normalize import compound_merge_key
from app.services.action_normalize import canonicalize_action

MIN_PLANTS = 8     # compound family must occur in ≥8 base plants (Fisher p guards small n)
SUP = 4            # action support gate
P_GATE = 0.01      # store only significant
TOPK = 14          # top actions per compound family

DDL = [
    """CREATE TABLE IF NOT EXISTS compound_action_assoc(
         compound_key text, compound_display text, action_canon text,
         support int, lift real, p_value double precision, n_compound_plants int,
         PRIMARY KEY(compound_key, action_canon))""",
    "CREATE INDEX IF NOT EXISTS ix_caa_key ON compound_action_assoc(compound_key, p_value)",
]


async def main():
    async with async_session() as db:
        t0 = time.time()
        await db.execute(text("DROP TABLE IF EXISTS compound_action_assoc"))   # schema changed
        for d in DDL:
            await db.execute(text(d))
        await db.commit()

        async def q(s):
            return (await db.execute(text(s))).fetchall()

        # plant → set(CANONICAL action). Uses BOTH the normalized action_id name and the
        # 47k unmapped action_raw → canonicalize_action collapses synonyms + drops route/meta.
        pa = defaultdict(set)
        for pid, aname, araw in await q(
                "SELECT u.plant_id, a.name, u.action_raw FROM plant_medicinal_uses u "
                "LEFT JOIN medicinal_actions a ON a.id=u.action_id"):
            canon = canonicalize_action(aname or araw)
            if canon:
                pa[pid].add(canon)
        base = set(pa)
        N = len(base)
        K = Counter()
        for p in base:
            for a in pa[p]:
                K[a] += 1

        # compound FAMILY (merge key) → set(plant); + display name (most common raw form)
        fam = defaultdict(set)
        disp_votes = defaultdict(Counter)
        for comp, pid in await q("SELECT compound, plant_id FROM plant_compounds "
                                 "WHERE compound IS NOT NULL"):
            key = compound_merge_key(comp)
            if key is None:
                continue
            fam[key].add(pid)
            disp_votes[key][comp.strip()] += 1
        display = {k: v.most_common(1)[0][0] for k, v in disp_votes.items()}

        ins = comps = 0
        for key, plants in fam.items():
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
                    "(compound_key,compound_display,action_canon,support,lift,p_value,n_compound_plants) "
                    "VALUES(:k,:d,:a,:s,:l,:p,:n) ON CONFLICT DO NOTHING"),
                    dict(k=key, d=display.get(key), a=a, s=cnt, l=lift, p=pv, n=n))
                ins += 1
        await db.commit()
        print(f"compound_action_assoc rebuilt: {comps} compound families, {ins} assoc rows, "
              f"N(base)={N}, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
