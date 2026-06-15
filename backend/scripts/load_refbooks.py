# -*- coding: utf-8 -*-
"""One-off: load the downloaded REFERENCE books into the system WITHOUT processing.

Each file → uploaded to MinIO (books/{id}/original.{ext}) + a Book row with
status='wishlist' (PARKED — the dispatcher only picks 'uploaded'/intermediate, so
these never enter the pipeline until someone flips them to 'uploaded'). The old
title-only wishlist placeholders (file_path IS NULL) are removed — superseded.

Run INSIDE the backend container after `docker compose cp` of the files:
  docker compose cp /tmp/refbooks backend:/tmp/refbooks
  docker compose exec -T -e PYTHONPATH=/app backend python /tmp/load_refbooks.py
"""
import asyncio
import os
import uuid

from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.database import async_session
from app.models.book import Book, ProcessingLog
from app.services import minio as minio_svc

SRC = "/tmp/refbooks"

# filename -> (title, author, year, domain, source_format)
MAP = {
    "budantsev_al_otv_red_rastitelnye_resursy_rossii_i_sopredelny.pdf":
        ("Растительные ресурсы России и сопредельных государств", "Буданцев А.Л. (отв. ред.)", None, "herbalism", "pdf"),
    "sokolov_pd_otv_red_rastitelnye_resursy_rossii_i_sopredelnykh.pdf":
        ("Растительные ресурсы России и сопредельных государств", "Соколов П.Д. (отв. ред.)", None, "herbalism", "pdf"),
    "fedorov_aa_otv_red_rastitelnye_resursy_sssr_tsvetkovye_raste.pdf":
        ("Растительные ресурсы СССР. Цветковые растения, их химический состав, использование", "Фёдоров А.А. (отв. ред.)", None, "herbalism", "pdf"),
    "sokolov_pd_otv_red_rastitelnye_resursy_sssr_tsvetkovye_raste.pdf":
        ("Растительные ресурсы СССР. Цветковые растения (том 1)", "Соколов П.Д. (отв. ред.)", None, "herbalism", "pdf"),
    "sokolov_pd_otv_red_rastitelnye_resursy_sssr_tsvetkovye_raste (1).pdf":
        ("Растительные ресурсы СССР. Цветковые растения (том 2)", "Соколов П.Д. (отв. ред.)", None, "herbalism", "pdf"),
    "sokolov_pd_otv_red_rastitelnye_resursy_sssr_tsvetkovye_raste (2).pdf":
        ("Растительные ресурсы СССР. Цветковые растения (том 3)", "Соколов П.Д. (отв. ред.)", None, "herbalism", "pdf"),
    "sokolov_pd_otv_red_rastitelnye_resursy_sssr_tsvetkovye_raste (3).pdf":
        ("Растительные ресурсы СССР. Цветковые растения (том 4)", "Соколов П.Д. (отв. ред.)", None, "herbalism", "pdf"),
    "goriaev_mi_efirnye_masla_flory_sssr.pdf":
        ("Эфирные масла флоры СССР", "Горяев М.И.", None, "aromatherapy", "pdf"),
    "Войткевич С.А. Эфирные масла для парфюмерии и ароматерапии.doc":
        ("Эфирные масла для парфюмерии и ароматерапии", "Войткевич С.А.", None, "aromatherapy", "doc"),
    "gusynin_ia_toksikologiia_iadovitykh_rastenii_fitotoksikologi.pdf":
        ("Токсикология ядовитых растений. Фитотоксикология", "Гусынин И.А.", None, "reference", "pdf"),
    "mashkovskii_md_lekarstvennye_sredstva.pdf":
        ("Лекарственные средства", "Машковский М.Д.", None, "reference", "pdf"),
    "ministerstvo_zdravoohranenija_sssr_gosudarstvennaja_farmakop.doc":
        ("Государственная фармакопея СССР, XI издание (часть 1)", "Минздрав СССР", None, "reference", "doc"),
    "ministerstvo_zdravoohranenija_sssr_gosudarstvennaja_farmakop (1).doc":
        ("Государственная фармакопея СССР, XI издание (часть 2)", "Минздрав СССР", None, "reference", "doc"),
    "orekhov_ap_khimiia_alkaloidov.pdf":
        ("Химия алкалоидов растений СССР", "Орехов А.П.", None, "reference", "pdf"),
    "plemenkov_vv_vvedenie_v_khimiiu_prirodnykh_soedinenii.pdf":
        ("Введение в химию природных соединений", "Племенков В.В.", None, "reference", "pdf"),
    "volynets_ap_fenolnye_soedineniia_v_zhiznedeiatelnosti_rasten.pdf":
        ("Фенольные соединения в жизнедеятельности растений", "Волынец А.П.", None, "reference", "pdf"),
    "Sokolov_S_Ya_Fitoterapia_i_fitofarmakologia2000.pdf":
        ("Фитотерапия и фитофармакология. Руководство для врачей", "Соколов С.Я.", 2000, "herbalism", "pdf"),
}
# Skipped: the .zip (unsupported + duplicate of Войткевич .doc) and the .djvu
# (duplicate of the same Соколов tsvetkovye .pdf).
SKIP = {
    "voitkevich_sa_efirnye_masla_dlia_parfiumerii_i_aromaterapii.zip",
    "sokolov_pd_otv_red_rastitelnye_resursy_sssr_tsvetkovye_raste.djvu",
}
CTYPE = {"pdf": "application/pdf", "djvu": "image/vnd.djvu", "doc": "application/msword"}


async def main():
    # 1) drop the title-only placeholders (no file) — superseded by real loads
    async with async_session() as db:
        r = await db.execute(text(
            "DELETE FROM books WHERE status='wishlist' AND file_path IS NULL RETURNING title"))
        dropped = [row[0] for row in r.fetchall()]
        await db.commit()
    print(f"dropped {len(dropped)} placeholder(s)", flush=True)

    created = skipped = 0
    for fn in sorted(os.listdir(SRC)):
        if fn in SKIP:
            print(f"SKIP {fn}", flush=True)
            continue
        if fn not in MAP:
            print(f"WARN no mapping, skipping: {fn}", flush=True)
            continue
        title, author, year, domain, sfmt = MAP[fn]

        async with async_session() as db:
            exists = (await db.execute(text(
                "SELECT 1 FROM books WHERE title=:t AND file_path IS NOT NULL LIMIT 1"),
                {"t": title})).first()
        if exists:
            print(f"already loaded: {title}", flush=True)
            skipped += 1
            continue

        with open(os.path.join(SRC, fn), "rb") as fh:
            content = fh.read()
        bid = uuid.uuid4()
        ext = sfmt
        file_path = f"books/{bid}/original.{ext}"
        await run_in_threadpool(minio_svc.upload_file, content, file_path,
                                content_type=CTYPE.get(sfmt, "application/octet-stream"))
        async with async_session() as db:
            db.add(Book(id=bid, title=title, author=author, year=year, domain=domain,
                        source_format=sfmt, status="wishlist", wizard_step=1,
                        file_path=file_path))
            db.add(ProcessingLog(book_id=bid, step="upload", status="completed",
                                 details={"source_format": sfmt, "bytes": len(content),
                                          "parked": True, "filename": fn}))
            await db.commit()
        created += 1
        print(f"LOADED [{domain}/{sfmt}] {title}  ({len(content)//1024} KB)", flush=True)

    print(f"DONE: created={created} skipped={skipped}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
