"""
Unified aiohttp HTTP server for VoiceBuddy.

Single port handles everything:
  GET  /             — browser demo UI (index.html)
  GET  /ws           — browser WebSocket (voice pipeline)
  POST /incoming-call — Twilio webhook, returns TwiML
  GET  /twilio-media  — WebSocket upgrade for Twilio MediaStream
  GET  /health        — liveness probe

Set PORT env var (default 8765) to control which port to bind.
Set SERVER_HOST=0.0.0.0 in production (Cloud Run requires binding all interfaces).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import aiohttp as _aiohttp
from aiohttp import web

from circuit_breaker import CircuitBreakerRegistry
from twilio_handler import _validate_twilio_signature, build_twiml_response

# Injected by server.py to avoid circular import
_handle_twilio_media_fn = None  # set via set_twilio_media_handler()
_handle_browser_ws_fn = None  # set via set_browser_ws_handler()


def set_twilio_media_handler(fn) -> None:
    """Called by server.py to wire in the Twilio MediaStream handler."""
    global _handle_twilio_media_fn
    _handle_twilio_media_fn = fn


def set_browser_ws_handler(fn) -> None:
    """Called by server.py to wire in the browser WebSocket handler."""
    global _handle_browser_ws_fn
    _handle_browser_ws_fn = fn


logger = logging.getLogger("voicebuddy.http")

PORT = int(os.environ.get("PORT", "8765"))

_INDEX_HTML = Path(__file__).parent / "static" / "index.html"

# Shared registries — set by server.py at startup to avoid circular imports
circuit_breaker_registry = CircuitBreakerRegistry()
_tenant_registry = None  # set via set_tenant_registry()


class _WsAdapter:
    """Adapts an aiohttp WebSocketResponse to the interface expected by the connection handlers."""

    def __init__(self, ws: web.WebSocketResponse, remote: str, path: str) -> None:
        self.remote_address = remote
        self._ws = ws

        class _Request:
            pass

        req = _Request()
        req.path = path
        self.request = req

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, bytes):
            await self._ws.send_bytes(data)
        else:
            await self._ws.send_str(data)

    async def close(self) -> None:
        await self._ws.close()

    def __aiter__(self):
        return self

    async def __anext__(self):
        msg = await self._ws.receive()
        if msg.type in (
            _aiohttp.WSMsgType.CLOSE,
            _aiohttp.WSMsgType.ERROR,
            _aiohttp.WSMsgType.CLOSED,
        ):
            raise StopAsyncIteration
        return msg.data


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
    from_number = (parsed.get("From", [""])[0]).replace(" ", "+")
    to_number = (parsed.get("To", [""])[0]).replace(" ", "+")
    if _tenant_registry is not None and to_number:
        tenant = _tenant_registry.get_by_phone(to_number)
        if tenant is None:
            logger.warning("Unknown To number: %s — rejecting call", to_number)
            reject_twiml = (
                '<?xml version="1.0" encoding="UTF-8"?><Response><Reject reason="unallocated-number"/></Response>'
            )
            return web.Response(status=200, text=reject_twiml, content_type="application/xml")

    twiml = build_twiml_response(from_number, to_number)
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
    """GET /twilio-media — WebSocket endpoint for Twilio MediaStream."""
    if _handle_twilio_media_fn is None:
        return web.Response(status=503, text="Media handler not ready")

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await _handle_twilio_media_fn(_WsAdapter(ws, request.remote, "/twilio-media"))
    return ws


async def handle_browser_ws(request: web.Request) -> web.WebSocketResponse:
    """GET /ws — WebSocket endpoint for the browser demo UI."""
    if _handle_browser_ws_fn is None:
        return web.Response(status=503, text="Browser handler not ready")

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    # Pass full path including ?voice=... so resolve_voice_id() works
    path = str(request.rel_url)
    await _handle_browser_ws_fn(_WsAdapter(ws, request.remote, path))
    return ws


async def handle_index(request: web.Request) -> web.Response:
    """GET / — serve the browser demo UI."""
    return web.Response(
        text=_INDEX_HTML.read_text(encoding="utf-8"),
        content_type="text/html",
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_browser_ws)
    app.router.add_post("/incoming-call", handle_incoming_call)
    app.router.add_get("/twilio-media", handle_twilio_media_ws)
    app.router.add_get("/health", handle_health)
    return app


async def start_http_server(host: str = "127.0.0.1", port: int = PORT) -> web.AppRunner:
    """Start the aiohttp server. Returns the runner so it can be cleaned up."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("VoiceBuddy HTTP server listening on http://%s:%d", host, port)
    return runner
