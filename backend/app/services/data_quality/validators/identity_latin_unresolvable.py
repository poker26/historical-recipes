"""identity.latin_unresolvable (P1) — name_latin resolves to NO GBIF taxon.

GBIF returned matchType=NONE for the binomial: it is not a real (or not a
recognisable) name — OCR garbage that survived as ascii, a made-up/misspelt
binomial, or a non-name. Complements norm.mixed_script (which catches Cyrillic
contamination); this catches ascii-but-unresolvable latins. Reads the GBIF cache
(populate via POST /api/quality/resolve-taxonomy first).

Human-confirm — may need a re-read / re-OCR of the source.
"""
from sqlalchemy import select

from app.models.plant import Plant
from app.models.gbif_cache import GbifTaxonCache
from app.services.plant_matching import _latin_key
from app.services.data_quality.framework import Finding, validator


@validator("identity.latin_unresolvable", severity="P1", auto_fixable=False,
           description="name_latin resolves to no GBIF taxon (matchType=NONE)")
async def check_latin_unresolvable(db) -> list[Finding]:
    cache = {c.latin_key: c for c in (await db.execute(select(GbifTaxonCache))).scalars()}
    rows = (await db.execute(
        select(Plant.id, Plant.name, Plant.name_latin)
        .where(Plant.name_latin.isnot(None))
    )).all()

    findings: list[Finding] = []
    for pid, name, latin in rows:
        k = _latin_key(latin)
        c = cache.get(k) if k else None
        if not c or c.match_type != "NONE":
            continue
        findings.append(Finding(
            check_id="identity.latin_unresolvable", severity="P1",
            entity_type="plant", entity_id=str(pid),
            title=f"Латынь «{latin}» (растение «{name}») не резолвится в GBIF",
            evidence={"plant": name, "name_latin": latin, "latin_key": k},
            suggested_fix={"action": "review_latin", "plant_id": str(pid),
                           "name_latin": latin},
        ))
    return findings
