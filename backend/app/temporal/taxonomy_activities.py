# -*- coding: utf-8 -*-
"""Durable OCR of the Cherepanov 1995 nomenclatural checklist (Plantae Vasculares
Rossicae) into the taxonomy backbone — `taxon_backbone` (accepted taxa) +
`taxon_synonym` (synonym → accepted). The spine is the EXTERNAL TRUTH the plant
identity cleanup needs (synonymy + accepted names + family/genus taxonomy).

The PDF is a 991-page scan with NO text layer → each page is rendered (PyMuPDF) and
read by the vision model (Qwen3-VL, task=ocr_hard). Idempotent per `source_page`:
a page already present is skipped (resume after a worker restart), or replaced when
`force`. Concurrency = `_SEM` VL calls; heartbeats per page so a slow batch can't
blow the 15-min heartbeat timeout. PDF lives in MinIO so the worker renders locally.
"""
import asyncio
import base64
import json
import re
import uuid

import fitz
from sqlalchemy import text
from temporalio import activity

from app.config import settings
from app.database import async_session
from app.services import minio as minio_svc
from app.services.llm import chat_completion
from app.services.plant_matching import _latin_key

_SEM = 8   # concurrent vision calls inside one batch activity

_DDL = [
    """CREATE TABLE IF NOT EXISTS taxon_backbone (
       id uuid PRIMARY KEY, family text, genus text, species text, author text,
       rank text DEFAULT 'species', accepted_key text,
       source text DEFAULT 'cherepanov-1995', source_page int, created_at timestamptz DEFAULT now())""",
    "CREATE INDEX IF NOT EXISTS ix_tb_key ON taxon_backbone(accepted_key)",
    "CREATE INDEX IF NOT EXISTS ix_tb_genus ON taxon_backbone(genus)",
    "CREATE INDEX IF NOT EXISTS ix_tb_page ON taxon_backbone(source_page)",
    """CREATE TABLE IF NOT EXISTS taxon_synonym (
       id uuid PRIMARY KEY, syn_key text, syn_latin text, author text,
       accepted_key text, source_page int, created_at timestamptz DEFAULT now())""",
    "CREATE INDEX IF NOT EXISTS ix_ts_key ON taxon_synonym(syn_key)",
    "CREATE INDEX IF NOT EXISTS ix_ts_page ON taxon_synonym(source_page)",
]

_PROMPT = """Это страница из номенклатурного свода Черепанова «Сосудистые растения России» (латынь).
Формат: ЗАГЛАВНЫМИ — семейство; жирным — род; виды «эпитет Автор(ы)»; строки с «=» — синонимы к принятому имени.
Извлеки КАЖДЫЙ таксон в JSON-массив, поля:
{"family":"<текущее семейство CAPS>","genus":"<род полностью>","species":"<видовой эпитет>","author":"<авторы или null>","type":"accepted"|"synonym","accepted_name":"<если synonym — принятое имя как напечатано, иначе null>"}
Только JSON-массив. Игнорируй чисто латинские ремарки вроде «cum auct.», «p.p.», «s.l.», «s.str.»."""

_INS_ACC = text("INSERT INTO taxon_backbone(id,family,genus,species,author,accepted_key,source_page) "
                "VALUES(:i,:f,:g,:s,:a,:k,:p)")
_INS_SYN = text("INSERT INTO taxon_synonym(id,syn_key,syn_latin,author,accepted_key,source_page) "
                "VALUES(:i,:k,:l,:a,:ak,:p)")


def _parse_arr(out: str):
    s = out.find("[")
    if s < 0:
        return None
    e = out.rfind("]")
    if e > s:
        try:
            return json.loads(out[s:e + 1])
        except Exception:
            pass
    # Salvage a TRUNCATED array (dense page hit max_tokens mid-JSON): close it after
    # the last complete object so we keep all but the final partial entry.
    frag = out[s:]
    last = frag.rfind("}")
    if last > 0:
        try:
            return json.loads(frag[:last + 1] + "]")
        except Exception:
            return None
    return None


def _expand(acc: str | None, genus: str) -> str | None:
    """Expand a genus-abbreviated accepted name («A. pubescens» → «Acer pubescens»)
    using the synonym's own genus (correct for same-genus synonyms, the majority)."""
    if not acc:
        return None
    m = re.match(r"^([A-Z])\.\s+(.+)$", acc.strip())
    if m and genus:
        return f"{genus} {m.group(2)}"
    return acc


async def _ocr_page(img_png: bytes) -> list:
    b64 = base64.b64encode(img_png).decode()
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": _PROMPT},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]
    out = await chat_completion(msgs, task="ocr_hard", temperature=0.1, max_tokens=20000)
    return _parse_arr(out) or []


@activity.defn
async def cherepanov_ocr_activity(minio_key: str, page_from: int, page_to: int,
                                  dpi: int = 220, force: bool = False) -> dict:
    """OCR pages [page_from, page_to) of the Cherepanov PDF in MinIO → backbone/synonym.
    Idempotent per source_page (skip already-present unless force). Returns counts."""
    async with async_session() as db:
        for d in _DDL:
            await db.execute(text(d))
        await db.commit()
        done = {r[0] for r in (await db.execute(text(
            "SELECT DISTINCT source_page FROM taxon_backbone "
            "WHERE source_page >= :a AND source_page < :b"),
            {"a": page_from, "b": page_to})).all()}

    pdf_bytes = minio_svc.download_file(minio_key)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    targets = [p for p in range(page_from, page_to) if force or p not in done]
    skipped = (page_to - page_from) - len(targets)
    sem = asyncio.Semaphore(_SEM)
    acc_n = syn_n = fail = 0

    async def handle(pg: int):
        nonlocal acc_n, syn_n, fail
        async with sem:
            try:
                img = doc[pg].get_pixmap(dpi=dpi).tobytes("png")
                arr = await _ocr_page(img)
            except Exception as e:
                fail += 1
                activity.logger.warning("cherepanov page %d failed: %s", pg, str(e)[:120])
                return
            recs_acc, recs_syn = [], []
            for d in arr:
                if not isinstance(d, dict):
                    continue
                g = (d.get("genus") or "").strip()
                sp = (d.get("species") or "").strip()
                if not g or not sp:
                    continue
                full = f"{g} {sp}"
                if d.get("type") == "synonym":
                    recs_syn.append({"i": str(uuid.uuid4()), "k": _latin_key(full), "l": full,
                                     "a": d.get("author"),
                                     "ak": _latin_key(_expand(d.get("accepted_name"), g) or ""), "p": pg})
                else:
                    recs_acc.append({"i": str(uuid.uuid4()), "f": d.get("family"), "g": g, "s": sp,
                                     "a": d.get("author"), "k": _latin_key(full), "p": pg})
            async with async_session() as db:
                if force:
                    await db.execute(text("DELETE FROM taxon_backbone WHERE source_page=:p"), {"p": pg})
                    await db.execute(text("DELETE FROM taxon_synonym WHERE source_page=:p"), {"p": pg})
                for r in recs_acc:
                    await db.execute(_INS_ACC, r)
                for r in recs_syn:
                    await db.execute(_INS_SYN, r)
                await db.commit()
            acc_n += len(recs_acc)
            syn_n += len(recs_syn)
            activity.heartbeat({"page": pg, "accepted": acc_n, "synonyms": syn_n, "fail": fail})

    await asyncio.gather(*[handle(p) for p in targets])
    return {"page_from": page_from, "page_to": page_to, "accepted": acc_n,
            "synonyms": syn_n, "skipped": skipped, "failed": fail}
