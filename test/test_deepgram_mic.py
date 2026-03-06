"""
Deepgram Flux STT — Live Microphone Test

Streams microphone audio to Deepgram Flux and prints live transcripts
with confidence-colored words. Runs until Ctrl+C.

Usage:
    poetry run python test/test_deepgram_mic.py
"""

import asyncio
import contextlib
import os
import signal
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env BEFORE importing deepgram — the SDK evaluates
# os.getenv("DEEPGRAM_API_KEY") once at import time as a default parameter.
load_dotenv(Path(__file__).parent.parent / ".env")

from deepgram import AsyncDeepgramClient  # noqa: E402
from deepgram.core.events import EventType  # noqa: E402
from deepgram.extensions.types.sockets import ListenV2SocketClientResponse  # noqa: E402

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from audio_sources import MicrophoneAudioSource  # noqa: E402


class Colors:
    GREEN = "\033[92m"  # 0.90–1.00
    YELLOW = "\033[93m"  # 0.80–0.90
    ORANGE = "\033[91m"  # 0.70–0.80
    RED = "\033[31m"  # <=0.69
    RESET = "\033[0m"


def get_confidence_color(confidence: float) -> str:
    if confidence >= 0.90:
        return Colors.GREEN
    elif confidence >= 0.80:
        return Colors.YELLOW
    elif confidence >= 0.70:
        return Colors.ORANGE
    else:
        return Colors.RED


_start_time: float = 0.0
_transcript_count: int = 0

CHUNK_SIZE = 2560  # ~80 ms at 16 kHz, mono, 16-bit
SAMPLE_RATE = 16000


def print_summary():
    duration = time.time() - _start_time
    print(f"\n{'=' * 50}")
    print(f"Session duration : {duration:.1f}s")
    print(f"Total transcripts: {_transcript_count}")
    print(f"{'=' * 50}")


async def main():
    global _start_time, _transcript_count
    _start_time = time.time()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    # Capture existing signal handlers to restore later
    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    try:
        if not os.environ.get("DEEPGRAM_API_KEY"):
            print("DEEPGRAM_API_KEY not set. Check your .env file.")
            sys.exit(1)

        # Set up microphone
        mic = MicrophoneAudioSource(sample_rate=SAMPLE_RATE, channels=1, dtype="int16")
        print(f"Audio: {mic.get_channels()}ch, {mic.get_sample_width()*8}bit, {mic.get_sample_rate()}Hz")

        # Pass API key explicitly to avoid reliance on import-time default
        client = AsyncDeepgramClient(api_key=os.environ["DEEPGRAM_API_KEY"])

        async with client.listen.v2.connect(
            model="flux-general-en",
            encoding="linear16",
            sample_rate=str(SAMPLE_RATE),
            eot_threshold=0.7,  # Trigger EndOfTurn when confidence >= 0.7
            eager_eot_threshold=0.6,  # Earlier EOT hint for faster responses
        ) as connection:

            def on_message(msg: ListenV2SocketClientResponse) -> None:
                global _transcript_count

                if hasattr(msg, "transcript") and msg.transcript:
                    _transcript_count += 1
                    print(f"\n  [{_transcript_count}] {msg.transcript}")

                    if hasattr(msg, "words") and msg.words:
                        colored = []
                        for w in msg.words:
                            c = get_confidence_color(w.confidence)
                            colored.append(f"{c}{w.word}({w.confidence:.2f}){Colors.RESET}")
                        print(f"       {' | '.join(colored)}")

                elif getattr(msg, "type", None) == "Connected":
                    print("Connected to Deepgram Flux — speak into your mic!")

                # Show end-of-turn probability when Deepgram sends it (Flux TurnInfo)
                eot_conf = getattr(msg, "end_of_turn_confidence", None)
                event = getattr(msg, "event", None)
                if eot_conf is not None:
                    label = event or "Update"
                    print(f"       ↳ End-of-turn confidence ({label}): {eot_conf:.2f}")

            connection.on(EventType.OPEN, lambda _: None)
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.CLOSE, lambda _: print("\nWebSocket closed"))
            connection.on(EventType.ERROR, lambda e: print(f"\nError: {e}"))

            # Start background listener
            listen_task = asyncio.create_task(connection.start_listening())

            # Brief pause for the connection to settle
            await asyncio.sleep(0.2)

            print("Streaming mic audio (Ctrl+C to stop) ...")

            try:
                async for chunk in mic.get_chunks(CHUNK_SIZE):
                    if stop_event.is_set():
                        break
                    await connection._send(chunk)
            except asyncio.CancelledError:
                pass

            listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listen_task
    finally:
        # Restore prior signal handlers
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        if _start_time:
            print_summary()
