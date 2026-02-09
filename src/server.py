"""
Phase 3 — WebSocket echo server for audio pipe testing.

Serves index.html on HTTP GET /, accepts WebSocket connections, and echoes
binary audio frames back to the browser. JSON text frames are used for
ping/pong latency measurement. Integrates StateMachine and LatencyLogger
to prove wiring before AI services are added.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve

# Make src importable when running as `python src/server.py`
sys.path.insert(0, str(Path(__file__).parent))

from latency_logger import LatencyLogger
from state_machine import Event, State, StateMachine

logger = logging.getLogger("voicebuddy.server")

HTML_PATH = Path(__file__).parent / "static" / "index.html"


def _load_html() -> str:
    """Load index.html content at startup."""
    return HTML_PATH.read_text(encoding="utf-8")


HTML_CONTENT = _load_html()


def process_request(connection, request):
    """Serve index.html on GET /; allow WS upgrade on /ws; 404 otherwise."""
    if request.path == "/":
        response = connection.respond(HTTPStatus.OK, HTML_CONTENT)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        return response
    if request.path == "/ws":
        return None  # proceed with WebSocket upgrade
    return connection.respond(HTTPStatus.NOT_FOUND, "Not Found")


async def handle_connection(websocket):
    """Handle a single WebSocket connection: echo binary, ping/pong JSON.

    Phase 3 simplification: uses a single socket for both send and receive.
    Phase 4+ will split into separate WebSocket connections per service
    (Deepgram STT, Cartesia TTS).
    """
    remote = websocket.remote_address
    logger.info("Client connected: %s", remote)

    log = LatencyLogger()
    sm = StateMachine(log)
    session_id = sm.ctx.session_id
    chunk_count = 0
    first_chunk = True

    log.log_event(session_id, sm.ctx.turn_id, "connection", {"action": "connected", "remote": str(remote)})

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # Binary frame — audio PCM data, echo immediately
                chunk_count += 1

                if first_chunk:
                    sm.handle(Event.START_OF_TURN)
                    logger.info("[%s] First audio chunk — START_OF_TURN fired", session_id[:8])
                    first_chunk = False

                if chunk_count % 50 == 0:
                    logger.info("[%s] Audio chunks echoed: %d", session_id[:8], chunk_count)
                    log.log_event(
                        session_id,
                        sm.ctx.turn_id,
                        "audio",
                        {"action": "chunk_milestone", "chunk_count": chunk_count, "bytes": len(message)},
                    )

                await websocket.send(message)

            else:
                # Text frame — JSON control messages
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("[%s] Invalid JSON: %s", session_id[:8], message[:100])
                    continue

                msg_type = msg.get("type")

                if msg_type == "audio_rtt":
                    log.log_event(
                        session_id,
                        sm.ctx.turn_id,
                        "audio_rtt",
                        {
                            "avg_ms": msg.get("avg_ms"),
                            "min_ms": msg.get("min_ms"),
                            "max_ms": msg.get("max_ms"),
                            "samples": msg.get("samples"),
                        },
                    )

                elif msg_type == "ping":
                    pong = {
                        "type": "pong",
                        "t": msg.get("t"),
                        "server_ts": time.time() * 1000,
                    }
                    await websocket.send(json.dumps(pong))

                    if "t" in msg:
                        # Note: client_ts is performance.now() (ms since page load),
                        # server_ts is Unix epoch ms — cross-clock difference is meaningless.
                        # Client computes the real RTT from the echoed "t" value.
                        log.log_event(
                            session_id,
                            sm.ctx.turn_id,
                            "ws_ping",
                            {"client_ts": msg["t"], "server_ts": pong["server_ts"]},
                        )

    except Exception as e:
        logger.error("[%s] Connection error: %s", session_id[:8], e)
        log.log_error(session_id, sm.ctx.turn_id, "connection_error", str(e))
    finally:
        # Clean state transitions on disconnect
        if sm.current_state == State.USER_SPEAKING:
            sm.handle(Event.END_OF_TURN)
            logger.info("[%s] END_OF_TURN fired on disconnect", session_id[:8])
        if sm.current_state != State.IDLE:
            sm.handle(Event.RESET)
            logger.info("[%s] RESET fired on disconnect", session_id[:8])

        logger.info("Client disconnected: %s (chunks echoed: %d)", remote, chunk_count)
        log.log_event(
            session_id,
            sm.ctx.turn_id,
            "connection",
            {"action": "disconnected", "remote": str(remote), "total_chunks": chunk_count},
        )


async def main(host: str = "localhost", port: int = 8765):
    """Start the echo server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Starting VoiceBuddy echo server on ws://%s:%d", host, port)
    logger.info("Open http://%s:%d/ in your browser", host, port)

    async with serve(handle_connection, host, port, process_request=process_request) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
