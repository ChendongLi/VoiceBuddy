"""
VoiceBuddy — Real-time voice assistant server

Pipeline: Browser mic → Deepgram Flux (STT) → Claude (LLM) → Cartesia (TTS) → Browser speaker.
Uses event queue pattern: all SDK callbacks push to asyncio.Queue, a single event
processor drives the state machine and orchestrates the pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import time
from http import HTTPStatus
from pathlib import Path

from websockets.asyncio.server import serve

# Make src importable when running as `python src/server.py`
sys.path.insert(0, str(Path(__file__).parent))

from deepgram_client import DeepgramFluxClient
from latency_logger import LatencyLogger
from llm_orchestrator import LLMOrchestrator
from sentence_splitter import SentenceSplitter
from state_machine import Event, State, StateMachine
from tts_client import TTSClient

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
    """Handle a single WebSocket connection with the full STT → LLM → TTS pipeline."""
    remote = websocket.remote_address
    logger.info("Client connected: %s", remote)

    log = LatencyLogger()
    sm = StateMachine(log)
    session_id = sm.ctx.session_id
    event_queue: asyncio.Queue = asyncio.Queue()
    tts_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    log.log_event(session_id, sm.ctx.turn_id, "connection", {"action": "connected", "remote": str(remote)})

    # --- Deepgram callbacks (synchronous — push to event queue) ---

    def on_start_of_turn(turn_index, ts_ms):
        event_queue.put_nowait(("start_of_turn", {"turn_index": turn_index, "ts_ms": ts_ms}))

    def on_end_of_turn(turn_index, transcript, confidence, transcript_received_ms):
        event_queue.put_nowait(
            (
                "end_of_turn",
                {
                    "turn_index": turn_index,
                    "transcript": transcript,
                    "confidence": confidence,
                    "transcript_received_ms": transcript_received_ms,
                },
            )
        )

    def on_transcript_update(partial_transcript):
        event_queue.put_nowait(("transcript_update", {"text": partial_transcript}))

    # --- LLM callbacks (synchronous — push to event queue) ---

    def on_filler_ready(text, ttft_ms):
        event_queue.put_nowait(("llm_filler_ready", {"text": text, "ttft_ms": ttft_ms}))

    def on_full_ready(text, ttft_ms):
        event_queue.put_nowait(("llm_full_ready", {"text": text, "ttft_ms": ttft_ms}))

    def on_full_token(token):
        event_queue.put_nowait(("llm_full_token", {"token": token}))

    # --- Initialize service clients ---

    dg = DeepgramFluxClient(on_start_of_turn, on_end_of_turn, on_transcript_update)
    llm = LLMOrchestrator()
    llm.on_filler_ready = on_filler_ready
    llm.on_full_ready = on_full_ready
    llm.on_full_token = on_full_token

    tts = TTSClient()

    # Sentence splitter feeds TTS queue
    turn_context_id = f"{session_id[:8]}-0"

    def on_sentence(sentence):
        nonlocal turn_context_id
        tts_queue.put_nowait((sentence, turn_context_id))

    splitter = SentenceSplitter(on_sentence=on_sentence)

    # --- Connect services ---
    await dg.connect()
    await tts.connect()

    # --- Event processor (background task) ---

    async def process_events():
        nonlocal turn_context_id
        while True:
            event_type, data = await event_queue.get()
            try:
                if event_type == "start_of_turn":
                    sm.handle(Event.START_OF_TURN)
                    logger.info("[%s] START_OF_TURN (turn %d)", session_id[:8], data["turn_index"])

                elif event_type == "transcript_update":
                    # Send partial transcript to browser for live display
                    await websocket.send(json.dumps({"type": "transcript_partial", "text": data["text"]}))

                elif event_type == "end_of_turn":
                    sm.handle(Event.END_OF_TURN, data=data)
                    transcript = data["transcript"]

                    # Log Stage 1: EOT detection latency
                    if "user_started_speaking" in sm.ctx.markers and "user_stopped_speaking" in sm.ctx.markers:
                        log.log_latency(
                            session_id,
                            sm.ctx.turn_id,
                            "eot_detected",
                            sm.ctx.markers["user_stopped_speaking"] - sm.ctx.markers["user_started_speaking"],
                        )

                    # Log Stage 2: transcript received
                    if "user_stopped_speaking" in sm.ctx.markers:
                        log.log_latency(
                            session_id,
                            sm.ctx.turn_id,
                            "transcript_received",
                            data["transcript_received_ms"] - sm.ctx.markers["user_stopped_speaking"],
                        )

                    # Send final transcript to browser
                    await websocket.send(json.dumps({"type": "transcript", "text": transcript}))
                    logger.info("[%s] Transcript: %r", session_id[:8], transcript)

                    if transcript:
                        # Set up context_id for this turn's TTS sentences
                        turn_context_id = f"{session_id[:8]}-{sm.ctx.turn_id}"
                        # Fire LLM processing
                        asyncio.create_task(llm.process_turn(transcript))

                elif event_type == "llm_filler_ready":
                    sm.handle(Event.LLM_FILLER_READY)
                    filler_text = data["text"]

                    # Log Stage 3: filler TTFT
                    if "user_stopped_speaking" in sm.ctx.markers:
                        log.log_latency(
                            session_id,
                            sm.ctx.turn_id,
                            "llm_filler_ttft",
                            data["ttft_ms"],
                            metadata={"model": "haiku"},
                        )

                    await websocket.send(json.dumps({"type": "filler", "text": filler_text}))
                    logger.info("[%s] Filler: %r", session_id[:8], filler_text)

                    # Send filler to TTS
                    tts_queue.put_nowait((filler_text, turn_context_id))

                elif event_type == "llm_full_token":
                    # Stream tokens through sentence splitter
                    splitter.feed(data["token"])

                elif event_type == "llm_full_ready":
                    # Flush remaining sentence from splitter
                    splitter.flush()

                    sm.handle(Event.LLM_FULL_READY)
                    full_text = data["text"]

                    # Log Stage 3: full response TTFT
                    if "user_stopped_speaking" in sm.ctx.markers:
                        log.log_latency(
                            session_id,
                            sm.ctx.turn_id,
                            "llm_full_ttft",
                            data["ttft_ms"],
                            metadata={"model": "sonnet"},
                        )

                    await websocket.send(json.dumps({"type": "response", "text": full_text}))
                    logger.info("[%s] Response: %r", session_id[:8], full_text[:100])

                    # Signal TTS that no more sentences are coming for this turn
                    # (TTS worker will send playback_done after processing all queued sentences)

                elif event_type == "tts_first_byte":
                    sm.handle(Event.TTS_AUDIO_READY)
                    # Log Stage 4: TTS first byte
                    if "user_stopped_speaking" in sm.ctx.markers:
                        log.log_latency(
                            session_id,
                            sm.ctx.turn_id,
                            "tts_first_byte",
                            data["ts_ms"] - sm.ctx.markers["user_stopped_speaking"],
                        )

                elif event_type == "tts_playback_done":
                    sm.handle(Event.TTS_PLAYBACK_DONE)
                    # Log Stage 5: playback complete
                    if "tts_first_byte" in sm.ctx.markers:
                        log.log_latency(
                            session_id,
                            sm.ctx.turn_id,
                            "playback_done",
                            data["ts_ms"] - sm.ctx.markers["tts_first_byte"],
                        )
                    await websocket.send(json.dumps({"type": "playback_done"}))
                    logger.info("[%s] Playback done", session_id[:8])

            except Exception as e:
                log.log_error(session_id, sm.ctx.turn_id, "event_processing_error", f"{event_type}: {e}")
                logger.warning("[%s] Event error: %s — %s", session_id[:8], event_type, e)

    event_task = asyncio.create_task(process_events())

    # --- TTS worker (background task) ---

    async def tts_worker():
        first_byte_of_turn = True
        while True:
            item = await tts_queue.get()
            if item is None:  # Shutdown sentinel
                break
            sentence, context_id = item
            first_byte = True
            try:
                async for audio_chunk in tts.synthesize(sentence, context_id=context_id):
                    if first_byte:
                        ts_ms = time.time() * 1000
                        if first_byte_of_turn:
                            event_queue.put_nowait(("tts_first_byte", {"ts_ms": ts_ms}))
                            first_byte_of_turn = False
                        first_byte = False
                    await websocket.send(audio_chunk)  # Binary frame → browser plays it
            except Exception as e:
                log.log_error(session_id, sm.ctx.turn_id, "tts_error", str(e))
                logger.warning("[%s] TTS error: %s", session_id[:8], e)

            # Check if TTS queue is empty — if so, this turn's audio is done
            if tts_queue.empty():
                ts_ms = time.time() * 1000
                event_queue.put_nowait(("tts_playback_done", {"ts_ms": ts_ms}))
                first_byte_of_turn = True  # Reset for next turn

    tts_task = asyncio.create_task(tts_worker())

    # --- Main WebSocket loop ---

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # Binary frame — forward mic audio to Deepgram (no echo)
                await dg.send_audio(message)
            else:
                # Text frame — JSON control messages
                try:
                    msg = json.loads(message)
                except json.JSONDecodeError:
                    logger.warning("[%s] Invalid JSON: %s", session_id[:8], message[:100])
                    continue

                msg_type = msg.get("type")

                if msg_type == "ping":
                    pong = {
                        "type": "pong",
                        "t": msg.get("t"),
                        "server_ts": time.time() * 1000,
                    }
                    await websocket.send(json.dumps(pong))

    except Exception as e:
        logger.error("[%s] Connection error: %s", session_id[:8], e)
        log.log_error(session_id, sm.ctx.turn_id, "connection_error", str(e))
    finally:
        # 1. Stop TTS worker gracefully
        tts_queue.put_nowait(None)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(tts_task, timeout=5.0)

        # 2. Stop event processor
        event_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await event_task

        # 3. Close service connections
        await dg.close()
        await tts.close()

        # 4. Clean state transitions on disconnect
        if sm.current_state == State.USER_SPEAKING:
            sm.handle(Event.END_OF_TURN)
        if sm.current_state != State.IDLE:
            sm.handle(Event.RESET)

        logger.info("Client disconnected: %s", remote)
        log.log_event(
            session_id,
            sm.ctx.turn_id,
            "connection",
            {"action": "disconnected", "remote": str(remote)},
        )


async def main(host: str = "localhost", port: int = 8765):
    """Start the VoiceBuddy server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Starting VoiceBuddy server on ws://%s:%d", host, port)
    logger.info("Open http://%s:%d/ in your browser", host, port)

    async with serve(handle_connection, host, port, process_request=process_request) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
