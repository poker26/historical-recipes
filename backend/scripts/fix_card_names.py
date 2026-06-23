"""Rename specific garbage/archaic plant-card primary names to a VERIFIED canonical name.

Deliberately NOT heuristic. The herbarium has ~40 active cards with latin-only / OCR-garbage
primary names, but their `names_historical` aliases are polluted (identity.over_merge), so a
«best alias» guess injects WRONG names (Ferula→«Фенхель», Chamomilla→«Кориандр»). Fixing those
properly needs the iNat/GBIF latin→Russian authority + per-card merge — a separate sub-project.

This applies ONLY the explicit, hand-verified renames in RENAMES, each checked for: latin
confirms the name, the canonical name does NOT already exist on another card (no dup), and the
old name is preserved as an alias. Audited in `plant_rename_audit` (reversible).
"""
import asyncio
from sqlalchemy import text
from app.database import async_session

# id-prefix -> (verified canonical name, why-it's-safe). Add only after verifying no collision.
RENAMES = {
    "1664b00a": ("Мускатный орех",
                 "latin=Myristica fragrans Houtt.; no existing «Мускатный орех» card; "
                 "names_historical already carries «Мускатный орех» + «басбас» (kept as alias)"),
}


async def main():
    async with async_session() as db:
        await db.execute(text(
            "CREATE TABLE IF NOT EXISTS plant_rename_audit (id uuid, old_name text, "
            "new_name text, reason text, at timestamptz DEFAULT now())"))
        for prefix, (new_name, reason) in RENAMES.items():
            row = (await db.execute(text(
                "SELECT id::text, name, names_historical FROM plants WHERE id::text LIKE :x LIMIT 1"),
                {"x": prefix + "%"})).first()
            if not row:
                print(f"  {prefix}: not found"); continue
            pid, old_name, hist = row
            if old_name == new_name:
                print(f"  {prefix}: already «{new_name}»"); continue
            # collision guard
            clash = (await db.execute(text(
                "SELECT count(*) FROM plants WHERE lower(name)=lower(:n) AND id<>:id"),
                {"n": new_name, "id": pid})).scalar()
            if clash:
                print(f"  {prefix}: REFUSING — «{new_name}» already exists on {clash} other card(s)")
                continue
            hist = list(hist or [])
            if old_name and old_name not in hist:
                hist.append(old_name)            # keep the old name as a searchable alias
            await db.execute(text(
                "INSERT INTO plant_rename_audit (id, old_name, new_name, reason) "
                "VALUES (:id,:o,:n,:r)"), {"id": pid, "o": old_name, "n": new_name, "r": reason})
            await db.execute(text(
                "UPDATE plants SET name=:n, names_historical=:h WHERE id=:id"),
                {"n": new_name, "h": hist or None, "id": pid})
            print(f"  {old_name!r} -> {new_name!r}  ({pid[:8]})")
        await db.commit()
        print("done (plant_rename_audit holds the reversible record).")


if __name__ == "__main__":
    asyncio.run(main())
