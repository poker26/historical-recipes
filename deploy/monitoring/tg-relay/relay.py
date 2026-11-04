"""Пересыльщик тревог Alertmanager → Telegram через вход туннеля.

Alertmanager (Go) не умеет ходить через вход tt.begemot26.ru:443: тот отвечает на
CONNECT кадром HTTP/2, а Go ждёт HTTP/1.1. Боты ботаники ходят через тот же вход на
httpx, и это работает, поэтому тревоги шлём так же. Принимает вебхук Alertmanager на
:9087, отвечает 500 при неудаче — тогда Alertmanager повторит.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

TOKEN = open("/run/secrets/bot_token").read().strip()
CHAT = os.environ["CHAT_ID"]
PROXY = open("/run/secrets/proxy").read().strip()


def render(payload: dict) -> str:
    lines = []
    for a in payload.get("alerts", []):
        mark = "🔴" if a.get("status") == "firing" else "✅ прошло:"
        text = (a.get("annotations") or {}).get("summary") or (a.get("labels") or {}).get("alertname", "?")
        lines.append(f"{mark} {text}")
    return "\n".join(lines) or "пустая тревога"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def do_GET(self):
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            text = render(json.loads(body))
            with httpx.Client(proxy=PROXY, timeout=30) as c:
                r = c.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                           json={"chat_id": CHAT, "text": text})
            ok = r.status_code == 200
            print(f"telegram {r.status_code}", flush=True)
        except Exception as e:  # noqa: BLE001 — Alertmanager повторит
            ok = False
            print(f"telegram error: {type(e).__name__}", flush=True)
        self.send_response(200 if ok else 500)
        self.end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9087), Handler).serve_forever()
