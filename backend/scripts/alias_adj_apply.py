"""Adjudicate ALL open alias.collision findings, then bulk-apply ≥0.9 verdicts.

Idempotent: run_adjudication skips already-judged findings, so a re-run resumes.
Run detached: docker exec -d -e PYTHONPATH=/app <c> sh -c 'python scripts/alias_adj_apply.py > /tmp/alias_adj_apply.log 2>&1'
"""
import asyncio
import httpx

from app.services.data_quality.adjudicator import run_adjudication


async def main():
    total = 0
    while True:
        r = await run_adjudication("alias.collision", limit=500)
        j = r.get("judged", 0)
        total += j
        print(f"batch={j} total={total} remaining={r.get('remaining')} "
              f"verdicts={r.get('verdicts')}", flush=True)
        if j == 0:
            break
    print("adjudication complete — bulk-applying ≥0.9 ...", flush=True)
    rr = httpx.post(
        "http://localhost:8000/api/quality/bulk-apply"
        "?check_id=alias.collision&min_confidence=0.9", timeout=1800)
    print(f"bulk-apply: {rr.status_code} {rr.text}", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
