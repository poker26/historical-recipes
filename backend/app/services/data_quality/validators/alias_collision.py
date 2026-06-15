"""alias.collision (P0) — the matcher landmine.

A plant's `names_historical` entry that, normalized, equals ANOTHER plant's
primary `name`. This is what mis-routed 308 ordinary-cherry recipes onto a wild
steppe cherry (a bare «Вишня» sat in its aliases). Highest leverage for recipe-
routing sanity, and it MUST be cleaned before any corpus-wide relink (relink is
destructive and re-captures aliases — it undoes the fix otherwise).

Human-confirm (strip the alias vs keep it) — never auto.
"""
from sqlalchemy import select

from app.models.plant import Plant
from app.services.data_quality.framework import Finding, norm, validator


@validator("alias.collision", severity="P0", auto_fixable=False,
           description="names_historical entry == another plant's primary name")
async def check_alias_collision(db) -> list[Finding]:
    rows = (await db.execute(
        select(Plant.id, Plant.name, Plant.name_latin, Plant.names_historical)
    )).all()

    # normalized primary name -> [(plant_id, display_name)]
    by_primary: dict[str, list[tuple]] = {}
    for pid, name, _latin, _hist in rows:
        by_primary.setdefault(norm(name), []).append((pid, name))

    # One finding PER PLANT (a card may have several colliding aliases) — keeps
    # entity_id unique and groups the whole card's fix into one triage item.
    findings: list[Finding] = []
    for pid, name, latin, hist in rows:
        collisions = []
        for alias in (hist or []):
            owners = by_primary.get(norm(alias), [])
            others = [(oid, onm) for oid, onm in owners if oid != pid]
            if others:
                collisions.append({
                    "alias": alias,
                    "collides_with": [{"id": str(o), "name": n} for o, n in others],
                })
        if not collisions:
            continue
        aliases = [c["alias"] for c in collisions]
        findings.append(Finding(
            check_id="alias.collision", severity="P0",
            entity_type="plant", entity_id=str(pid),
            title=f"«{name}»: {len(collisions)} алиас(ов) совпадают с основным именем другого растения ({', '.join(aliases)})",
            evidence={"plant": name, "plant_latin": latin, "collisions": collisions},
            suggested_fix={"action": "strip_aliases", "plant_id": str(pid), "aliases": aliases},
        ))
    return findings
