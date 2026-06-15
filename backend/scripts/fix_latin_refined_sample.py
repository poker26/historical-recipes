# -*- coding: utf-8 -*-
"""PHASE 0 — refined gate (writes NOTHING). iNat-by-Russian-name is PRIMARY; the
homonym/kingdom safety comes from GBIF (the iconic_taxa param proved unreliable):
  - resolve iNat's scientific name through GBIF -> REQUIRE kingdom in Plantae/Fungi
    (kills the birds/wasps/bees iNat returns for homonym Russian names);
  - REQUIRE a STRONG Russian-name match (iNat's ru name shares genus + species with
    the card) -> kills species-defaulting (Тимофеевка луговая != щетинистая);
  - LLM agreement is a bonus tier, not required.
auto_write when (GBIF kingdom ok + GBIF confirms + (strong ru-match OR LLM agrees));
else review; nothing resolvable -> null.
"""
import asyncio
import json
import re

from sqlalchemy import text

from app.database import async_session
from app.services.llm import chat_completion_json
from app.services.data_quality.taxonomy import _resolve_one
from app.services.inaturalist import INAT_BASE, _HEADERS
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
    "Ты ботаник-таксономист. Дана карточка: русское название и OCR-искажённая латынь "
    "(кириллица вместо латиницы). Определи ПРИНЯТЫЙ научный бином (род+вид); если виден "
    "конкретный вид — сохрани его, не подменяй на самый известный вид рода. Не уверен — "
    "\"UNKNOWN\". Строго JSON: {\"latin\": \"Genus species\"|\"UNKNOWN\"}."
)

_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")


def ruwords(s):
    return _RU.findall((s or "").lower().replace("ё", "е"))


def norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else ""


def strong_match(card, ru):
    """Genus (first word) AND species epithet (last word) must match. Using the
    last word — not any-2-shared — stops filler words like «почти»/«или» from
    faking agreement when the real epithet differs (почти-яблоконосный vs почти-Буша)."""
    cw, iw = ruwords(card), ruwords(ru)
    if not cw or not iw:
        return False
    if iw[0] != cw[0]:
        return False
    if len(cw) == 1 and len(iw) == 1:
        return True
    return cw[-1] == iw[-1]


async def inat_by_ru(client, name):
    qs = [name]
    w = ruwords(name)
    if len(w) >= 2:
        qs.append(" ".join(w[:2]))
    if w:
        qs.append(w[0])
    for q in qs:
        params = {"q": q, "locale": "ru", "per_page": 5, "is_active": "true"}
        try:
            resp = await client.get(f"{INAT_BASE}/taxa", params=params, headers=_HEADERS)
            if resp.status_code != 200:
                continue
            results = resp.json().get("results", [])
        except Exception:
            continue
        sp = [r for r in results if r.get("rank") == "species"]
        cand = sp[0] if sp else None
        if cand:
            return {"sci": cand.get("name"), "ru": cand.get("preferred_common_name")}
    return None


async def process(client, sem, row):
    pid, name, latin, kingdom = row
    async with sem:
        try:
            user = json.dumps({"name": name, "garbled_latin": latin}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS_FULL}, {"role": "user", "content": user}],
                task="plant_extraction", temperature=0.1, max_tokens=1800,
            )
            llm_sci = (llm.get("latin") or "").strip()
        except Exception:
            llm_sci = ""
        if llm_sci.upper() == "UNKNOWN":
            llm_sci = ""
        inat = await inat_by_ru(client, name)
        inat_sci = (inat or {}).get("sci")
        g_inat = await _resolve_one(client, inat_sci) if inat_sci else None
        g_llm = await _resolve_one(client, llm_sci) if llm_sci else None

    inat_ru = (inat or {}).get("ru")
    strong = strong_match(name, inat_ru)
    king = (g_inat or {}).get("kingdom")
    king_ok = king in ("Plantae", "Fungi", "Chromista")
    mt = ((g_inat or {}).get("match_type") or "").upper()
    gbif_ok = mt in ("EXACT", "FUZZY") and ((g_inat or {}).get("confidence") or 0) >= 85
    k_inat, k_llm = (g_inat or {}).get("usage_key"), (g_llm or {}).get("usage_key")
    agree = bool(norm_bino(inat_sci)) and (
        norm_bino(inat_sci) == norm_bino(llm_sci) or (k_inat and k_llm and k_inat == k_llm))

    if inat_sci and king_ok and gbif_ok and (strong or agree):
        action, write = "auto_write", (g_inat or {}).get("canonical")
    elif inat_sci or llm_sci:
        action, write = "review", None
    else:
        action, write = "null_latin", None
    return {"name": name, "garbled": latin, "llm": llm_sci, "inat": inat_sci, "inat_ru": inat_ru,
            "king": king, "strong": strong, "agree": agree, "action": action, "write": write}


async def main():
    async with async_session() as db:
        rows = (await db.execute(text(
            f"SELECT id, name, name_latin, kingdom FROM plants WHERE {FILTER} ORDER BY random() LIMIT {SAMPLE}"
        ))).all()
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=30) as client:
        results = await asyncio.gather(*[process(client, sem, r) for r in rows])

    tally = {}
    print("=" * 100)
    for r in results:
        tally[r["action"]] = tally.get(r["action"], 0) + 1
        flags = f"strong={r['strong']} agree={r['agree']} king={r['king']}"
        print(f"[{r['action'].upper():10}] {r['name']}   ({flags})")
        print(f"             garbled : {r['garbled']}")
        print(f"             iNat     : {r['inat']}  (ru={r['inat_ru']})")
        print(f"             LLM      : {r['llm'] or '—'}")
        if r["write"]:
            print(f"             => WRITE : {r['write']}")
    print("=" * 100)
    print("TALLY:", tally)


if __name__ == "__main__":
    asyncio.run(main())
