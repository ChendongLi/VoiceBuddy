"""
Lightweight aiohttp HTTP server for Twilio webhooks.

Runs alongside the websockets server in the same asyncio event loop.
Handles POST /incoming-call only — all WebSocket connections go to the
websockets server (src/server.py).

Default port: 8766 (configurable via HTTP_PORT env var)
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

from twilio_handler import _validate_twilio_signature, build_twiml_response

logger = logging.getLogger("voicebuddy.http")

HTTP_PORT = int(os.environ.get("HTTP_PORT", "8766"))


class _TwilioRequest:
    """Thin adapter so twilio_handler can read headers from an aiohttp request."""

    def __init__(self, aio_request: web.Request, body: bytes) -> None:
        self._headers = aio_request.headers
        self.body = body

    @property
    def headers(self) -> dict:
        return self._headers  # type: ignore[return-value]


async def handle_incoming_call(request: web.Request) -> web.Response:
    """POST /incoming-call — validate Twilio signature and return TwiML."""
    body = await request.read()
    adapted = _TwilioRequest(request, body)

    if not _validate_twilio_signature(adapted):
        logger.warning("Invalid Twilio signature on /incoming-call")
        return web.Response(status=403, text="Invalid signature")

    twiml = build_twiml_response()
    logger.info("Incoming call → TwiML (host=%s)", os.environ.get("TWILIO_WEBHOOK_HOST", "localhost"))
    return web.Response(
        status=200,
        text=twiml,
        content_type="application/xml",
    )


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — liveness probe for the HTTP server."""
    return web.Response(text='{"status":"ok"}', content_type="application/json")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/incoming-call", handle_incoming_call)
    app.router.add_get("/health", handle_health)
    return app


async def start_http_server(host: str = "127.0.0.1", port: int = HTTP_PORT) -> web.AppRunner:
    """Start the aiohttp server. Returns the runner so it can be cleaned up."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Twilio HTTP server listening on http://%s:%d", host, port)
    return runner
