"""Planning helper: reconstruct klex sweep progress from Temporal history alone.

No klex.ru requests at all — it reads the durable workflow history of
``klex-herb-download`` to recover (a) the full ordered catalogue the original
sweep fetched via ``klex_list_activity`` and (b) the per-book results of every
``klex_download_activity`` that ran across the whole continue-as-new chain.

From that it prints the set of codes still needing a download (never processed,
or processed with status 'failed'), so we can re-run the sweep over ONLY those
on a fresh egress IP and waste zero requests on already-mirrored books.

Run inside any container that can reach Temporal (e.g. the klex worker):
    python scripts/klex_plan.py            # summarise + write remaining to stdout
    python scripts/klex_plan.py --dump /tmp/remaining.json
"""

import argparse
import asyncio
import json

from temporalio.client import Client

from app.config import settings

WF_ID = "klex-herb-download"

# phantastike slugs are a plain GOST-ish transliteration of the (lowercased)
# Russian title with spaces -> '_'. Reverse-engineered from observed pairs, e.g.
# "Золотой корень" -> zolotoy_koren, "Зелень для жизни" -> zelen_dlya_zhizni.
_TR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(s: str) -> str:
    return "".join(_TR.get(ch, ch) for ch in (s or "").lower())


def _norm(s: str) -> str:
    """Collapse to comparable form: keep only [a-z0-9], drop everything else."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum() and ord(ch) < 128)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", help="write remaining [[code,title]...] JSON here")
    args = ap.parse_args()

    client = await Client.connect(settings.temporal_address,
                                  namespace=settings.temporal_namespace)

    # 1) enumerate every run of the workflow id (all continue-as-new segments,
    #    plus my later fresh/terminated starts).
    runs = []
    async for wf in client.list_workflows(f"WorkflowId='{WF_ID}'"):
        runs.append(wf)
    runs.sort(key=lambda w: w.start_time)
    print(f"=== {len(runs)} run(s) for WorkflowId={WF_ID!r} ===")
    for w in runs:
        print(f"  run={w.run_id}  status={w.status.name if w.status else '?'}"
              f"  start={w.start_time}  close={w.close_time}")

    # 2) walk every run's history, collecting:
    #    - the catalogue (first klex_list_activity result we see)
    #    - per-code download status (last one wins)
    from temporalio.client import WorkflowHistoryEventFilterType  # noqa

    catalogue = None  # list of [code, title]
    status_by_code: dict[str, str] = {}
    n_dl_results = 0

    for w in runs:
        handle = client.get_workflow_handle(WF_ID, run_id=w.run_id)
        try:
            hist = await handle.fetch_history()
        except Exception as e:
            print(f"  ! could not fetch history for {w.run_id}: {e}")
            continue
        for ev in hist.events:
            comp = ev.activity_task_completed_event_attributes
            if comp is None or not comp.result or not comp.result.payloads:
                continue
            try:
                data = json.loads(comp.result.payloads[0].data.decode())
            except Exception:
                continue
            # klex_list result: a list of [code, title]
            if isinstance(data, list) and data and isinstance(data[0], list):
                if catalogue is None:
                    catalogue = data
                continue
            # klex_download result: a dict {code, status, ...}
            if isinstance(data, dict) and "code" in data and "status" in data:
                status_by_code[data["code"]] = data["status"]
                n_dl_results += 1

    print(f"\n=== history mined ===")
    print(f"  catalogue size: {len(catalogue) if catalogue else 'NOT FOUND'}")
    print(f"  download results seen: {n_dl_results}")
    from collections import Counter
    print(f"  status tally: {dict(Counter(status_by_code.values()))}")

    if not catalogue:
        print("\n!! no catalogue in history — cannot plan remaining. Abort.")
        return 1

    # 3) The bucket is the real record of what's mirrored (earlier attempts'
    #    history aged out). List its slugs and reconcile against the catalogue.
    from app.services import minio as minio_svc
    client_m = minio_svc.get_client()
    bucket_slugs = set()
    for o in client_m.list_objects("klex-herb", recursive=True):
        name = o.object_name.rsplit("/", 1)[-1]          # strip any prefix
        slug = name.rsplit(".", 1)[0]                      # strip extension
        bucket_slugs.add(slug.lower())                     # keep '_' token sep
    print(f"\n=== bucket ===")
    print(f"  objects (slugs): {len(bucket_slugs)}")

    # codes whose slug we positively know from history and which ARE in the bucket
    done_codes = set()
    for code, status in status_by_code.items():
        if status in ("ok", "skip"):
            done_codes.add(code)
        # no_file is terminal-done too (nothing to fetch), keep it out of remaining
        if status == "no_file":
            done_codes.add(code)

    # Token-overlap match: a bucket slug is "done" if some catalogue title's
    # transliterated token-set covers (almost) all of the slug's tokens. This is
    # robust to word reordering, slug truncation and minor translit differences,
    # which exact/prefix matching is not. We match per-slug (each bucket object
    # is one real book) and pick the best-covering catalogue entry.
    import re as _re

    def _toks(s: str) -> list[str]:
        # split on ANY non-alphanumeric so it works for both slug underscores
        # AND transliterated titles (which are space-separated).
        return [w for w in _re.split(r"[^a-z0-9]+", (s or "").lower()) if w]

    # precompute catalogue title token-sets
    cat_toks = [(code, set(_toks(_translit(title)))) for code, title in catalogue]

    def _covers(slug_tokens: set[str], title_tokens: set[str]) -> float:
        if not slug_tokens:
            return 0.0
        hit = 0
        for st in slug_tokens:
            if len(st) < 3:           # ignore tiny tokens (i, v, na, ...)
                hit += 1 if st in title_tokens else 0
                continue
            # prefix match tolerates truncated slug tokens and stem variants
            if any(tt == st or tt.startswith(st) or st.startswith(tt)
                   for tt in title_tokens):
                hit += 1
        return hit / len(slug_tokens)

    # Build all (score, slug, code) candidate pairs above threshold, then assign
    # greedily by descending score so each catalogue code is claimed by at most
    # ONE bucket slug (no collisions inflating the "remaining" list).
    candidates = []
    for bs in bucket_slugs:
        stoks = set(_toks(bs))
        ntok = len(stoks)
        thresh = 0.6 if ntok >= 3 else (0.99 if ntok == 1 else 0.75)
        for code, ttoks in cat_toks:
            sc = _covers(stoks, ttoks)
            if sc >= thresh:
                candidates.append((sc, bs, code))
    candidates.sort(key=lambda x: -x[0])

    matched_slugs = set()
    used_codes = set()
    for sc, bs, code in candidates:
        if bs in matched_slugs or code in used_codes:
            continue
        matched_slugs.add(bs)
        used_codes.add(code)
        done_codes.add(code)

    unmatched_examples = [bs for bs in bucket_slugs if bs not in matched_slugs]
    unmatched_bucket = len(bucket_slugs) - len(matched_slugs)
    print("  sample UNMATCHED bucket slugs:", unmatched_examples[:25])
    remaining = [[code, title] for code, title in catalogue if code not in done_codes]
    print(f"\n=== reconcile ===")
    print(f"  catalogue:                 {len(catalogue)}")
    print(f"  bucket slugs matched:      {len(matched_slugs)} / {len(bucket_slugs)}"
          f"  (unmatched bucket slugs: {unmatched_bucket})")
    print(f"  done (history+bucket):     {len(done_codes)}")
    print(f"  REMAINING to fetch:        {len(remaining)}")
    print("  first 10 remaining codes:", [c for c, _ in remaining[:10]])

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(remaining, f, ensure_ascii=False)
        print(f"\n  wrote {len(remaining)} remaining -> {args.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
