# -*- coding: utf-8 -*-
"""PHASE 0 v3 (measure only — writes NOTHING). Pipeline with iNat as the truth
source for the latin<->Russian correspondence:
  1) LLM proposes the accepted binomial (Russian name + OCR-garbled latin) and
     cleans names_historical;
  2) GBIF confirms the binomial exists (canonical + kingdom);
  3) iNat resolves that binomial -> its Russian common name (locale=ru), which we
     compare to the CARD's Russian name. This catches species-defaulting that GBIF
     can't (Valeriana officinalis -> iNat «Валериана лекарственная» != card
     «Валериана сердечниковая»).
Auto only when iNat's Russian name FULLY corroborates the card name; otherwise
review; junk -> null. Prints per-card detail + tallies.
"""
import asyncio
import json
import re

from sqlalchemy import text

from app.database import async_session
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one
from app.services.inaturalist import resolve_taxon_photo
import httpx

SAMPLE = 30

FILTER = r"""
    name_latin ~ '[А-Яа-яЁё]'
    AND NOT (
        name ~ '^[0-9]'
        OR name ~ '^[A-ZА-ЯЁ]\.'
        OR name ~ '[@{}\[\]\$<>|]'
        OR name ~ '[A-Za-z]'
    )
"""

SYS_FULL = (
    "Ты ботаник-таксономист. Дана карточка растения из исторического русского источника: "
    "русское название, OCR-искажённая латынь (кириллические глифы вместо латинских — "
    "испорченная версия настоящего бинома) и исторические синонимы (тоже OCR, часть мусор). "
    "Задачи:\n"
    "1) Определи ПРИНЯТЫЙ научный бином (род+вид). Опирайся на русское название И на "
    "искажённую латынь. ВАЖНО: если в искажённой латыни или русском эпитете виден конкретный "
    "ВИД — сохрани именно его, НЕ подменяй на самый известный вид рода (напр. «сердечниковая»/"
    "«cardamines» — это cardamines, не officinalis). Не уверен — \"UNKNOWN\". Не выдумывай.\n"
    "2) Почисти синонимы: keep — осмысленные русские народные/исторические названия этого "
    "растения; drop — OCR-мусор, география, обрывки, иностранное.\n"
    "Строго JSON: {\"latin\": \"Genus species\"|\"UNKNOWN\", \"confidence\": 0.0-1.0, "
    "\"aliases_keep\": [], \"aliases_drop\": []}."
)

_WORD = re.compile(r"[а-яёa-z]+")


def words(s):
    return _WORD.findall((s or "").lower())


def name_match(card, inat_ru):
    """Compare card Russian name to iNat Russian name. full / partial / mismatch / absent."""
    if not inat_ru:
        return "absent"
    cw, iw = words(card), words(inat_ru)
    if not cw or not iw:
        return "absent"
    shared = set(cw) & set(iw)
    genus_match = cw[0] == iw[0]
    if genus_match and len(shared) >= 2:
        return "full"          # genus + at least one more word agree
    if shared:
        return "partial"       # share genus OR some word, but not full
    return "mismatch"


def decide(proposed, gbif, nm):
    if not proposed or proposed.upper() == "UNKNOWN":
        return "null_latin"
    if not gbif or (gbif.get("match_type") or "").upper() not in ("EXACT", "FUZZY"):
        return "null_latin"
    if gbif.get("kingdom") not in ("Plantae", "Fungi", "Chromista"):
        return "null_latin"
    if (gbif.get("match_type") or "").upper() == "EXACT" and (gbif.get("confidence") or 0) >= 90 and nm == "full":
        return "auto_write"
    return "review"


async def process(client, sem, row):
    pid, name, latin, hist = row
    hist = list(hist or [])
    async with sem:
        try:
            user = json.dumps({"name": name, "garbled_latin": latin, "aliases": hist}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS_FULL}, {"role": "user", "content": user}],
                task="lightweight", temperature=0.1, max_tokens=2048,
            )
        except Exception as e:
            return {"name": name, "error": str(e)[:70]}
        proposed = (llm.get("latin") or "").strip()
        gbif = inat = None
        if proposed and proposed.upper() != "UNKNOWN":
            gbif = await _resolve_one(client, proposed)
            lookup = (gbif.get("canonical") if gbif else None) or proposed
            inat = await resolve_taxon_photo(client, lookup)
        inat_ru = (inat or {}).get("common_name")
        nm = name_match(name, inat_ru)
        return {
            "name": name, "garbled": latin, "proposed": proposed, "gbif": gbif,
            "inat_ru": inat_ru, "nm": nm, "action": decide(proposed, gbif, nm),
            "keep": llm.get("aliases_keep") or [], "drop": llm.get("aliases_drop") or [],
            "hist_n": len(hist),
        }


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(
            f"SELECT id, name, name_latin, names_historical FROM plants WHERE {FILTER} ORDER BY random() LIMIT {SAMPLE}"
        ))).all()
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(*[process(client, sem, r) for r in rows])

    tally, nmtally = {}, {}
    print("=" * 100)
    for r in results:
        if r.get("error"):
            print(f"[ERR] {r['name']}  ::  {r['error']}")
            tally["error"] = tally.get("error", 0) + 1
            continue
        g = r["gbif"] or {}
        gstr = f"{g.get('match_type')}/{g.get('confidence')}/{g.get('kingdom')}->{g.get('canonical')}" if g else "—"
        print(f"[{r['action'].upper():10} | iNat:{r['nm']:8}] {r['name']}")
        print(f"             garbled : {r['garbled']}")
        print(f"             LLM->    : {r['proposed']}")
        print(f"             GBIF     : {gstr}")
        print(f"             iNat ru  : {r['inat_ru']}")
        if r["hist_n"]:
            print(f"             aliases  : keep={r['keep']}  drop={r['drop']}")
        tally[r["action"]] = tally.get(r["action"], 0) + 1
        nmtally[r["nm"]] = nmtally.get(r["nm"], 0) + 1
    print("=" * 100)
    print("ACTION TALLY:", tally)
    print("iNat-MATCH TALLY:", nmtally)


if __name__ == "__main__":
    asyncio.run(main())
