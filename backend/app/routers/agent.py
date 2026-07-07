"""Consumer agent endpoint for nastoiki.pro — `POST /api/agent/chat` (SSE).

Thin transport layer: validates the request, applies a per-client rate-limit
backstop, then streams the agent loop (app/services/agent.py) to the client as
Server-Sent Events. The server is STATELESS — the client holds the conversation
history and sends it every turn (no accounts, matching the rest of the consumer
surface). Guardrails (topic-gate, token/round caps, persona/scope) live in the
service; here we only add the request-rate backstop and usage logging.
"""

import asyncio
import json
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text as _sql

from app.database import async_session, engine
from app.services import agent

logger = logging.getLogger("agent.router")

router = APIRouter()

_log_ready = False


async def _log_exchange(device_key: str, question: str, answer: str) -> None:
    """Fire-and-forget: record what people ask the agent (anonymous, by device).
    Never blocks or breaks the response — best-effort analytics."""
    global _log_ready
    try:
        if not _log_ready:
            async with engine.begin() as conn:
                await conn.execute(_sql("""
                    CREATE TABLE IF NOT EXISTS agent_queries (
                      id         BIGSERIAL PRIMARY KEY,
                      device_key TEXT,
                      question   TEXT,
                      answer     TEXT,
                      created_at TIMESTAMPTZ DEFAULT now()
                    )"""))
            _log_ready = True
        async with async_session() as s:
            await s.execute(_sql(
                "INSERT INTO agent_queries (device_key, question, answer) "
                "VALUES (:k, :q, :a)"),
                {"k": device_key[:64], "q": question[:2000], "a": (answer or "")[:8000]})
            await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"query log failed: {e}")

# ── Rate-limit backstop (in-memory; resets on restart) ───────────────────────
# The real cost lever is the topic-gate; this just caps abuse/runaway clients.
_RL_WINDOW_S = 3600
_RL_MAX = 40                      # messages per key per hour
_hits: dict[str, deque] = defaultdict(deque)

_MAX_MESSAGES = 40                # reject absurdly long histories
_MAX_CHARS = 24000                # …and absurdly large payloads

_RU_MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)
    location: str | None = None


def _rate_limited(key: str) -> bool:
    now = time.monotonic()
    dq = _hits[key]
    while dq and now - dq[0] > _RL_WINDOW_S:
        dq.popleft()
    if len(dq) >= _RL_MAX:
        return True
    dq.append(now)
    return False


def _today_ru() -> str:
    from datetime import datetime
    d = datetime.now()
    return f"{d.day} {_RU_MONTHS[d.month - 1]} {d.year} года (сейчас {_RU_MONTHS[d.month - 1]})"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(
    req: ChatRequest,
    request: Request,
    x_device_id: str | None = Header(default=None),
):
    """Stream one assistant turn as SSE. Body: {messages:[{role,content}], location?}."""
    key = x_device_id or (request.client.host if request.client else "anon")

    # Validate / bound the payload.
    history = [{"role": m.role, "content": m.content} for m in req.messages
               if m.role in ("user", "assistant") and m.content]
    total_chars = sum(len(m["content"]) for m in history)
    if not history or history[-1]["role"] != "user":
        return StreamingResponse(
            iter([_sse({"type": "error", "text": "Кажется, вопрос не дошёл. Напишите его, пожалуйста, ещё раз."}), _sse({"type": "done"})]),
            media_type="text/event-stream",
        )
    if len(history) > _MAX_MESSAGES or total_chars > _MAX_CHARS:
        history = history[-_MAX_MESSAGES:]

    if _rate_limited(key):
        logger.info(f"agent: rate-limited key={key[:16]}")
        return StreamingResponse(
            iter([
                _sse({"type": "delta", "text": "Не так быстро, друг мой. 😊 Дайте настойке настояться и возвращайтесь чуть позже."}),
                _sse({"type": "done"}),
            ]),
            media_type="text/event-stream",
        )

    today = _today_ru()
    logger.info(f"agent chat: key={key[:16]} msgs={len(history)} loc={req.location!r}")

    question = history[-1]["content"]

    async def gen():
        answer_parts: list[str] = []
        try:
            async for event in agent.run_agent(history, today=today, location=req.location):
                if event.get("type") == "delta":
                    answer_parts.append(event.get("text", ""))
                yield _sse(event)
        except Exception as e:  # noqa: BLE001 — never leak a stack trace to the stream
            logger.error(f"agent run failed: {e}")
            yield _sse({"type": "error", "text": "Что-то пошло не так. Повторите, пожалуйста."})
            yield _sse({"type": "done"})
        finally:
            asyncio.create_task(_log_exchange(key, question, "".join(answer_parts)))

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
