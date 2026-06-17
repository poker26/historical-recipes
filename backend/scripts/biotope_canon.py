"""Canonicalize every plant's free-text habitat into controlled biotopes and store
plant↔biotope. Per plant: aggregate all its plant_habitats.biotope strings → LLM
multi-label (app.services.biotope) → rows in plant_biotopes (one NULL row marks a
processed plant with no biotope, so 0-tag plants drop out of the todo). Idempotent
+ resumable (NOT IN plant_biotopes). Optional argv[1] = max plants (test).

Run detached: docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/biotope_canon.py > /tmp/biotope_canon.log 2>&1'
"""
import asyncio
import sys

from sqlalchemy import text

from app.database import async_session
from app.services.biotope import canonicalize

MAX = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0 = all


async def main():
    done = tagged = empty = 0
    while True:
        async with async_session() as db:
            rows = (await db.execute(text("""
                SELECT h.plant_id, string_agg(DISTINCT h.biotope, ' | ') AS bio
                FROM plant_habitats h
                WHERE h.biotope IS NOT NULL AND length(h.biotope) > 3
                  AND h.plant_id NOT IN (SELECT plant_id FROM plant_biotopes)
                GROUP BY h.plant_id LIMIT 100
            """))).all()
        if not rows:
            break
        for pid, bio in rows:
            tags = await canonicalize(bio)
            async with async_session() as db:
                if tags:
                    for t in tags:
                        await db.execute(text(
                            "INSERT INTO plant_biotopes (plant_id, biotope) VALUES (:p, :b)"),
                            {"p": str(pid), "b": t})
                    tagged += 1
                else:
                    await db.execute(text(
                        "INSERT INTO plant_biotopes (plant_id, biotope) VALUES (:p, NULL)"),
                        {"p": str(pid)})
                    empty += 1
                await db.commit()
            done += 1
            if MAX and done >= MAX:
                print(f"TEST STOP: done={done} tagged={tagged} empty={empty}", flush=True)
                return
        print(f"done={done} tagged={tagged} empty={empty}", flush=True)
    print(f"FINISHED: done={done} tagged={tagged} empty={empty}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
