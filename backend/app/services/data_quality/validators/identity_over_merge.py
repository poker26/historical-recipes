"""identity.over_merge — several DISTINCT species collapsed into one «magnet» card
(`d9a0fe6d`: «ГОЛУБИКА (ЧЕРНИКА, БРУСНИКА)», latin «Vacciniun», 171 KB of facts from
three Vaccinium). Fix = SPLIT by species (ground each fact on its original_text);
destructive → deferred. This DETECTS only.

HARD PART (learned by a 3338-FP first pass): Russian herbals constantly list SYNONYMS
(«первоцвет, или примула»; «Лопушникъ, Репейникъ» = one burdock), which a naive multi-
noun rule reads as multiple species. Synonym-vs-sibling-species is a SEMANTIC call that
pure-read heuristics can't make — so this check is a deliberately NARROW *candidate*
generator, not a verdict:
  1. the multi-noun must sit INSIDE PARENTHESES «X (Y, Z)» (the merge shape), and
  2. the parenthetical must NOT contain «или»/«syn» (an explicit synonym marker), and
  3. it must yield ≥2 DISTINCT noun heads (adjectives dropped), and
  4. the latin must be genus-only/absent (the tell that species got collapsed), and
  5. the card must be a MAGNET — fact count ≥ threshold (a thin synonym card isn't).
Even so, the output needs synonym-vs-species confirmation (taxonomy/LLM) before a split.
"""
import re

from sqlalchemy import select, text

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, validator

_ADJ = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
        "ого", "его", "ому", "ом", "ую", "юю")
_PARENS = re.compile(r"\(([^)]*)\)")
_INNER_SPLIT = re.compile(r"[,;/]")
_WORD = re.compile(r"[а-яё]+")
_LAT = re.compile(r"[A-Za-z]+")
_SYN_MARK = re.compile(r"\bили\b|\bsyn\b|\bсин\b", re.I)
_MAGNET_FACTS = 15   # a real over-merge accretes facts from several species

_FACTS_SQL = text(
    "SELECT p.id,"
    " (SELECT count(*) FROM plant_medicinal_uses x WHERE x.plant_id=p.id)"
    "+(SELECT count(*) FROM plant_compounds x WHERE x.plant_id=p.id)"
    "+(SELECT count(*) FROM plant_culinary_uses x WHERE x.plant_id=p.id)"
    "+(SELECT count(*) FROM plant_toxicities x WHERE x.plant_id=p.id)"
    "+(SELECT count(*) FROM plant_harvests x WHERE x.plant_id=p.id)"
    "+(SELECT count(*) FROM plant_habitats x WHERE x.plant_id=p.id) AS facts "
    "FROM plants p WHERE p.id = ANY(:ids)")


def _heads(text_in: str) -> set[str]:
    out: set[str] = set()
    for seg in _INNER_SPLIT.split(text_in):
        for w in _WORD.findall(seg.lower().replace("ё", "е")):
            if len(w) >= 4 and not w.endswith(_ADJ):
                out.add(w)
                break
    return out


@validator("identity.over_merge", severity="P2", auto_fixable=False,
           description="candidate magnet card: «X (Y, Z)» multi-species in parens + genus-only latin + fact-magnet — needs synonym-vs-species confirmation before split")
async def check_identity_over_merge(db) -> list[Finding]:
    rows = (await db.execute(select(Plant.id, Plant.name, Plant.name_latin))).all()

    cand: dict[str, dict] = {}
    for pid, name, latin in rows:
        if not name or "(" not in name:
            continue
        genus_only = (not latin) or len(_LAT.findall(latin)) <= 1
        if not genus_only:
            continue
        heads: set[str] = set()
        for grp in _PARENS.findall(name):
            if _SYN_MARK.search(grp):      # explicit synonym list → not a merge
                continue
            heads |= _heads(grp)
        if len(heads) >= 2:
            cand[str(pid)] = {"name": name, "latin": latin, "heads": sorted(heads)}

    if not cand:
        return []

    # Magnet gate — only fact-heavy cards (accreted from several species) qualify.
    facts = {str(r[0]): r[1] for r in (await db.execute(
        _FACTS_SQL, {"ids": list(cand.keys())})).all()}

    findings: list[Finding] = []
    for pid, c in cand.items():
        nfacts = facts.get(pid, 0)
        if nfacts < _MAGNET_FACTS:
            continue
        findings.append(Finding(
            check_id="identity.over_merge", severity="P2",
            entity_type="plant", entity_id=pid,
            title=(f"«{c['name']}»: возможно слиты разные виды ({', '.join(c['heads'][:4])}) "
                   f"при genus-латыни «{c['latin'] or '—'}», {nfacts} фактов-магнит — кандидат на split"),
            evidence={"plant": c["name"], "name_latin": c["latin"], "parens_heads": c["heads"],
                      "facts": nfacts},
            suggested_fix={"action": "split_card", "plant_id": pid,
                           "note": "CONFIRM synonym-vs-species (taxonomy/LLM) first; split grounded on original_text; destructive — defer until corpus drains"},
        ))
    return findings
