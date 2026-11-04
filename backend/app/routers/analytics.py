"""Продуктовая аналитика «Что растёт» (RFC-v2 §4): first-party приём событий клиента.

Контур ОТДЕЛЬНЫЙ от полевых данных: сюда не попадают фото, EXIF, точные координаты,
device_key и handle — только псевдонимный analytics_device_id, экраны, действия и
active_ms. Клиент шлёт батчи offline-first очереди; event_id даёт идемпотентность
(повторная доставка батча не плодит дубли). Схема хранения — RFC-драфт §15.3.

Таблица создаётся лениво (CREATE IF NOT EXISTS на первом батче) — тот же паттерн, что
`leads`/`agent_queries`: прод-alembic диверджнут, лишний head там не нужен.
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BATCH = 200          # событий на запрос
MAX_PROPS_CHARS = 4000   # сериализованные properties одного события
_TEXT_CAP = 128          # любые строковые идентификаторы

_DDL = """
CREATE TABLE IF NOT EXISTS product_events (
    event_id uuid PRIMARY KEY,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    analytics_device_id text NOT NULL,
    session_id uuid,
    event_name text NOT NULL,
    screen_id text,
    screen_instance_id uuid,
    previous_screen_id text,
    entry_point text,
    layout_version int,
    app_version text,
    platform text,
    schema_version int NOT NULL DEFAULT 1,
    properties jsonb
);
CREATE INDEX IF NOT EXISTS product_events_name_time ON product_events (event_name, occurred_at);
CREATE INDEX IF NOT EXISTS product_events_device_time ON product_events (analytics_device_id, occurred_at);
CREATE INDEX IF NOT EXISTS product_events_screen ON product_events (screen_id);
"""
_ddl_done = False


async def _ensure_table(db: AsyncSession) -> None:
    global _ddl_done
    if _ddl_done:
        return
    for stmt in _DDL.strip().split(";"):
        if stmt.strip():
            await db.execute(text(stmt))
    await db.commit()
    _ddl_done = True


class AnalyticsEvent(BaseModel):
    event_id: str
    event_name: str = Field(max_length=_TEXT_CAP)
    occurred_at: str
    device_id: str = Field(max_length=_TEXT_CAP)      # псевдоним, НЕ device_key
    session_id: str | None = None
    screen_id: str | None = Field(default=None, max_length=_TEXT_CAP)
    screen_instance_id: str | None = None
    previous_screen_id: str | None = Field(default=None, max_length=_TEXT_CAP)
    entry_point: str | None = Field(default=None, max_length=_TEXT_CAP)
    layout_version: int | None = None
    app_version: str | None = Field(default=None, max_length=32)
    platform: str | None = Field(default=None, max_length=16)
    properties: dict = Field(default_factory=dict)


class EventBatch(BaseModel):
    schema_version: int = 1
    events: list[AnalyticsEvent]


_CLOCK_FLOOR = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _parse_ts(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


@router.post("/events/batch")
async def ingest_batch(batch: EventBatch, db: AsyncSession = Depends(get_db)):
    """Принять батч событий. Идемпотентно по event_id (повтор → rejected растёт, но
    данные не дублируются). Сбой одного события не роняет батч."""
    await _ensure_table(db)
    now = datetime.now(timezone.utc)
    accepted = rejected = 0
    # iOS до 2.4.x брал время из kotlin.system.getTimeMillis(), а это счётчик от
    # произвольной точки, не время с 1970 года: все события iOS приходили датой
    # 1970-01-xx (замер 26.10.2026, 1534 события у 161 устройства). Разница между
    # такими отметками верная, поэтому событие ставим относительно последнего в
    # батче, а батч отправляется сразу после событий. Исходная отметка остаётся в
    # свойствах.
    parsed = [_parse_ts(ev.occurred_at) for ev in batch.events[:MAX_BATCH]]
    bogus = [t for t in parsed if t is not None and t < _CLOCK_FLOOR]
    newest_bogus = max(bogus) if bogus else None
    for ev, ts in zip(batch.events[:MAX_BATCH], parsed):
        if ts is None:
            rejected += 1
            continue
        props = ev.properties or {}
        if ts < _CLOCK_FLOOR:
            props = {**props, "client_ts_raw": ev.occurred_at}
            ts = max(now - (newest_bogus - ts), now - timedelta(days=7))
        # события «из будущего» (кривые часы) клампим к серверному времени
        if ts > now:
            ts = now
        import json as _json
        if len(_json.dumps(props, ensure_ascii=False)) > MAX_PROPS_CHARS:
            props = {"_truncated": True}
        try:
            r = await db.execute(text("""
                INSERT INTO product_events (event_id, occurred_at, analytics_device_id,
                    session_id, event_name, screen_id, screen_instance_id,
                    previous_screen_id, entry_point, layout_version, app_version,
                    platform, schema_version, properties)
                VALUES (CAST(:eid AS uuid), :ts, :dev, CAST(:sid AS uuid), :name,
                        :screen, CAST(:sinst AS uuid), :prev, :entry, :layout,
                        :appv, :plat, :schema, CAST(:props AS jsonb))
                ON CONFLICT (event_id) DO NOTHING
            """), {
                "eid": ev.event_id, "ts": ts, "dev": ev.device_id,
                "sid": ev.session_id, "name": ev.event_name,
                "screen": ev.screen_id, "sinst": ev.screen_instance_id,
                "prev": ev.previous_screen_id, "entry": ev.entry_point,
                "layout": ev.layout_version, "appv": ev.app_version,
                "plat": ev.platform, "schema": batch.schema_version,
                "props": _json.dumps(props, ensure_ascii=False),
            })
            if r.rowcount:
                accepted += 1
            else:
                rejected += 1   # дубль event_id — уже приняли раньше
        except Exception as e:
            rejected += 1
            await db.rollback()
            logger.warning(f"analytics event rejected: {type(e).__name__}: {e}")
    rejected += max(0, len(batch.events) - MAX_BATCH)
    await db.commit()
    return {"accepted": accepted, "rejected": rejected,
            "server_time": now.isoformat()}
