"""stub.zero_facts — an identity-less card with NOTHING to recover AND a garbage name:
no latin, no historical names, no description, zero facts of any kind, AND the name
itself is OCR junk (digits / symbols / mixed script / too short). The «0. о 15та» class.

CRITICAL distinction (learned the hard way): a zero-fact card with a VALID Russian
name — «Гриб-зонтик высокий», «Вешенка поздняя», «Роцелла» — is NOT junk; it's a real
species that simply hasn't been enriched/extracted yet. Deleting those loses real
cards. So we delete ONLY when the name is itself unrecoverable garbage; valid-named
thin cards are left for ENRICHMENT (a different, non-destructive path), not deletion.

Detection only (pure-read). The DELETE is destructive → it waits for the corpus to
drain (a still-processing book could yet attach a fact, auto-`stale`-ing the finding).
"""
import re

from sqlalchemy import select, or_, func

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, validator

_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")
_SYMBOL = re.compile(r"[{}\[\]$<>|@©§«»~^*]")


def _is_garbage_name(name: str | None) -> bool:
    """True only for unrecoverable OCR junk — never for a clean vernacular name."""
    s = (name or "").strip()
    if len(s) < 3:
        return True
    if _DIGIT.search(s) or _SYMBOL.search(s):
        return True
    if _CYR.search(s) and _LAT.search(s):       # mixed cyrillic+latin = OCR look-alike
        return True
    if not _CYR.search(s) and not _LAT.search(s):  # no letters at all
        return True
    letters = sum(ch.isalpha() for ch in s)
    return letters < len(s) * 0.5               # punctuation soup


@validator("stub.zero_facts", severity="P2", auto_fixable=False,
           description="identity-less card with a GARBAGE name and zero facts — delete candidate (valid-named thin cards excluded)")
async def check_stub_zero_facts(db) -> list[Finding]:
    rows = (await db.execute(
        select(Plant.id, Plant.name).where(
            Plant.name_latin.is_(None),
            or_(Plant.names_historical.is_(None),
                func.coalesce(func.cardinality(Plant.names_historical), 0) == 0),
            or_(Plant.description.is_(None), func.length(func.trim(Plant.description)) == 0),
            ~Plant.medicinal_uses.any(),
            ~Plant.compounds.any(),
            ~Plant.culinary_uses.any(),
            ~Plant.toxicities.any(),
            ~Plant.harvests.any(),
            ~Plant.habitats.any(),
        )
    )).all()

    findings: list[Finding] = []
    for pid, name in rows:
        if not _is_garbage_name(name):
            continue   # valid name → recoverable real card → enrich, don't delete
        findings.append(Finding(
            check_id="stub.zero_facts", severity="P2",
            entity_type="plant", entity_id=str(pid),
            title=f"«{name}»: OCR-мусорное имя, нет латыни/фактов — пустышка, кандидат на снос",
            evidence={"plant": name},
            suggested_fix={"action": "delete_stub", "plant_id": str(pid),
                           "note": "destructive — defer until corpus drains"},
        ))
    return findings
