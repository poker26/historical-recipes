"""Миграция выданных значков мест на кумулятивную модель «Знаток места» (2026-08-24).

Было: badge_id = "{place_id}:{window}:{year}" — значок на место×месяц×год, прогресс
сгорал на смене окна (курильский кейс). Стало: badge_id = "{place_id}:all" — один
кумулятивный значок на место, ярусы навсегда.

Что делает (идемпотентно):
1. Печатает дамп затрагиваемых строк (аудит — вставить в лог сессии).
2. Для каждой (device, place, tier) группы старых оконных строк оставляет САМУЮ РАННЮЮ
   (честная дата достижения), остальные удаляет (дубли яруса по разным месяцам).
3. Переписывает badge_id оставшихся на "{place_id}:all".
4. Пересчитывает ordinal внутри (badge_id, tier) по issued_at (редкость «ты #N»).
Биотопные значки (badge_id LIKE 'biotope:%') не трогает.
"""
import asyncio
import json

from sqlalchemy import text

from app.database import async_session


async def main():
    async with async_session() as s:
        rows = (await s.execute(text("""
            SELECT id, badge_id, device_key::text, tier, points, ordinal, issued_at::text
            FROM quest_issued_badges
            WHERE badge_id NOT LIKE 'biotope:%' AND badge_id NOT LIKE '%\:all'
            ORDER BY issued_at"""))).all()
        print(f"оконных строк к миграции: {len(rows)}")
        print(json.dumps([list(map(str, r)) for r in rows], ensure_ascii=False))
        if not rows:
            print("нечего мигрировать")
            return
        # 2. дедуп: внутри (device, place, tier) выживает самая ранняя строка
        deleted = (await s.execute(text("""
            DELETE FROM quest_issued_badges b
            USING quest_issued_badges keep
            WHERE b.badge_id NOT LIKE 'biotope:%' AND b.badge_id NOT LIKE '%\:all'
              AND keep.badge_id NOT LIKE 'biotope:%' AND keep.badge_id NOT LIKE '%\:all'
              AND split_part(b.badge_id, ':', 1) = split_part(keep.badge_id, ':', 1)
              AND b.device_key = keep.device_key AND b.tier = keep.tier
              AND (keep.issued_at < b.issued_at
                   OR (keep.issued_at = b.issued_at AND keep.id < b.id))
            RETURNING b.id"""))).rowcount
        # 3. переписать id на кумулятивный
        updated = (await s.execute(text("""
            UPDATE quest_issued_badges
            SET badge_id = split_part(badge_id, ':', 1) || '\:all'
            WHERE badge_id NOT LIKE 'biotope:%' AND badge_id NOT LIKE '%\:all'
            RETURNING id"""))).rowcount
        # 4. пересчёт ordinal по порядку достижения
        await s.execute(text("""
            UPDATE quest_issued_badges b
            SET ordinal = z.rn
            FROM (SELECT id, row_number() OVER (
                      PARTITION BY badge_id, tier ORDER BY issued_at, id) AS rn
                  FROM quest_issued_badges WHERE badge_id LIKE '%\:all') z
            WHERE b.id = z.id AND b.ordinal IS DISTINCT FROM z.rn"""))
        await s.commit()
        print(f"удалено дублей: {deleted}, переписано: {updated}")
        chk = (await s.execute(text("""
            SELECT badge_id, tier, count(*), array_agg(ordinal ORDER BY ordinal)
            FROM quest_issued_badges WHERE badge_id LIKE '%\:all'
            GROUP BY badge_id, tier ORDER BY badge_id, tier"""))).all()
        for bid, tier, n, ords in chk:
            print(f"  {bid} tier={tier}: {n} шт, ordinals={ords}")


asyncio.run(main())
