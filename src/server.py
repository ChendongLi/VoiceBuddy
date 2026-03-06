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
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import serve

# Make src importable when running as `python src/server.py`
sys.path.insert(0, str(Path(__file__).parent))

from deepgram_client import DeepgramFluxClient
from latency_logger import LatencyLogger
from llm_orchestrator import LLMOrchestrator
from sentence_splitter import SentenceSplitter
from state_machine import Event, State, StateMachine
from tts_client import TTSClient
from vad_detector import VADDetector

logger = logging.getLogger("voicebuddy.server")

HTML_PATH = Path(__file__).parent / "static" / "index.html"

# Known selectable voices — key matches the ?voice= query param from the UI
VOICE_IDS: dict[str, str] = {
    "allison": "f786b574-daa5-4673-aa0c-cbe3e8534c02",
    "don": "a3e3ea35-4533-47d6-afdb-c286538657ca",
}


def _load_html() -> str:
    """Load index.html content at startup."""
    return HTML_PATH.read_text(encoding="utf-8")


HTML_CONTENT = _load_html()


def process_request(connection, request):
    """Serve index.html on GET /; /health for probes; allow WS upgrade on /ws; 404 otherwise."""
    if request.path == "/health":
        response = connection.respond(HTTPStatus.OK, '{"status":"ok"}')
        response.headers["Content-Type"] = "application/json"
        return response
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

    # Resolve voice ID from ?voice= query param (e.g. /ws?voice=allison)
    qs = parse_qs(urlparse(websocket.request.path).query)
    voice_key = qs.get("voice", ["allison"])[0].lower()
    voice_id = VOICE_IDS.get(voice_key) or VOICE_IDS["allison"]
    logger.info("Client connected: %s (voice=%s)", remote, voice_key)

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

    # --- VAD callbacks (synchronous — push to event queue) ---

    def on_vad_speech_start():
        event_queue.put_nowait(("vad_speech_start", {}))

    def on_vad_speech_end():
        event_queue.put_nowait(("vad_speech_end", {}))

    # --- Initialize service clients ---

    dg = DeepgramFluxClient(on_start_of_turn, on_end_of_turn, on_transcript_update)
    llm = LLMOrchestrator()
    llm.on_filler_ready = on_filler_ready
    llm.on_full_ready = on_full_ready
    llm.on_full_token = on_full_token

    tts = TTSClient(voice_id=voice_id)
    vad = VADDetector(on_speech_start=on_vad_speech_start, on_speech_end=on_vad_speech_end)
    vad_speech_active = False

    # Barge-in grace window (handles Deepgram-first ordering)
    last_vad_speech_start_ms: float = 0.0
    pending_barge_in: dict | None = None
    BARGE_IN_GRACE_MS = 200.0

    # Cancellation state
    llm_task: asyncio.Task | None = None
    llm_cancelled = False
    tts_cancel_event = asyncio.Event()

    # Silence policy
    silence_timer_task: asyncio.Task | None = None

    # Sentence splitter feeds TTS queue
    turn_context_id = f"{session_id[:8]}-0"

    def on_sentence(sentence):
        nonlocal turn_context_id
        tts_queue.put_nowait((sentence, turn_context_id))

    splitter = SentenceSplitter(on_sentence=on_sentence)

    # --- Connect services ---
    await dg.connect()
    await tts.connect()

    # --- Cancel pipeline (barge-in) ---

    async def cancel_pipeline():
        """Cancel in-flight LLM and TTS on barge-in."""
        nonlocal llm_task, llm_cancelled
        llm_cancelled = True

        # 0. Log cancellation marker before clearing state
        log.log_latency(
            session_id,
            sm.ctx.turn_id,
            "turn_cancelled",
            time.time() * 1000 - sm.ctx.markers.get("user_started_speaking", time.time() * 1000),
            metadata={"from_state": sm.current_state.name},
        )

        # 1. Cancel LLM task
        if llm_task and not llm_task.done():
            llm_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await llm_task
            llm_task = None

        # 2. Drain TTS queue
        while not tts_queue.empty():
            try:
                tts_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # 3. Signal TTS worker to abort current synthesis
        tts_cancel_event.set()

        # 4. Cancel Cartesia stream
        await tts.cancel_current()

        # 5. Discard sentence splitter buffer
        splitter.discard()

        # 6. Record interrupted response in conversation history
        llm.mark_interrupted()

        # 7. Signal browser to stop playback
        await websocket.send(json.dumps({"type": "stop_playback"}))

        # 8. Reset VAD
        vad.reset()

    # --- Silence policy ---

    async def silence_policy():
        """Graduated silence responses — prompt after 2s, disconnect after 5s."""
        try:
            await asyncio.sleep(2.0)
            if sm.current_state in {State.PROCESSING, State.FILLER_RESPONSE}:
                tts_queue.put_nowait(("Are you still there?", turn_context_id))
                await websocket.send(json.dumps({"type": "filler", "text": "Are you still there?"}))

            await asyncio.sleep(3.0)  # 5s total
            if sm.current_state in {State.PROCESSING, State.FILLER_RESPONSE}:
                goodbye = "I'm sorry, it seems we lost the connection. Feel free to call back anytime."
                tts_queue.put_nowait((goodbye, turn_context_id))
                await websocket.send(json.dumps({"type": "response", "text": goodbye}))
                await asyncio.sleep(5.0)
                await websocket.close()
        except asyncio.CancelledError:
            pass

    def start_silence_timer():
        nonlocal silence_timer_task
        cancel_silence_timer()
        silence_timer_task = asyncio.create_task(silence_policy())

    def cancel_silence_timer():
        nonlocal silence_timer_task
        if silence_timer_task and not silence_timer_task.done():
            silence_timer_task.cancel()
            silence_timer_task = None

    # --- Event processor (background task) ---

    async def process_events():
        nonlocal turn_context_id, vad_speech_active, llm_task, last_vad_speech_start_ms, pending_barge_in, llm_cancelled
        while True:
            event_type, data = await event_queue.get()
            try:
                if event_type == "vad_speech_start":
                    vad_speech_active = True
                    now_ms = time.time() * 1000
                    last_vad_speech_start_ms = now_ms
                    sm.ctx.markers["vad_speech_start"] = now_ms

                    # Check for pending barge-in from Deepgram (Deepgram-first ordering)
                    barge_in_states = {State.BOT_SPEAKING, State.FILLER_RESPONSE, State.PROCESSING}
                    if (
                        pending_barge_in
                        and (now_ms - pending_barge_in["ts_ms"]) < BARGE_IN_GRACE_MS
                        and sm.current_state in barge_in_states
                    ):
                        turn_index = pending_barge_in["turn_index"]
                        pending_barge_in = None
                        sm.handle(Event.BARGE_IN_DETECTED)
                        await cancel_pipeline()
                        vad_speech_active = False
                        cancel_silence_timer()
                        sm.handle(Event.START_OF_TURN)
                        logger.info("[%s] BARGE-IN deferred (turn %d)", session_id[:8], turn_index)

                        if "vad_speech_start" in sm.ctx.markers and "barge_in_detected" in sm.ctx.markers:
                            log.log_latency(
                                session_id,
                                sm.ctx.turn_id,
                                "barge_in_reaction",
                                sm.ctx.markers["barge_in_detected"] - sm.ctx.markers["vad_speech_start"],
                            )

                elif event_type == "vad_speech_end":
                    vad_speech_active = False

                elif event_type == "start_of_turn":
                    current = sm.current_state
                    barge_in_states = {State.BOT_SPEAKING, State.FILLER_RESPONSE, State.PROCESSING}

                    # Check VAD: currently active OR recently started (grace window)
                    now_ms = time.time() * 1000
                    vad_recently_active = vad_speech_active or (
                        last_vad_speech_start_ms > 0 and (now_ms - last_vad_speech_start_ms) < BARGE_IN_GRACE_MS
                    )

                    logger.info(
                        "[%s] SOT check: state=%s vad_active=%s vad_recent=%s pending=%s",
                        session_id[:8],
                        current.name,
                        vad_speech_active,
                        vad_recently_active,
                        pending_barge_in is not None,
                    )

                    if current in barge_in_states and vad_recently_active:
                        # Real barge-in confirmed by VAD + Deepgram
                        pending_barge_in = None  # Clear any pending
                        sm.handle(Event.BARGE_IN_DETECTED)
                        await cancel_pipeline()
                        vad_speech_active = False
                        cancel_silence_timer()
                        # Transition to USER_SPEAKING for the new utterance
                        sm.handle(Event.START_OF_TURN)
                        logger.info("[%s] BARGE-IN (turn %d)", session_id[:8], data["turn_index"])

                        # Log barge-in reaction time
                        if "vad_speech_start" in sm.ctx.markers and "barge_in_detected" in sm.ctx.markers:
                            log.log_latency(
                                session_id,
                                sm.ctx.turn_id,
                                "barge_in_reaction",
                                sm.ctx.markers["barge_in_detected"] - sm.ctx.markers["vad_speech_start"],
                            )
                    elif current in barge_in_states:
                        # Deepgram first — defer, wait for VAD within grace window
                        pending_barge_in = {"turn_index": data["turn_index"], "ts_ms": now_ms}
                        logger.debug(
                            "[%s] START_OF_TURN deferred, awaiting VAD (turn %d)",
                            session_id[:8],
                            data["turn_index"],
                        )
                    elif current in {State.IDLE, State.BARGE_IN_DETECTED}:
                        # Normal START_OF_TURN in listening states
                        sm.handle(Event.START_OF_TURN)
                        cancel_silence_timer()
                        logger.info("[%s] START_OF_TURN (turn %d)", session_id[:8], data["turn_index"])
                    else:
                        # False speech in non-listening state — drop silently
                        logger.debug("[%s] Dropping START_OF_TURN in %s (no VAD)", session_id[:8], current.name)

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
                        llm_cancelled = False
                        llm_task = asyncio.create_task(llm.process_turn(transcript))
                        # Start silence policy timer
                        start_silence_timer()

                elif event_type == "llm_filler_ready":
                    if llm_cancelled:
                        logger.debug("[%s] Dropping stale llm_filler_ready", session_id[:8])
                    else:
                        sm.handle(Event.LLM_FILLER_READY)
                        cancel_silence_timer()
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
                    if not llm_cancelled:
                        splitter.feed(data["token"])

                elif event_type == "llm_full_ready":
                    if llm_cancelled:
                        logger.debug("[%s] Dropping stale llm_full_ready", session_id[:8])
                    else:
                        # Flush remaining sentence from splitter
                        splitter.flush()
                        # Signal TTS worker: no more sentences for this turn
                        tts_queue.put_nowait(("__turn_end__", turn_context_id))

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

                elif event_type == "tts_first_byte":
                    sm.handle(Event.TTS_AUDIO_READY)
                    cancel_silence_timer()
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

            # Turn-end sentinel: fire tts_playback_done and wait for next turn
            if sentence == "__turn_end__":
                ts_ms = time.time() * 1000
                event_queue.put_nowait(("tts_playback_done", {"ts_ms": ts_ms}))
                first_byte_of_turn = True
                continue

            first_byte = True
            tts_cancel_event.clear()
            try:
                async for audio_chunk in tts.synthesize(sentence, context_id=context_id):
                    if tts_cancel_event.is_set():
                        break
                    if first_byte:
                        ts_ms = time.time() * 1000
                        if first_byte_of_turn:
                            event_queue.put_nowait(("tts_first_byte", {"ts_ms": ts_ms}))
                            first_byte_of_turn = False
                        first_byte = False
                    await websocket.send(audio_chunk)  # Binary frame → browser plays it
            except Exception as e:
                if not tts_cancel_event.is_set():
                    log.log_error(session_id, sm.ctx.turn_id, "tts_error", str(e))
                    logger.warning("[%s] TTS error: %s", session_id[:8], e)

            if tts_cancel_event.is_set():
                first_byte_of_turn = True
                continue

    tts_task = asyncio.create_task(tts_worker())

    # --- Main WebSocket loop ---

    try:
        async for message in websocket:
            if isinstance(message, bytes):
                # Binary frame — feed VAD and forward mic audio to Deepgram
                vad.feed(message)
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
        # 0. Cancel silence timer
        cancel_silence_timer()

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


async def main(host: str = "0.0.0.0", port: int = 8765):
    """Start the VoiceBuddy server."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("Starting VoiceBuddy server on ws://%s:%d", host, port)
    logger.info("Open http://%s:%d/ in your browser", host, port)

    # Pre-warm Silero VAD model so the first WebSocket connection doesn't pay
    # the ONNX session-creation cost. Each VADDetector still gets its own
    # instance (isolated LSTM state), but the ONNX runtime caches the session
    # factory after this initial load, making subsequent instantiations fast.
    try:
        from silero_vad import load_silero_vad as _load_vad
        logger.info("Pre-loading Silero VAD model...")
        _load_vad(onnx=True)
        logger.info("Silero VAD model pre-loaded OK")
    except Exception as _e:
        logger.warning("VAD pre-load failed (non-fatal): %s", _e)

    async with serve(handle_connection, host, port, process_request=process_request) as server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
