"""
Lightweight aiohttp HTTP server for Twilio webhooks + WebSocket media.

Runs alongside the websockets server in the same asyncio event loop.
Handles:
  POST /incoming-call  — Twilio webhook, returns TwiML
  GET  /twilio-media   — WebSocket upgrade for Twilio MediaStream
  GET  /health         — liveness probe

Single-port design: ngrok only needs to expose HTTP_PORT (8766).
TWILIO_WEBHOOK_HOST should be set to the public ngrok/Cloud Run host so
TwiML <Stream> URL points to the right place.

Default port: 8766 (configurable via HTTP_PORT env var)
"""

from __future__ import annotations

import json
import logging
import os

from aiohttp import web

from circuit_breaker import CircuitBreakerRegistry
from twilio_handler import _validate_twilio_signature, build_twiml_response

# Injected by server.py to avoid circular import
_handle_twilio_media_fn = None  # set via set_twilio_media_handler()


def set_twilio_media_handler(fn) -> None:
    """Called by server.py to wire in the Twilio MediaStream handler."""
    global _handle_twilio_media_fn
    _handle_twilio_media_fn = fn


logger = logging.getLogger("voicebuddy.http")

HTTP_PORT = int(os.environ.get("HTTP_PORT", "8766"))

# Shared registries — set by server.py at startup to avoid circular imports
circuit_breaker_registry = CircuitBreakerRegistry()
_tenant_registry = None  # set via set_tenant_registry()


def set_tenant_registry(registry: object) -> None:
    """Called by server.py after TenantRegistry is built."""
    global _tenant_registry
    _tenant_registry = registry


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

    # Parse form params from already-read body to avoid double-read
    from urllib.parse import parse_qs

    parsed = parse_qs(body.decode("utf-8", errors="replace"))
    # URL form encoding turns '+' into ' ' — normalize back to E.164
    to_number = (parsed.get("To", [""])[0]).replace(" ", "+")
    if _tenant_registry is not None and to_number:
        tenant = _tenant_registry.get_by_phone(to_number)
        if tenant is None:
            logger.warning("Unknown To number: %s — rejecting call", to_number)
            reject_twiml = (
                '<?xml version="1.0" encoding="UTF-8"?><Response><Reject reason="unallocated-number"/></Response>'
            )
            return web.Response(status=200, text=reject_twiml, content_type="application/xml")

    twiml = build_twiml_response()
    logger.info(
        "Incoming call → TwiML for %s (host=%s)",
        to_number,
        os.environ.get("TWILIO_WEBHOOK_HOST", "localhost"),
    )
    return web.Response(
        status=200,
        text=twiml,
        content_type="application/xml",
    )


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — liveness probe with per-tenant circuit breaker status."""
    tenants = {}
    if _tenant_registry is not None:
        for t in _tenant_registry.all_tenants:
            cb = circuit_breaker_registry.get(t.tenant_id)
            tenants[t.tenant_id] = {
                "phone": t.phone_number,
                "circuit_breaker": cb.snapshot()["state"],
            }
    body = json.dumps(
        {
            "status": "ok",
            "tenants": tenants,
            "circuit_breakers": circuit_breaker_registry.all_snapshots(),
        }
    )
    return web.Response(text=body, content_type="application/json")


async def handle_twilio_media_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /twilio-media — WebSocket endpoint for Twilio MediaStream.

    Wraps the existing handle_twilio_media() function with an aiohttp-to-websockets
    adapter so both servers share the same business logic.
    """
    if _handle_twilio_media_fn is None:
        return web.Response(status=503, text="Media handler not ready")

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Thin adapter: expose the same send/recv API the websockets library uses
    import aiohttp as _aiohttp

    remote = request.remote

    class _FakeRequest:
        path = "/twilio-media"

    class _WsAdapter:
        def __init__(self) -> None:
            self.remote_address = remote
            self.request = _FakeRequest()

        async def send(self, data: str | bytes) -> None:
            if isinstance(data, bytes):
                await ws.send_bytes(data)
            else:
                await ws.send_str(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            msg = await ws.receive()
            if msg.type in (
                _aiohttp.WSMsgType.CLOSE,
                _aiohttp.WSMsgType.ERROR,
                _aiohttp.WSMsgType.CLOSED,
            ):
                raise StopAsyncIteration
            return msg.data

    await _handle_twilio_media_fn(_WsAdapter())
    return ws


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/incoming-call", handle_incoming_call)
    app.router.add_get("/twilio-media", handle_twilio_media_ws)
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
