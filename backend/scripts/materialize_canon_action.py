"""Materialise `plant_medicinal_uses.canon_action` from action_normalize.

App-wide canonical action vocabulary: computes the canonical action for every use (over
the action_id-linked name OR the raw `action_raw`) and stores it, so facets / `?action=`
filter / plant card / MCP all speak one clean ~60-action language. NULL = route/meta
dropped or untranslated long tail. Idempotent (recomputes the column).

A distinct-source → canon map (~20k strings) is built in Python once, then applied with two
set-based UPDATEs. Run after editing action_normalize:
    docker compose exec -T backend python < backend/scripts/materialize_canon_action.py

NOTE: stored reader-monographs (plant_reader_monograph, served by ?view=field) cache the
old action names — they refresh on the next Layer-2 monograph regen.
"""
import asyncio

from sqlalchemy import text

from app.database import async_session
from app.services.action_normalize import canonicalize_action


async def main():
    async with async_session() as db:
        await db.execute(text("ALTER TABLE plant_medicinal_uses ADD COLUMN IF NOT EXISTS canon_action text"))
        await db.execute(text("CREATE INDEX IF NOT EXISTS ix_pmu_canon ON plant_medicinal_uses(canon_action)"))
        await db.commit()

        names = [r[0] for r in (await db.execute(text(
            "SELECT DISTINCT name FROM medicinal_actions WHERE name IS NOT NULL"))).all()]
        raws = [r[0] for r in (await db.execute(text(
            "SELECT DISTINCT action_raw FROM plant_medicinal_uses WHERE action_raw IS NOT NULL"))).all()]
        srcs = {n.strip().lower() for n in names} | {r.strip().lower() for r in raws}
        mapped = {s: c for s in srcs if (c := canonicalize_action(s))}

        await db.execute(text("CREATE TEMP TABLE acmap(src text PRIMARY KEY, canon text) ON COMMIT DROP"))
        rows = list(mapped.items())
        for i in range(0, len(rows), 500):
            chunk = rows[i:i + 500]
            vals = ",".join(f"(:s{j},:c{j})" for j in range(len(chunk)))
            params = {}
            for j, (s, c) in enumerate(chunk):
                params[f"s{j}"], params[f"c{j}"] = s, c
            await db.execute(text(f"INSERT INTO acmap(src,canon) VALUES {vals} ON CONFLICT DO NOTHING"), params)

        await db.execute(text("UPDATE plant_medicinal_uses SET canon_action=NULL"))
        u1 = (await db.execute(text(
            "UPDATE plant_medicinal_uses u SET canon_action=m.canon "
            "FROM medicinal_actions a JOIN acmap m ON m.src=lower(a.name) WHERE u.action_id=a.id"))).rowcount
        u2 = (await db.execute(text(
            "UPDATE plant_medicinal_uses u SET canon_action=m.canon "
            "FROM acmap m WHERE u.action_id IS NULL AND m.src=lower(u.action_raw)"))).rowcount
        await db.commit()
        cov = (await db.execute(text(
            "SELECT count(*) FILTER (WHERE canon_action IS NOT NULL), count(*) FROM plant_medicinal_uses"))).first()
        dist = (await db.execute(text(
            "SELECT count(DISTINCT canon_action) FROM plant_medicinal_uses WHERE canon_action IS NOT NULL"))).scalar()
        print(f"canon_action set: via action_id={u1} via raw={u2} | "
              f"coverage {cov[0]}/{cov[1]} ({100*cov[0]//cov[1]}%) | distinct canon={dist}")


if __name__ == "__main__":
    asyncio.run(main())
