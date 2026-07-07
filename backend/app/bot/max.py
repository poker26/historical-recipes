"""Max bot for nastoiki.pro — same lead magnet + agent as the Telegram bot,
on the Max messenger (platform-api.max.ru, TamTam-style Bot API).

Long-polls /updates. Reacts ONLY in private dialogs (channels/groups ignored —
the bot token is shared with other outbound integrations, we must not answer in
their chats). bot_started → lead + gift book; a dialog message → the site agent.
"""

import asyncio
import json
import logging
import os
import re

import httpx

from app.config import settings
from app.database import async_session
from app.services import minio
from app.bot.telegram import GIFT_BOOKS, WELCOME, DEFAULT_GIFT, ensure_leads_table

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nastoiki.max")

BASE = os.environ.get("MAX_API_BASE_URL", "https://platform-api.max.ru")
TOKEN = os.environ.get("MAX_BOT_TOKEN", "")
BACKEND = settings.internal_api_url.rstrip("/")

HISTORY: dict[int, list] = {}
MAX_TURNS = 12
LIMIT = 3800


# ─────────────────────────────── DB ───────────────────────────────

async def save_lead(chat_id, user: dict, gift, payload) -> None:
    try:
        from sqlalchemy import text as sql
        name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])) or user.get("name")
        async with async_session() as s:
            await s.execute(sql("""
                INSERT INTO leads (source, tg_id, tg_username, name, gift, payload)
                VALUES ('max_bot', :id, :u, :n, :g, :p)
                ON CONFLICT (source, tg_id) DO UPDATE SET
                    tg_username = EXCLUDED.tg_username, name = EXCLUDED.name,
                    gift = COALESCE(EXCLUDED.gift, leads.gift),
                    payload = COALESCE(EXCLUDED.payload, leads.payload), updated_at = now()
            """), {"id": user.get("user_id"), "u": user.get("username"), "n": name, "g": gift, "p": payload})
            await s.commit()
    except Exception as e:  # noqa: BLE001
        log.error(f"save_lead failed: {e}")


async def grant_book_if_new(user_id, gift) -> bool:
    """Atomically stamp the gift on the lead if not yet given. Returns True the
    FIRST time only — so the book is sent exactly once per user, surviving
    restarts (Max doesn't reliably fire bot_started, so we grant on first contact)."""
    try:
        from sqlalchemy import text as sql
        async with async_session() as s:
            row = (await s.execute(sql(
                "UPDATE leads SET gift=:g, updated_at=now() "
                "WHERE source='max_bot' AND tg_id=:id AND gift IS NULL RETURNING id"),
                {"g": gift, "id": user_id})).first()
            await s.commit()
            return row is not None
    except Exception as e:  # noqa: BLE001
        log.error(f"grant_book_if_new failed: {e}")
        return False


async def onboard(chat_id, user: dict, payload: str) -> bool:
    """First-contact: ensure a lead row, then send welcome + gift book ONCE."""
    gift = payload if payload in GIFT_BOOKS else DEFAULT_GIFT
    await save_lead(chat_id, user, None, payload or None)
    if await grant_book_if_new(user.get("user_id"), gift):
        log.info(f"onboarding max user={user.get('user_id')} → book {gift}")
        await send(chat_id, WELCOME)
        await send_book(chat_id, gift)
        return True
    return False


# ─────────────────────────────── Max API ───────────────────────────────

async def api(method: str, path: str, params: dict | None = None, body: dict | None = None):
    headers = {"Authorization": TOKEN}
    if body is not None:
        headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.request(method, f"{BASE}{path}", params=params or {}, headers=headers, json=body)
        try:
            return r.json()
        except ValueError:
            return {"_status": r.status_code}


def _strip_md(t: str) -> str:
    """Max renders plain text most reliably; drop Markdown markers, keep structure."""
    t = re.sub(r"`{1,3}", "", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", t)
    t = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", t)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•]\s+", "• ", t, flags=re.M)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)
    return t


def _chunks(text: str):
    while len(text) > LIMIT:
        cut = text.rfind("\n", 0, LIMIT)
        if cut < LIMIT // 2:
            cut = LIMIT
        yield text[:cut]
        text = text[cut:]
    if text:
        yield text


async def send(chat_id, text: str) -> None:
    for chunk in _chunks(text):
        await api("POST", "/messages", params={"chat_id": chat_id}, body={"text": chunk})


async def typing(chat_id) -> None:
    try:
        await api("POST", f"/chats/{chat_id}/actions", body={"action": "typing_on"})
    except Exception:  # noqa: BLE001 — a missing typing indicator is harmless
        pass


async def send_book(chat_id, key: str) -> None:
    book = GIFT_BOOKS.get(key)
    if not book:
        return
    try:
        data = await asyncio.to_thread(minio.download_file, book["path"])
        up = await api("POST", "/uploads", params={"type": "file"})
        url = up.get("url")
        async with httpx.AsyncClient(timeout=200) as c:
            r = await c.post(url, files={"data": (book["filename"], data, "application/pdf")})
            token = (r.json() or {}).get("token")
        if not token:
            raise RuntimeError("no upload token")
    except Exception as e:  # noqa: BLE001
        log.error(f"max book send prep failed: {e}")
        await send(chat_id, "Книга сейчас недоступна — починю и пришлю, извините. 🙏")
        return
    # attachment may need a moment to become ready
    for delay in (0, 1.5, 3.0, 5.0):
        if delay:
            await asyncio.sleep(delay)
        res = await api("POST", "/messages", params={"chat_id": chat_id},
                        body={"text": book["caption"],
                              "attachments": [{"type": "file", "payload": {"token": token}}]})
        if res.get("code") != "attachment.not.ready":
            break
    log.info(f"max book {key} sent to chat {chat_id}")


# ─────────────────────────────── Agent ───────────────────────────────

async def ask_agent(chat_id, text: str) -> str:
    hist = HISTORY.setdefault(chat_id, [])
    hist.append({"role": "user", "content": text})
    del hist[:-MAX_TURNS]
    answer = ""
    try:
        async with httpx.AsyncClient(timeout=200) as c:
            async with c.stream("POST", f"{BACKEND}/api/agent/chat",
                                 headers={"X-Device-Id": f"max{chat_id}"},
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

async def handle(update: dict) -> None:
    try:
        ut = update.get("update_type")
        if ut == "bot_started":
            await onboard(update.get("chat_id"), update.get("user") or {},
                          (update.get("payload") or "").strip())
            return
        if ut == "message_created":
            msg = update.get("message") or {}
            recipient = msg.get("recipient") or {}
            # ONLY private dialogs — never answer in channels/groups
            if recipient.get("chat_type") != "dialog":
                return
            chat_id = recipient.get("chat_id")
            user = msg.get("sender") or {}
            text = ((msg.get("body") or {}).get("text") or "").strip()
            if not text:
                return
            # First contact → welcome + gift book (Max often skips bot_started).
            sent_book = await onboard(chat_id, user, "")
            if text.startswith("/"):
                if not sent_book:  # a bare /start from someone who already has the book
                    await send(chat_id, "Просто напишите мне вопрос про настойки, травы или рецепты 😊")
                return
            await typing(chat_id)
            answer = await ask_agent(chat_id, text)
            await send(chat_id, _strip_md(answer))
    except Exception as e:  # noqa: BLE001
        log.error(f"handle error: {e}")


async def main() -> None:
    if not TOKEN:
        log.error("MAX_BOT_TOKEN not set — max bot idle")
        while True:
            await asyncio.sleep(3600)
    await ensure_leads_table()
    me = await api("GET", "/me")
    log.info(f"max bot online: {me.get('name')} (@{me.get('username')})")
    # Skip any backlog so we don't reply to old/channel messages on startup.
    first = await api("GET", "/updates", params={"timeout": 0, "limit": 100})
    marker = first.get("marker")
    log.info(f"starting from marker={marker} (skipped {len(first.get('updates', []))} backlog)")
    while True:
        try:
            params = {"timeout": 30, "limit": 50}
            if marker is not None:
                params["marker"] = marker
            data = await api("GET", "/updates", params=params)
            marker = data.get("marker", marker)
            for up in data.get("updates", []):
                asyncio.create_task(handle(up))
        except Exception as e:  # noqa: BLE001
            log.warning(f"max poll error: {e}")
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
