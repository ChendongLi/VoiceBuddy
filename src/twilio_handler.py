"""
Twilio inbound call handling for VoiceBuddy.

- POST /incoming-call → TwiML connecting the call to /twilio-media WebSocket
- WS /twilio-media → Twilio MediaStream protocol (connected, start, media, stop)
"""

from __future__ import annotations

import base64
import json
import logging
import os
from http import HTTPStatus
from urllib.parse import parse_qs

from twilio.request_validator import RequestValidator

logger = logging.getLogger("voicebuddy.twilio")

TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WEBHOOK_HOST = os.environ.get("TWILIO_WEBHOOK_HOST", "")


def _validate_twilio_signature(request) -> bool:
    """Validate Twilio request signature. Returns True if valid or if auth token is not configured."""
    if not TWILIO_AUTH_TOKEN:
        logger.warning("TWILIO_AUTH_TOKEN not set — skipping signature validation")
        return True

    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = f"https://{TWILIO_WEBHOOK_HOST}/incoming-call"

    body = request.body or b""
    params = parse_qs(body.decode("utf-8", errors="replace"))
    # parse_qs returns lists; Twilio validator expects single values
    flat_params = {k: v[0] for k, v in params.items()}

    return validator.validate(url, flat_params, signature)


def build_twiml_response() -> str:
    """Build TwiML XML that connects the call to our MediaStream WebSocket."""
    ws_url = f"wss://{TWILIO_WEBHOOK_HOST}/twilio-media"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="{ws_url}" />'
        "</Connect>"
        "</Response>"
    )


def handle_incoming_call(connection, request):
    """HTTP handler for POST /incoming-call. Returns TwiML or 403 on bad signature."""
    if not _validate_twilio_signature(request):
        logger.warning("Invalid Twilio signature on /incoming-call")
        return connection.respond(HTTPStatus.FORBIDDEN, "Invalid signature")

    twiml = build_twiml_response()
    response = connection.respond(HTTPStatus.OK, twiml)
    response.headers["Content-Type"] = "application/xml"
    logger.info("Incoming call → TwiML response (host=%s)", TWILIO_WEBHOOK_HOST)
    return response


async def handle_twilio_media(websocket):
    """Handle a Twilio MediaStream WebSocket connection.

    Parses the Twilio MediaStream JSON protocol:
    - connected: log connection
    - start: extract CallSid, From, To into session context
    - media: decode base64 mulaw audio (buffered for AGE-13)
    - stop: clean shutdown
    """
    session: dict = {}
    audio_buffer = bytearray()

    logger.info("Twilio MediaStream WebSocket connected")

    try:
        async for message in websocket:
            try:
                event = json.loads(message)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from Twilio MediaStream: %s", message[:100])
                continue

            event_type = event.get("event")

            if event_type == "connected":
                protocol = event.get("protocol", "unknown")
                version = event.get("version", "unknown")
                logger.info("Twilio MediaStream connected (protocol=%s, version=%s)", protocol, version)

            elif event_type == "start":
                start_data = event.get("start", {})
                session["stream_sid"] = event.get("streamSid", "")
                session["call_sid"] = start_data.get("callSid", "")
                session["from"] = start_data.get("customParameters", {}).get("from", "")
                session["to"] = start_data.get("customParameters", {}).get("to", "")

                # Also check top-level accountSid
                session["account_sid"] = start_data.get("accountSid", "")

                logger.info(
                    "Twilio stream started: CallSid=%s, StreamSid=%s, From=%s, To=%s",
                    session.get("call_sid"),
                    session.get("stream_sid"),
                    session.get("from"),
                    session.get("to"),
                )

            elif event_type == "media":
                media_data = event.get("media", {})
                payload = media_data.get("payload", "")
                if payload:
                    raw_audio = base64.b64decode(payload)
                    audio_buffer.extend(raw_audio)

            elif event_type == "stop":
                logger.info(
                    "Twilio stream stopped: CallSid=%s (buffered %d bytes)",
                    session.get("call_sid", "unknown"),
                    len(audio_buffer),
                )
                break

    except Exception as e:
        logger.error("Twilio MediaStream error: %s", e)
    finally:
        logger.info(
            "Twilio MediaStream WebSocket closed: CallSid=%s, total audio=%d bytes",
            session.get("call_sid", "unknown"),
            len(audio_buffer),
        )
