"""identity.headnoun_cluster (P2, diagnostic) — the «соль»/«бальзам» class finder.

A name that is a generic head-noun + modifier («соль» → поваренная / калия /
морфина; «бальзам» → итальянский / мускатный) collapses or cross-links distinct
entities, and — crucially — these are NOT biological species, so they carry NO
Latin, so the latin-key disambiguator is useless on them. The discriminator is the
modifier, which the matcher ignores.

This validator is a CANDIDATE-FINDER: among Latin-less plant cards it groups by the
name's head token and flags heads shared by several cards. Its output is the list
of suspicious generic heads (answering "нужен список голов") + their member cards,
to be confirmed by the user and then handled by a dedicated identity rule (key =
full phrase; forbid alias-capture by the bare head).

One finding per HEAD (entity_type=headnoun, entity_id=the head token). P2 — it's a
review lead, not an absurdity by itself.
"""
from collections import defaultdict

from sqlalchemy import select

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, norm, validator

# Heads shared by at least this many distinct Latin-less cards are worth a look.
_MIN_CARDS = 3
# Skip too-short heads (prepositions/particles slipping in as a first token).
_MIN_HEAD_LEN = 3


@validator("identity.headnoun_cluster", severity="P2", auto_fixable=False,
           description="generic head-noun shared by many Latin-less cards (соль/бальзам class)")
async def check_headnoun_cluster(db) -> list[Finding]:
    # Only Latin-less cards: species carry a binomial and are disambiguated by it;
    # the head-noun problem is specific to non-species (соль/бальзам/масло…).
    rows = (await db.execute(
        select(Plant.id, Plant.name)
        .where(Plant.name_latin.is_(None))
    )).all()

    by_head: dict[str, list[tuple]] = defaultdict(list)
    for pid, name in rows:
        toks = norm(name).split()
        if not toks or len(toks[0]) < _MIN_HEAD_LEN:
            continue
        by_head[toks[0]].append((pid, name))

    findings: list[Finding] = []
    for head, members in by_head.items():
        # Need several DISTINCT names (not the same card repeated) to be a cluster.
        distinct_names = {norm(n) for _pid, n in members}
        if len(distinct_names) < _MIN_CARDS:
            continue
        findings.append(Finding(
            check_id="identity.headnoun_cluster", severity="P2",
            entity_type="headnoun", entity_id=head,
            title=f"Голова «{head}»: {len(distinct_names)} карточек без латыни делят её",
            evidence={"head": head, "card_count": len(distinct_names),
                      "cards": [{"id": str(p), "name": n} for p, n in members][:50]},
            suggested_fix={"action": "review_headnoun", "head": head,
                           "note": "ключ идентичности = полная фраза; запретить alias-захват по голой голове"},
        ))
    return findings
