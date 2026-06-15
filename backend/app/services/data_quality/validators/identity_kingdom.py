"""identity.kingdom (P0) — our kingdom vs GBIF's, via the binomial.

Catches cross-kingdom miscarding: an animal product carded as a plant («Мускус»,
«Бобровая струя»), a fungus tagged растение or vice-versa. Reads the cached GBIF
match (populate via POST /api/quality/resolve-taxonomy first). Only confident
matches (EXACT/FUZZY, conf ≥ 85) drive a P0 verdict — kingdom must be precise.

NB: does NOT catch the «жидовская вишня» case (Physalis carded as Prunus cerasus —
both Plantae); that's name_vs_latin (vernacular), a separate check.
"""
from sqlalchemy import select

from app.models.plant import Plant
from app.models.gbif_cache import GbifTaxonCache
from app.services.plant_matching import _latin_key
from app.services.data_quality.framework import Finding, validator

# GBIF kingdom → our `kingdom` value. Chromista (brown/yellow algae — ламинария,
# фукус, комбу) is treated as «растение» by project decision: algae are legitimate
# herbal materia medica and we keep them in the herbarium as plants. Anything still
# not here (Animalia, Bacteria, Protozoa, Viruses) is neither → always a mismatch
# (those cards are deleted as junk, not re-tagged).
_GBIF_TO_OURS = {"Plantae": "растение", "Fungi": "гриб", "Chromista": "растение"}
_MIN_CONF = 85


@validator("identity.kingdom", severity="P0", auto_fixable=False,
           description="our kingdom (растение/гриб) disagrees with GBIF's for the binomial")
async def check_identity_kingdom(db) -> list[Finding]:
    cache = {c.latin_key: c for c in (await db.execute(select(GbifTaxonCache))).scalars()}
    rows = (await db.execute(
        select(Plant.id, Plant.name, Plant.name_latin, Plant.kingdom)
        .where(Plant.name_latin.isnot(None))
    )).all()

    findings: list[Finding] = []
    for pid, name, latin, our_kingdom in rows:
        k = _latin_key(latin)
        c = cache.get(k) if k else None
        if not c or not c.kingdom:
            continue
        # Precision over recall for a P0: only a confident EXACT *species* match
        # drives a verdict. A GENUS-rank match is the cross-kingdom homonym trap
        # (the plant genus «Juglans»/«Acer» fuzzy-hits an animal/fungus genus of
        # the same name) → skip it; it's a false positive, not a miscarding.
        if c.match_type != "EXACT" or c.rank != "SPECIES":
            continue
        if (c.confidence or 0) < _MIN_CONF:
            continue
        ours = our_kingdom or "растение"
        expected = _GBIF_TO_OURS.get(c.kingdom)   # растение/гриб, or None for Animalia/…
        if expected == ours:
            continue                              # agree → fine
        if expected is None:
            title = f"«{name}» ({latin}): GBIF kingdom={c.kingdom} — это не растение и не гриб, а у нас «{ours}»"
        else:
            title = f"«{name}» ({latin}): у нас «{ours}», GBIF={c.kingdom}→«{expected}»"
        findings.append(Finding(
            check_id="identity.kingdom", severity="P0",
            entity_type="plant", entity_id=str(pid),
            title=title,
            evidence={"plant": name, "name_latin": latin, "our_kingdom": ours,
                      "gbif_kingdom": c.kingdom, "gbif_canonical": c.canonical,
                      "match_type": c.match_type, "confidence": c.confidence},
            suggested_fix={"action": "review_kingdom", "plant_id": str(pid),
                           "gbif_kingdom": c.kingdom},
        ))
    return findings
