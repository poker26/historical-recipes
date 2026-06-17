"""Adjudicate ALL open domain.recipe_is_monograph findings (NO auto-apply —
deletion is destructive, so apply is a separate, reviewed step).

Run detached: docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/recipe_mono_adj.py > /tmp/recipe_mono_adj.log 2>&1'
"""
import asyncio

from app.services.data_quality.adjudicator import run_adjudication


async def main():
    total = 0
    while True:
        r = await run_adjudication("domain.recipe_is_monograph", limit=500)
        j = r.get("judged", 0)
        total += j
        print(f"batch={j} total={total} remaining={r.get('remaining')} "
              f"verdicts={r.get('verdicts')}", flush=True)
        if j == 0:
            break
    print("ADJUDICATION COMPLETE (no apply)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
