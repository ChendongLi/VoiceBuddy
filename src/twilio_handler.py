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

from dotenv import load_dotenv
from twilio.request_validator import RequestValidator

load_dotenv()

logger = logging.getLogger("voicebuddy.twilio")


def _get_request_body(request) -> bytes:
    return getattr(request, "body", None) or b""


def _validate_twilio_signature(request) -> bool:
    """Validate Twilio request signature. Skipped if TWILIO_AUTH_TOKEN or TWILIO_WEBHOOK_HOST not set,
    or if SKIP_TWILIO_VALIDATION=true (useful for local dev with ngrok)."""
    if os.environ.get("SKIP_TWILIO_VALIDATION", "").lower() in ("1", "true", "yes"):
        logger.warning("Twilio signature validation SKIPPED (SKIP_TWILIO_VALIDATION=true)")
        return True

    # Read fresh each call to avoid stale module-level constants
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    webhook_host = os.environ.get("TWILIO_WEBHOOK_HOST", "").strip()

    if not auth_token or not webhook_host:
        logger.warning("Twilio signature validation skipped (TWILIO_AUTH_TOKEN or TWILIO_WEBHOOK_HOST not set)")
        return True

    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")

    # Prefer X-Forwarded-Proto if set by LB, otherwise default to https
    proto = request.headers.get("X-Forwarded-Proto", "https")
    url = f"{proto}://{webhook_host}/incoming-call"

    body = _get_request_body(request)
    body_str = body.decode("utf-8", errors="replace")
    params = parse_qs(body_str, keep_blank_values=True)
    flat_params = {k: v[0] for k, v in params.items()}

    # Compute signature for comparison (diagnostic)
    try:
        computed_sig = validator.compute_signature(url, flat_params)
    except Exception:
        computed_sig = "(error)"

    logger.warning(
        "Twilio sig diag: url=%s sig_match=%s computed=%s expected=%s raw_body=%r",
        url,
        computed_sig == signature,
        computed_sig[:16] if computed_sig else "(none)",
        signature[:16] if signature else "(none)",
        body[:300],
    )

    try:
        result = validator.validate(url, flat_params, signature)
    except Exception as exc:
        logger.error("Twilio signature validation error: %s", exc, exc_info=True)
        return False

    if not result:
        logger.warning(
            "Twilio signature mismatch: url=%s, param_keys=%s",
            url,
            list(flat_params.keys()),
        )

    return result


def build_twiml_response(from_number: str = "", to_number: str = "") -> str:
    """Build TwiML XML that connects the call to our MediaStream WebSocket.

    Embeds from/to as <Parameter> children so the MediaStream ``start`` event
    carries them in ``customParameters``.
    """
    # Read fresh each call so runtime env changes (e.g. TWILIO_WEBHOOK_HOST) take effect
    host = os.environ.get("TWILIO_WEBHOOK_HOST", "") or "localhost:8766"
    scheme = "ws" if host.startswith("localhost") or host.startswith("127.") else "wss"
    ws_url = f"{scheme}://{host}/twilio-media"

    params = ""
    if from_number or to_number:
        params = f'<Parameter name="from" value="{from_number}"/>' f'<Parameter name="to" value="{to_number}"/>'

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        "<Connect>"
        f'<Stream url="{ws_url}">{params}</Stream>'
        "</Connect>"
        "</Response>"
    )


def handle_incoming_call(connection, request):
    """HTTP handler for POST /incoming-call. Returns TwiML or 403 on bad signature."""
    try:
        if not _validate_twilio_signature(request):
            logger.warning("Invalid Twilio signature on /incoming-call")
            return connection.respond(HTTPStatus.FORBIDDEN, "Invalid signature")

        twiml = build_twiml_response()
        response = connection.respond(HTTPStatus.OK, twiml)
        response.headers["Content-Type"] = "application/xml"
        logger.info("Incoming call → TwiML response (host=%s)", os.environ.get("TWILIO_WEBHOOK_HOST", "localhost"))
        return response
    except Exception as exc:
        logger.error("Error handling /incoming-call: %s", exc, exc_info=True)
        return connection.respond(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")


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
