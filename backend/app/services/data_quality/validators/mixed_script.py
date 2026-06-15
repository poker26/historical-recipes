"""norm.mixed_script (P1) — Cyrillic contamination in name_latin = OCR damage.

A binomial must be pure Latin script. OCR routinely substitutes visually-identical
Cyrillic glyphs (`Rіbes` with Cyrillic і U+0456, `M и r a b и l и s J a l a p a`),
which silently breaks latin-key resolution / dedup. Flag any name_latin holding a
Cyrillic letter, with the offending characters and positions as evidence.

Human-confirm (it signals OCR damage that may need a re-read), not auto.
"""
from sqlalchemy import select

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, validator


def _cyrillic_chars(s: str) -> list[str]:
    # Cyrillic block U+0400–U+04FF (plus a couple of common look-alikes already there).
    return sorted({ch for ch in s if "Ѐ" <= ch <= "ӿ"})


@validator("norm.mixed_script", severity="P1", auto_fixable=False,
           description="name_latin contains Cyrillic letters (OCR look-alike damage)")
async def check_mixed_script(db) -> list[Finding]:
    rows = (await db.execute(
        select(Plant.id, Plant.name, Plant.name_latin)
        .where(Plant.name_latin.isnot(None))
    )).all()

    findings: list[Finding] = []
    for pid, name, latin in rows:
        bad = _cyrillic_chars(latin or "")
        if not bad:
            continue
        findings.append(Finding(
            check_id="norm.mixed_script", severity="P1",
            entity_type="plant", entity_id=str(pid),
            title=f"Кириллица в латыни «{latin}» (растение «{name}»): {' '.join(bad)}",
            evidence={"plant": name, "name_latin": latin,
                      "cyrillic_chars": bad,
                      "codepoints": [f"U+{ord(c):04X}" for c in bad]},
            suggested_fix={"action": "review_ocr_latin", "plant_id": str(pid),
                           "name_latin": latin},
        ))
    return findings
