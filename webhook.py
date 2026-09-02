import json
import os
import asyncio
from http.server import BaseHTTPRequestHandler

from telegram import Update

_app = None
_initialized = False


async def _get_app():
    global _app, _initialized

    if _app is None:
        from bot import build_application
        _app = build_application(os.environ["BOT_TOKEN"])

    if not _initialized:
        await _app.initialize()
        _initialized = True

    return _app


class handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        raw = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {"ok": True, "service": "telegram-webhook"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))

            async def process():
                app = await _get_app()
                update = Update.de_json(payload, app.bot)
                await app.process_update(update)

            asyncio.run(process())
            self._send(200, {"ok": True})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)})
