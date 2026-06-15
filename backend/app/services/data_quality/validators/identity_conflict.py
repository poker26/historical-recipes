"""identity.conflict — a plant tagged BOTH toxic AND edible (RFC-reader-monograph §8).
NOT a data defect: almost every medicinal plant is "toxic" (dose-dependent — ландыш
is a heart medicine but don't eat the berries; мухомор is edible only after a double
boil with the water poured off). The data is usually CORRECT — what's missing is a
VERDICT whose WORDING carries the condition (dose / preparation / which part). Marking
medicinal plants as flatly toxic would just scare users off the whole herbarium, which
is the opposite of the goal. So this check is a **Layer-2 verdict work-queue**, NOT a
fix-the-tag queue — the resolution is always "write a conditional verdict", never
"strip is_toxic".

Two priorities (severity = how acute the wording matters, NOT how broken the data is):
* **P1 same-part** — the SAME part is both edible and toxic (мухомор: плодовое тело
  ядовито сырым / съедобно после отваривания). The verdict MUST spell out the
  condition — eating it raw is acutely dangerous, so this wording is safety-critical.
* **P2 unreconciled** — toxic + edible on DIFFERENT parts, or `is_toxic` with no part
  scoping (Хмель: побеги съедобны, шишки одурманивают). Lower acute risk; an advisory
  «нужен аккуратный вердикт».

Pure-read: only emits findings, never mutates.
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, validator, norm


def _parts(values) -> set[str]:
    out: set[str] = set()
    for v in values or []:
        n = norm(v)
        if n:
            out.add(n)
    return out


@validator("identity.conflict", severity="P2", auto_fixable=False,
           description="toxic+edible plant needs a CONDITIONAL verdict (dose/preparation/part) — Layer-2 wording, not a tag fix")
async def check_identity_conflict(db) -> list[Finding]:
    # Only plants with an edible signal can conflict — scope the scan to them, then
    # eager-load toxicities + culinary so the per-plant check is N+0.
    plants = (await db.execute(
        select(Plant)
        .where(Plant.culinary_uses.any())
        .options(selectinload(Plant.toxicities), selectinload(Plant.culinary_uses))
    )).scalars().all()

    findings: list[Finding] = []
    for p in plants:
        is_toxic = bool(p.is_toxic) or bool(p.toxicities)
        if not is_toxic:
            continue

        toxic_parts: set[str] = set()
        for t in p.toxicities:
            toxic_parts |= _parts(t.toxic_parts)
        edible_parts = _parts([cu.part for cu in p.culinary_uses])

        overlap = sorted(toxic_parts & edible_parts)
        if overlap:
            sev = "P1"
            title = (f"«{p.name}»: часть «{overlap[0]}» и съедобна, и ядовита — вердикт обязан "
                     f"назвать условие (доза/способ приготовления)")
            kind = "same_part"
        else:
            sev = "P2"
            scope = "без указания ядовитых частей" if not toxic_parts else "на разных частях"
            title = (f"«{p.name}»: и съедобно, и ядовито ({scope}) — нужен аккуратный вердикт")
            kind = "unreconciled"

        findings.append(Finding(
            check_id="identity.conflict", severity=sev,
            entity_type="plant", entity_id=str(p.id),
            title=title,
            evidence={"plant": p.name, "name_latin": p.name_latin, "is_toxic": bool(p.is_toxic),
                      "toxic_parts": sorted(toxic_parts), "edible_parts": sorted(edible_parts),
                      "overlap": overlap, "kind": kind},
            # Layer-2 resolution: write a conditional verdict. NEVER strip is_toxic —
            # almost every medicinal plant is dose-dependent toxic; flat-toxic kills use.
            suggested_fix={"action": "write_conditional_verdict", "plant_id": str(p.id),
                           "kind": kind, "never": "strip_is_toxic"},
        ))
    return findings
