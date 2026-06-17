"""compound.bad_hierarchy — a parent edge in the compound vocabulary that is probably
a CONTAINMENT-misread, not a real taxonomy edge. The extractor reads "в камеди найдены
вода, сахара, белки…" and sets вода/сахара/белки's PARENT = камедь — but gum CONTAINS
water, it is not its chemical-class ancestor. The hierarchy IS surfaced (get_compound
returns parent + children; the «камедь» card would then wrongly list вода/белки as
sub-compounds; compound_associations walks the subtree), so bad edges corrupt navigation.

STRUCTURAL signal (no hardcoded substance list): a real class-parent is found at least
as widely as its members, so it has ≥ the child's fact count; a containment-misread is
INVERTED — a very common general child (вода/сахара/витамины, thousands of plant links)
points at a RARE specific substance parent (камедь/прополис/маточное молочко, a handful).
Flag `child_facts > 3·parent_facts + 5 AND child_facts > 20`.

High RECALL, not precision: it also surfaces correct class-edges where the child happens
to be named more often than the umbrella class (флавоноиды←фенольные соединения,
кальций←макроэлементы). Distinguishing «class» from «concrete substance/mixture» is
SEMANTIC → an LLM adjudicator decides per edge; this check only stages the candidates.
Fix = null parent_id for the confirmed containment ones, then re-run the compound dedup.
"""
from sqlalchemy import text

from app.services.data_quality.framework import Finding, validator

_SQL = text("""
    WITH fc AS (
        SELECT compound_id AS cid, count(*) AS n
        FROM plant_compounds WHERE compound_id IS NOT NULL GROUP BY compound_id
    )
    SELECT c.id::text, c.name, c.compound_class, p.id::text, p.name,
           COALESCE((SELECT n FROM fc WHERE cid = c.id), 0) AS cf,
           COALESCE((SELECT n FROM fc WHERE cid = p.id), 0) AS pf
    FROM compounds c JOIN compounds p ON p.id = c.parent_id
    WHERE COALESCE((SELECT n FROM fc WHERE cid = c.id), 0)
            > 3 * COALESCE((SELECT n FROM fc WHERE cid = p.id), 0) + 5
      AND COALESCE((SELECT n FROM fc WHERE cid = c.id), 0) > 20
""")


@validator("compound.bad_hierarchy", severity="P2", auto_fixable=False,
           description="suspect parent edge: child far more common than its 'parent' — likely containment-misread (parent CONTAINS child, isn't its class). Needs LLM/human class-vs-substance call.")
async def check_compound_hierarchy(db) -> list[Finding]:
    rows = (await db.execute(_SQL)).all()
    findings: list[Finding] = []
    for cid, cname, cclass, pid, pname, cf, pf in rows:
        findings.append(Finding(
            check_id="compound.bad_hierarchy", severity="P2",
            entity_type="compound", entity_id=cid,
            title=(f"«{cname}» [{cf} фактов] числится потомком «{pname}» [{pf}] — "
                   f"вероятно containment (родитель содержит ребёнка, а не его класс)"),
            evidence={"child": cname, "child_facts": cf, "child_class": cclass,
                      "parent": pname, "parent_facts": pf, "parent_id": pid},
            suggested_fix={"action": "null_parent_if_containment", "compound_id": cid,
                           "note": "LLM/human: is parent a real chemical CLASS of the child, "
                                   "or a substance/mixture that CONTAINS it? If contains → null parent_id"},
        ))
    return findings
