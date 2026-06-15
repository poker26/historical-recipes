import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Per-process pool sizing. The heavy worker keeps the large pool (concurrency=8 +
# inner fan-out); lightweight processes (the dispatcher, the MCP server) set a
# small DB_POOL_SIZE via env so they don't each hold 20 idle CLIENT slots open
# against the Supabase pooler — those slots count toward its max_client_conn even
# though they don't pin a real PG backend, and saturating them makes the pooler
# reset connections mid-query ("connection closed in the middle of operation").
_pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # PgBouncer (transaction mode) doesn't support prepared statements
    pool_pre_ping=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=30,
    # Proactively retire connections before the pooler's idle/lifetime limit kills
    # them. pre_ping only catches a death at checkout; a long-lived connection that
    # passes SELECT 1 can still be reset by the pooler during the real query. A
    # 3-min recycle keeps connections younger than that limit.
    pool_recycle=180,
    connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
