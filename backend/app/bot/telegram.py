"""Telegram bot for @neprostoinastoi_bot — nastoiki.pro lead magnet + agent.

Built from scratch (long-polling, no framework — just httpx). Two jobs:
  1. /start <payload>  → capture the contact into `leads`, send the gift book PDF.
  2. any text message  → proxy to the site agent (POST /api/agent/chat) and reply.

Runs as its own container reusing the backend image (like worker / mcp). Token
comes from the NASTOIKI_BOT_TOKEN env var (never committed).
"""

import asyncio
import html as htmllib
import json
import logging
import os
import re

import httpx
from sqlalchemy import text as sql

from app.config import settings
from app.database import async_session, engine
from app.services import minio

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nastoiki.bot")

TOKEN = os.environ.get("NASTOIKI_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"
BACKEND = settings.internal_api_url.rstrip("/")

# Gift books (real pre-1917 titles from the corpus). Keyed by the deep-link
# payload used on the site: t.me/neprostoinastoi_bot?start=book1858
GIFT_BOOKS = {
    "book1858": {
        "path": "books/9db759fc-7fea-4c42-becd-a8d08e27da69/original.pdf",
        "filename": "50-sposobov-nastaivat-1858.pdf",
        "caption": "📖 Держите подарок — «50 малороссийских способов настаивать», подлинное "
                   "издание 1858 года. Настоящие старинные рецепты, прямо из нашей библиотеки. "
                   "Приятного чтения!",
    },
}
DEFAULT_GIFT = "book1858"

WELCOME = (
    "Здравствуйте! Это бот проекта «Корни, Травы, Дистиллят». 😊\n\n"
    "Я — мастер старинных русских настоек. Спросите меня про любой рецепт, траву или "
    "коренёк, и я расскажу по дореволюционным книгам: что за напиток, зачем в нём каждый "
    "ингредиент, с чем его сочетать и когда собирать травы.\n\n"
    "А приехать и сделать настойку своими руками можно на мастер-классе в Пронино — "
    "расписание на нашем сайте nastoiki.pro."
)

# In-memory per-chat history (the agent is stateless; we carry a short context).
HISTORY: dict[int, list] = {}
MAX_TURNS = 12
TG_LIMIT = 4000  # keep under Telegram's 4096 with margin


# ─────────────────────────────── DB (leads) ───────────────────────────────

async def ensure_leads_table() -> None:
    async with engine.begin() as conn:
        await conn.execute(sql("""
            CREATE TABLE IF NOT EXISTS leads (
              id          BIGSERIAL PRIMARY KEY,
              source      TEXT NOT NULL,
              tg_id       BIGINT,
              tg_username TEXT,
              name        TEXT,
              email       TEXT,
              gift        TEXT,
              payload     TEXT,
              created_at  TIMESTAMPTZ DEFAULT now(),
              updated_at  TIMESTAMPTZ DEFAULT now(),
              UNIQUE (source, tg_id)
            )"""))
    log.info("leads table ensured")


async def save_lead(tg_id, username, name, gift, payload) -> None:
    try:
        async with async_session() as s:
            await s.execute(sql("""
                INSERT INTO leads (source, tg_id, tg_username, name, gift, payload)
                VALUES ('tg_bot', :tg_id, :u, :n, :g, :p)
                ON CONFLICT (source, tg_id) DO UPDATE SET
                    tg_username = EXCLUDED.tg_username,
                    name        = EXCLUDED.name,
                    gift        = COALESCE(EXCLUDED.gift, leads.gift),
                    payload     = EXCLUDED.payload,
                    updated_at  = now()
            """), {"tg_id": tg_id, "u": username, "n": name, "g": gift, "p": payload})
            await s.commit()
        log.info(f"lead saved tg_id={tg_id} gift={gift}")
    except Exception as e:  # noqa: BLE001 — a lead write must never break the reply
        log.error(f"save_lead failed: {e}")


# ─────────────────────────────── Telegram I/O ───────────────────────────────

async def tg(method: str, **params):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/{method}", json=params)
        return r.json()


def _md_to_html(text: str) -> str:
    """Convert the agent's Markdown into the small HTML subset Telegram renders."""
    t = htmllib.escape(text)
    t = re.sub(r"```(?:\w+)?\n?(.+?)```", r"<pre>\1</pre>", t, flags=re.S)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)      # drop blockquote markers
    t = re.sub(r"^\s*[-*•]\s+", "• ", t, flags=re.M)  # bullets
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)       # headings → plain
    return t


def _chunks(text: str):
    while len(text) > TG_LIMIT:
        cut = text.rfind("\n", 0, TG_LIMIT)
        if cut < TG_LIMIT // 2:
            cut = TG_LIMIT
        yield text[:cut]
        text = text[cut:]
    if text:
        yield text


async def send_message(chat_id: int, text: str, html: bool = False, **kw):
    for chunk in _chunks(text):
        res = await tg("sendMessage", chat_id=chat_id, text=chunk,
                       parse_mode="HTML" if html else None,
                       disable_web_page_preview=True, **kw)
        # If Telegram rejects our HTML (rare parse edge case), resend as plain text
        # so the answer never silently disappears.
        if html and not res.get("ok"):
            log.warning(f"HTML send rejected ({res.get('description')}); retrying plain")
            plain = re.sub(r"<[^>]+>", "", chunk)
            await tg("sendMessage", chat_id=chat_id, text=htmllib.unescape(plain),
                     disable_web_page_preview=True, **kw)


async def send_book(chat_id: int, key: str) -> None:
    book = GIFT_BOOKS.get(key)
    if not book:
        return
    try:
        data = await asyncio.to_thread(minio.download_file, book["path"])
    except Exception as e:  # noqa: BLE001
        log.error(f"book download failed: {e}")
        await send_message(chat_id, "Книга сейчас недоступна — починю и пришлю, извините. 🙏")
        return
    async with httpx.AsyncClient(timeout=180) as c:
        await c.post(f"{API}/sendDocument",
                     data={"chat_id": chat_id, "caption": book["caption"]},
                     files={"document": (book["filename"], data, "application/pdf")})
    log.info(f"book {key} sent to {chat_id}")


# ─────────────────────────────── Agent proxy ───────────────────────────────

async def ask_agent(chat_id: int, user_text: str) -> str:
    hist = HISTORY.setdefault(chat_id, [])
    hist.append({"role": "user", "content": user_text})
    del hist[:-MAX_TURNS]
    answer = ""
    try:
        async with httpx.AsyncClient(timeout=200) as c:
            async with c.stream("POST", f"{BACKEND}/api/agent/chat",
                                 headers={"X-Device-Id": f"tg{chat_id}"},
                                 json={"messages": hist}) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") in ("delta", "error"):
                        answer += ev.get("text", "")
    except Exception as e:  # noqa: BLE001
        log.error(f"agent proxy failed: {e}")
    if answer:
        hist.append({"role": "assistant", "content": answer})
    return answer or "Прошу прощения, с ходу не вышло ответить. Задайте вопрос ещё раз, пожалуйста. 😊"


# ─────────────────────────────── Dispatch ───────────────────────────────

def _full_name(u: dict) -> str:
    return " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or None


async def handle_message(msg: dict) -> None:
    chat_id = msg["chat"]["id"]
    user = msg.get("from") or {}
    text = (msg.get("text") or "").strip()

    if text.startswith("/start"):
        payload = text[len("/start"):].strip()
        gift = payload if payload in GIFT_BOOKS else None
        await save_lead(user.get("id"), user.get("username"), _full_name(user), gift, payload or None)
        await send_message(chat_id, WELCOME)
        if gift:
            await send_book(chat_id, gift)
        else:
            await tg("sendMessage", chat_id=chat_id,
                     text="🎁 И вот вам подарок — настоящая книга старинных рецептов 1858 года:",
                     reply_markup={"inline_keyboard": [[
                         {"text": "📖 Прислать книгу", "callback_data": f"gift:{DEFAULT_GIFT}"}]]})
        return

    if text.startswith("/"):
        await send_message(chat_id, "Просто напишите мне вопрос про настойки, травы или рецепты 😊")
        return

    if not text:
        await send_message(chat_id, "Напишите текстом — про какую настойку, траву или рецепт рассказать? 😊")
        return

    await tg("sendChatAction", chat_id=chat_id, action="typing")
    answer = await ask_agent(chat_id, text)
    await send_message(chat_id, _md_to_html(answer), html=True)


async def handle_callback(cq: dict) -> None:
    await tg("answerCallbackQuery", callback_query_id=cq["id"])
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    user = cq.get("from") or {}
    if data.startswith("gift:") and chat_id:
        key = data.split(":", 1)[1]
        await save_lead(user.get("id"), user.get("username"), _full_name(user), key, "button")
        await send_book(chat_id, key)


async def handle_update(up: dict) -> None:
    try:
        if "message" in up:
            await handle_message(up["message"])
        elif "callback_query" in up:
            await handle_callback(up["callback_query"])
    except Exception as e:  # noqa: BLE001 — one bad update must not kill the loop
        log.error(f"handle_update error: {e}")


async def main() -> None:
    if not TOKEN:
        log.error("NASTOIKI_BOT_TOKEN not set — bot idle")
        while True:
            await asyncio.sleep(3600)
    await ensure_leads_table()
    await tg("deleteWebhook")  # long-polling needs the webhook off
    me = await tg("getMe")
    log.info(f"bot online: @{me.get('result', {}).get('username')}")
    offset = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as c:
                r = await c.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 30})
                data = r.json()
            for up in data.get("result", []):
                offset = up["update_id"] + 1
                asyncio.create_task(handle_update(up))
        except Exception as e:  # noqa: BLE001
            log.warning(f"poll error: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
