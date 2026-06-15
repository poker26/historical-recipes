"""«Линтер гербария» — validator registry + finding upsert engine.

A *validator* is a registered pure-read callable `async (db) -> list[Finding]`
that emits findings for one `check_id` and NEVER mutates. The engine upserts those
findings into `data_quality_findings`, deduped on `(check_id, entity_id)`, with:

* sticky human triage — `confirmed`/`dismissed`/`fixed` rows are not resurrected
  to `open` by a later sweep;
* stale-aging — an `open` finding of a check that is no longer emitted (problem
  gone) is moved to `stale`.

Adding a check = writing one decorated function. The catalogue is the registry.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models.data_quality import DataQualityFinding


def norm(s: str | None) -> str:
    """Comparable form for name matching: lowercased, whitespace-collapsed, trimmed.

    Keeps internal spaces (so «соль калия» ≠ «соль») but ignores case and spacing
    noise. NOT alnum-stripping — for head-noun work we need word boundaries."""
    return " ".join((s or "").lower().split())


@dataclass
class Finding:
    check_id: str
    severity: str          # P0 | P1 | P2
    entity_type: str       # plant | recipe | oil | book | qdrant
    entity_id: str
    title: str
    evidence: dict = field(default_factory=dict)
    suggested_fix: dict | None = None
    auto_fixable: bool = False


_REGISTRY: dict[str, dict] = {}   # check_id -> {fn, severity, auto_fixable, description}


def validator(check_id: str, severity: str, auto_fixable: bool = False, description: str = ""):
    """Register a validator for `check_id`. The function gets a read `db` session."""
    def deco(fn):
        _REGISTRY[check_id] = {
            "fn": fn, "severity": severity,
            "auto_fixable": auto_fixable, "description": description,
        }
        return fn
    return deco


def registered_checks() -> dict[str, dict]:
    return {cid: {k: v for k, v in meta.items() if k != "fn"}
            for cid, meta in _REGISTRY.items()}


async def _upsert(check_id: str, findings: list[Finding]) -> dict:
    now = datetime.now(timezone.utc)
    # Defensive: a validator must emit at most one finding per entity_id (the
    # unique constraint enforces it). Dedup last-wins so a buggy validator degrades
    # to "last finding for that entity" instead of crashing the whole check.
    findings = list({f.entity_id: f for f in findings}.values())
    async with async_session() as db:
        existing = {
            f.entity_id: f for f in (await db.execute(
                select(DataQualityFinding).where(DataQualityFinding.check_id == check_id)
            )).scalars()
        }
        seen: set[str] = set()
        new = updated = 0
        for fnd in findings:
            seen.add(fnd.entity_id)
            ex = existing.get(fnd.entity_id)
            if ex is None:
                db.add(DataQualityFinding(
                    check_id=fnd.check_id, severity=fnd.severity,
                    entity_type=fnd.entity_type, entity_id=fnd.entity_id,
                    title=fnd.title, evidence=fnd.evidence,
                    suggested_fix=fnd.suggested_fix, auto_fixable=fnd.auto_fixable,
                    status="open", first_seen=now, last_seen=now,
                ))
                new += 1
            else:
                ex.last_seen = now
                ex.title = fnd.title
                ex.evidence = fnd.evidence
                ex.suggested_fix = fnd.suggested_fix
                ex.severity = fnd.severity
                ex.auto_fixable = fnd.auto_fixable
                if ex.status == "stale":      # problem came back
                    ex.status = "open"
                # open stays open; confirmed/dismissed/fixed are sticky — untouched
                updated += 1
        staled = 0
        for eid, ex in existing.items():
            if eid not in seen and ex.status == "open":
                ex.status = "stale"           # no longer emitted → problem gone
                ex.last_seen = now
                staled += 1
        await db.commit()
    return {"check_id": check_id, "found": len(findings),
            "new": new, "updated": updated, "staled": staled}


async def run_sweep(check_ids: list[str] | None = None) -> list[dict]:
    """Run the given checks (or all registered) and upsert their findings.

    Each validator gets its OWN read session, then the upsert runs in a separate
    write session — so a slow/failed validator can't poison another's transaction.
    """
    ids = check_ids or list(_REGISTRY.keys())
    results = []
    for cid in ids:
        meta = _REGISTRY.get(cid)
        if not meta:
            results.append({"check_id": cid, "error": "unknown check"})
            continue
        try:
            async with async_session() as db:
                findings = await meta["fn"](db)
            res = await _upsert(cid, findings)
        except Exception as e:  # one bad check must not kill the sweep
            res = {"check_id": cid, "error": str(e)[:200]}
        results.append(res)
    return results
