# -*- coding: utf-8 -*-
"""PHASE 0 — convergence test (writes NOTHING). Two INDEPENDENT resolvers per card:
  A) iNat by the card's Russian name, constrained to the card's kingdom
     (iconic_taxa=Plantae/Fungi so homonym animals can't sneak in);
  B) LLM proposes the binomial from Russian name + OCR-garbled latin.
Both candidates are normalized through GBIF (usage_key catches synonyms). Auto only
when A and B AGREE on the same accepted taxon; otherwise review; nothing → null.
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
    "Ты ботаник-таксономист. Дана карточка растения из исторического русского источника: "
    "русское название, OCR-искажённая латынь (кириллические глифы вместо латинских) и "
    "исторические синонимы. Определи ПРИНЯТЫЙ научный бином (род+вид). Опирайся на русское "
    "название И на искажённую латынь; если виден конкретный вид — сохрани его, не подменяй "
    "на самый известный вид рода. Не уверен — \"UNKNOWN\". Не выдумывай. "
    "Строго JSON: {\"latin\": \"Genus species\"|\"UNKNOWN\"}."
)

_RU = re.compile(r"[а-яёa-z]+")
_LAT = re.compile(r"[A-Za-z]+")


def ruwords(s):
    return _RU.findall((s or "").lower().replace("ё", "е"))


def norm_bino(s):
    t = _LAT.findall(s or "")
    return " ".join(t[:2]).lower() if len(t) >= 2 else ""


def iconic_for(kingdom):
    return "Fungi" if (kingdom or "").startswith("гриб") else "Plantae"


async def inat_by_ru(client, name, iconic):
    qs = [name]
    w = ruwords(name)
    if len(w) >= 2:
        qs.append(" ".join(w[:2]))
    if w:
        qs.append(w[0])
    for q in qs:
        params = {"q": q, "locale": "ru", "per_page": 5, "is_active": "true", "iconic_taxa": iconic}
        try:
            resp = await client.get(f"{INAT_BASE}/taxa", params=params, headers=_HEADERS)
            if resp.status_code != 200:
                continue
            results = resp.json().get("results", [])
        except Exception:
            continue
        sp = [r for r in results if r.get("rank") == "species"]
        cand = sp[0] if sp else (results[0] if results else None)
        if cand:
            return {"sci": cand.get("name"), "ru": cand.get("preferred_common_name"),
                    "matched": cand.get("matched_term"), "rank": cand.get("rank")}
    return None


async def gbif_key(client, sci):
    if not sci:
        return None
    g = await _resolve_one(client, sci)
    return g


async def process(client, sem, row):
    pid, name, latin, kingdom = row
    iconic = iconic_for(kingdom)
    async with sem:
        # B) LLM
        try:
            user = json.dumps({"name": name, "garbled_latin": latin}, ensure_ascii=False)
            llm = await chat_completion_json(
                [{"role": "system", "content": SYS_FULL}, {"role": "user", "content": user}],
                task="lightweight", temperature=0.1, max_tokens=1800,
            )
            llm_sci = (llm.get("latin") or "").strip()
        except Exception:
            llm_sci = ""
        if llm_sci.upper() == "UNKNOWN":
            llm_sci = ""
        # A) iNat by Russian name
        inat = await inat_by_ru(client, name, iconic)
        inat_sci = (inat or {}).get("sci") if (inat and inat.get("rank") == "species") else None
        # normalize both through GBIF
        g_llm = await gbif_key(client, llm_sci) if llm_sci else None
        g_inat = await gbif_key(client, inat_sci) if inat_sci else None

    nb_llm, nb_inat = norm_bino(llm_sci), norm_bino(inat_sci)
    k_llm = (g_llm or {}).get("usage_key")
    k_inat = (g_inat or {}).get("usage_key")
    agree = bool(nb_llm) and bool(nb_inat) and (
        nb_llm == nb_inat or (k_llm and k_inat and k_llm == k_inat))
    # decide
    if agree:
        action = "auto_write"
    elif inat_sci or llm_sci:
        action = "review"
    else:
        action = "null_latin"
    canonical = (g_inat or g_llm or {}).get("canonical") if agree else None
    return {"name": name, "garbled": latin, "llm": llm_sci, "inat": inat_sci,
            "inat_ru": (inat or {}).get("ru"), "agree": agree, "action": action, "write": canonical}


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
        print(f"[{r['action'].upper():10}] {r['name']}")
        print(f"             garbled : {r['garbled']}")
        print(f"             LLM      : {r['llm'] or '—'}")
        print(f"             iNat     : {r['inat'] or '—'}  (ru={r['inat_ru']})")
        if r["write"]:
            print(f"             => WRITE : {r['write']}")
    print("=" * 100)
    print("TALLY:", tally)


if __name__ == "__main__":
    asyncio.run(main())
