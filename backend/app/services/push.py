"""Пуш-отправлялка «Что растёт» (RFC-v2 §5): APNs напрямую + RuStore Push.

Токены девайсов — в `push_tokens` (device_key, platform apns|rustore, token);
регистрирует клиент через POST /devices/{key}/push-token. Отправка:

- **APNs** — токен-авторизация (ES256 JWT по .p8-ключу, НЕ истекает; ключ в env
  APNS_KEY_B64). HTTP/2 на api.push.apple.com (замер 2026-08-24: с server 1
  достижим напрямую, прокси не нужен). Мёртвые токены (410/BadDeviceToken)
  вычищаются из таблицы.
- **RuStore Push** — VK-инфраструктура (без Google): FCM-подобный REST
  vkpns.rustore.ru. Ждёт project_id + сервисный токен из консоли RuStore.

Всё best-effort: сбой пуша никогда не ломает вызвавший сценарий (никаких raise).
Первый потребитель — «Ты заработал значок — забери» (claimable, джоба отдельно).
"""
import base64
import logging
import time

import httpx
import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

APNS_HOST = "https://api.push.apple.com"

_jwt_cache: tuple[float, str] | None = None


def _apns_jwt() -> str | None:
    """ES256-токен провайдера; Apple просит обновлять раз в 20–60 мин — кэш 45 мин."""
    global _jwt_cache
    if not settings.apns_key_b64:
        return None
    now = time.time()
    if _jwt_cache and now - _jwt_cache[0] < 45 * 60:
        return _jwt_cache[1]
    key_pem = base64.b64decode(settings.apns_key_b64)
    token = jwt.encode(
        {"iss": settings.apns_team_id, "iat": int(now)},
        key_pem, algorithm="ES256",
        headers={"kid": settings.apns_key_id},
    )
    _jwt_cache = (now, token)
    return token


async def send_apns(token: str, title: str, body: str,
                    data: dict | None = None) -> tuple[bool, str]:
    """(delivered, reason). reason='BadDeviceToken'/'Unregistered' → токен мёртв."""
    bearer = _apns_jwt()
    if not bearer:
        return False, "apns not configured"
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    if data:
        payload.update(data)
    try:
        async with httpx.AsyncClient(http2=True, timeout=20) as client:
            r = await client.post(
                f"{APNS_HOST}/3/device/{token}",
                json=payload,
                headers={
                    "authorization": f"bearer {bearer}",
                    "apns-topic": settings.apns_topic,
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                })
        if r.status_code == 200:
            return True, "ok"
        reason = ""
        try:
            reason = r.json().get("reason", "")
        except ValueError:
            pass
        return False, f"{r.status_code}:{reason}"
    except httpx.HTTPError as e:
        return False, f"transport:{str(e)[:80]}"


_fcm_cache: tuple[float, str, str] | None = None   # (когда, access_token, project_id)


async def _fcm_access_token() -> tuple[str, str] | None:
    """OAuth-токен FCM по ключу сервисного аккаунта (JWT RS256 → oauth2), кэш на 50 минут."""
    global _fcm_cache
    if not settings.fcm_service_account_b64:
        return None
    now = time.time()
    if _fcm_cache and now - _fcm_cache[0] < 50 * 60:
        return _fcm_cache[1], _fcm_cache[2]
    import json as _json
    sa = _json.loads(base64.b64decode(settings.fcm_service_account_b64))
    assertion = jwt.encode(
        {"iss": sa["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
         "aud": "https://oauth2.googleapis.com/token", "iat": int(now), "exp": int(now) + 3600},
        sa["private_key"], algorithm="RS256", headers={"kid": sa.get("private_key_id")})
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion})
    r.raise_for_status()
    tok = r.json()["access_token"]
    _fcm_cache = (now, tok, sa["project_id"])
    return tok, sa["project_id"]


async def send_fcm(token: str, title: str, body: str,
                   data: dict | None = None) -> tuple[bool, str]:
    """FCM HTTP v1. Канал уведомлений тот же, что у приложения (chto_rastet_default)."""
    try:
        auth = await _fcm_access_token()
        if not auth:
            return False, "fcm not configured"
        access, project = auth
        msg = {"message": {"token": token,
                           "notification": {"title": title, "body": body},
                           "android": {"notification": {"channel_id": "chto_rastet_default"}}}}
        if data:
            msg["message"]["data"] = {k: str(v) for k, v in data.items()}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                                  json=msg, headers={"Authorization": f"Bearer {access}"})
        if r.status_code == 200:
            return True, "200"
        # Токен больше не действует: приложение удалили или токен сменился.
        dead = r.status_code == 404 or "UNREGISTERED" in r.text or "registration-token-not-registered" in r.text
        return False, f"{'Unregistered ' if dead else ''}{r.status_code}:{r.text[:80]}"
    except Exception as e:
        return False, f"transport:{type(e).__name__}:{str(e)[:60]}"


async def send_rustore(token: str, title: str, body: str,
                       data: dict | None = None) -> tuple[bool, str]:
    """RuStore Push (VK): FCM-подобный send. Ждёт project_id + сервисный токен."""
    if not (settings.rustore_push_project_id and settings.rustore_push_token):
        return False, "rustore push not configured"
    msg = {"message": {"token": token,
                       "notification": {"title": title, "body": body}}}
    if data:
        msg["message"]["data"] = {k: str(v) for k, v in data.items()}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"https://vkpns.rustore.ru/v1/projects/{settings.rustore_push_project_id}/messages:send",
                json=msg,
                headers={"Authorization": f"Bearer {settings.rustore_push_token}"})
        return (r.status_code == 200, f"{r.status_code}:{r.text[:80]}")
    except httpx.HTTPError as e:
        return False, f"transport:{str(e)[:80]}"


async def send_to_device(db: AsyncSession, device_key: str, title: str, body: str,
                         data: dict | None = None) -> dict:
    """Все токены девайса; мёртвые APNs-токены удаляются. Никогда не бросает."""
    out = {"sent": 0, "failed": 0}
    try:
        rows = (await db.execute(text(
            "SELECT platform, token FROM push_tokens WHERE device_key = CAST(:dk AS uuid)"),
            {"dk": device_key})).all()
        # На Android токенов бывает два: FCM и RuStore. Сначала FCM; если он дошёл, RuStore не
        # шлём, иначе на телефоне с обоими каналами будет два одинаковых уведомления.
        order = {"apns": 0, "fcm": 1, "rustore": 2}
        android_sent = False
        for platform, token in sorted(rows, key=lambda r: order.get(r[0], 9)):
            if platform == "rustore" and android_sent:
                continue
            if platform == "apns":
                ok, reason = await send_apns(token, title, body, data)
            elif platform == "fcm":
                ok, reason = await send_fcm(token, title, body, data)
            else:
                ok, reason = await send_rustore(token, title, body, data)
            if ok and platform in ("fcm", "rustore"):
                android_sent = True
            if ok:
                out["sent"] += 1
            else:
                out["failed"] += 1
                logger.info(f"push {platform} failed for {device_key[:8]}: {reason}")
                if any(m in reason for m in ("BadDeviceToken", "Unregistered", "ExpiredToken")):
                    await db.execute(text(
                        "DELETE FROM push_tokens WHERE device_key = CAST(:dk AS uuid) AND platform = :p"),
                        {"dk": device_key, "p": platform})
                    await db.commit()
    except Exception as e:
        logger.warning(f"send_to_device error: {type(e).__name__}: {e}")
    return out
