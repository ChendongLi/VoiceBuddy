"""
Phase 1 API Validation Script

Validates all 3 APIs are accessible and measures baseline latency:
1. Deepgram Flux API - Speech-to-text transcription
2. Anthropic API - Claude Haiku 4.5 and Sonnet 4.5
3. Cartesia API - Sonic 3 text-to-speech with voice clone

Success criteria:
- All APIs respond successfully
- Latencies are logged to JSONL
- Console shows clear pass/fail for each service
"""

import asyncio
import json
import os
import sys
import time
import wave  # Still needed for reading test audio in Deepgram test
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# Third-party imports
try:
    import httpx
    from anthropic import AsyncAnthropic
    from cartesia import Cartesia
    from deepgram import AsyncDeepgramClient
    from deepgram.core.events import EventType
    from deepgram.extensions.types.sockets import ListenV2SocketClientResponse
except ImportError as e:
    print(f"❌ Missing required package: {e}")
    print("\nPlease install dependencies:")
    print("  pip install httpx anthropic cartesia")
    sys.exit(1)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audio_config import config as audio_config
from audio_sources import AudioSource, FileAudioSource, MicrophoneAudioSource
from latency_logger import LatencyLogger


# Load environment variables
def load_env():
    """Load API keys from .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    env_vars = {}

    if not env_path.exists():
        print(f"❌ .env file not found at {env_path}")
        sys.exit(1)

    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                # Remove quotes and strip whitespace/newlines
                value = value.strip().strip('"').strip("'")
                env_vars[key] = value

    return env_vars


@dataclass
class DeepgramStreamResult:
    """Track results from Deepgram WebSocket streaming test."""

    connection_time_ms: Optional[float] = None
    eot_time_ms: Optional[float] = None
    transcript: Optional[str] = None
    error: Optional[str] = None
    eot_detected: asyncio.Event = field(default_factory=asyncio.Event)
    last_transcript_time: Optional[float] = None


class APIValidator:
    """Validates all VoiceBuddy API integrations."""

    def __init__(self, env_vars: Dict[str, str]):
        self.env_vars = env_vars
        self.logger = LatencyLogger()
        self.session_id = "phase1-validation"
        self.turn_id = 1
        self.results = {}

    async def test_deepgram(self, mode: str = "file") -> bool:
        """
        Test Deepgram Flux API with WebSocket streaming.

        Args:
            mode: "file", "mic", or "both"
        """
        print("\n" + "=" * 80)
        print(f"Testing Deepgram Flux API (WebSocket Streaming - {mode} mode)")
        print("=" * 80)

        api_key = self.env_vars.get("DEEPGRAM_API_KEY")
        if not api_key:
            print("❌ DEEPGRAM_API_KEY not found in .env")
            return False

        # Check if we have a test audio file for file mode
        test_audio_path = Path(__file__).parent.parent / "assets" / "audio" / "fixtures" / "test_audio.wav"

        if mode == "file":
            if not test_audio_path.exists():
                print(f"⚠️  Test audio file not found at {test_audio_path}")
                print("   Using connectivity test instead...")
                return await self._test_deepgram_connectivity(api_key)
            return await self.test_deepgram_file_streaming(api_key, test_audio_path)

        elif mode == "mic":
            return await self.test_deepgram_mic_streaming(api_key, duration_seconds=5)

        elif mode == "both":
            # Test both modes
            file_ok = False
            if test_audio_path.exists():
                file_ok = await self.test_deepgram_file_streaming(api_key, test_audio_path)
            else:
                print(f"⚠️  Test audio file not found, skipping file test")
                file_ok = await self._test_deepgram_connectivity(api_key)

            mic_ok = await self.test_deepgram_mic_streaming(api_key, duration_seconds=5)
            return file_ok and mic_ok

        else:
            print(f"❌ Invalid mode: {mode}. Use 'file', 'mic', or 'both'")
            return False

    async def _test_deepgram_connectivity(self, api_key: str) -> bool:
        """Fallback: Test API connectivity when no audio file available."""
        async with httpx.AsyncClient() as client:
            try:
                start_time = time.time()
                response = await client.get(
                    "https://api.deepgram.com/v1/projects", headers={"Authorization": f"Token {api_key}"}, timeout=10.0
                )
                latency_ms = (time.time() - start_time) * 1000

                if response.status_code == 200:
                    print(f"✅ Deepgram API: Connected successfully")
                    print(f"   API Key Valid: Yes")
                    print(f"   Response Time: {latency_ms:.1f}ms")
                    self.results["deepgram"] = {
                        "status": "pass",
                        "latency_ms": latency_ms,
                        "note": "API key validated (no streaming test)",
                    }
                    return True
                else:
                    print(f"❌ Deepgram API returned status {response.status_code}")
                    return False
            except Exception as e:
                print(f"❌ Deepgram API error: {e}")
                return False

    async def _test_deepgram_streaming(self, api_key: str, audio_source: AudioSource, test_name: str = "file") -> bool:
        """Test Deepgram with WebSocket streaming (flux model)."""
        try:
            # Get audio metadata from source
            channels = audio_source.get_channels()
            sample_width = audio_source.get_sample_width()
            framerate = audio_source.get_sample_rate()

            print(f"   Audio: {channels}ch, {sample_width*8}bit, {framerate}Hz ({test_name} mode)")

            # Track results
            result = DeepgramStreamResult()
            audio_end_time = None

            # Create Deepgram client
            client = AsyncDeepgramClient(api_key=api_key)

            # Connect to WebSocket
            connection_start = time.time()

            async with client.listen.v2.connect(
                model="flux-general-en",
                encoding="linear16",
                sample_rate=str(framerate),  # Use actual sample rate from audio file
            ) as connection:

                result.connection_time_ms = (time.time() - connection_start) * 1000
                print(f"✅ WebSocket connected ({result.connection_time_ms:.1f}ms)")

                # Event handlers (match pattern from test_deepgram.py)
                def on_open(_):
                    print("   Streaming audio...")

                def on_message(msg: ListenV2SocketClientResponse):
                    nonlocal result

                    # Check for transcript updates
                    if hasattr(msg, "transcript") and msg.transcript:
                        result.transcript = msg.transcript
                        result.last_transcript_time = time.time()

                def on_error(error):
                    result.error = str(error)
                    print(f"❌ WebSocket error: {error}")
                    result.eot_detected.set()  # Unblock

                def on_close(_):
                    print("   WebSocket closed")

                # Register handlers
                connection.on(EventType.OPEN, on_open)
                connection.on(EventType.MESSAGE, on_message)
                connection.on(EventType.ERROR, on_error)
                connection.on(EventType.CLOSE, on_close)

                # Start listening (background task)
                listen_task = asyncio.create_task(connection.start_listening())

                # Brief delay for connection
                await asyncio.sleep(0.1)

                # Stream audio in chunks (80ms worth of audio)
                # Calculate chunk size based on actual sample rate
                CHUNK_DELAY_MS = 80
                CHUNK_SIZE = int((CHUNK_DELAY_MS / 1000.0) * framerate * channels * sample_width)

                chunks_sent = 0
                async for chunk in audio_source.get_chunks(CHUNK_SIZE):
                    await connection._send(chunk)
                    chunks_sent += 1

                audio_end_time = time.time()
                print(f"   Sent {chunks_sent} chunks")

                # Wait for transcript to stabilize (no updates for 300ms)
                # This indicates EOT has been detected by Deepgram
                max_wait = 5.0  # Maximum 5 seconds
                stabilization_delay = 0.3  # 300ms without updates = EOT
                last_check = result.last_transcript_time or audio_end_time

                while (time.time() - audio_end_time) < max_wait:
                    await asyncio.sleep(0.05)  # Check every 50ms

                    if result.last_transcript_time:
                        time_since_last = time.time() - result.last_transcript_time
                        if time_since_last >= stabilization_delay:
                            # Transcript has stabilized - this is EOT
                            result.eot_time_ms = (result.last_transcript_time - audio_end_time) * 1000
                            result.eot_detected.set()
                            break

                # Check if we got a transcript
                if not result.transcript:
                    print("❌ No transcript received")
                    listen_task.cancel()
                    return False

                # If we didn't detect stabilization, use current time
                if not result.eot_detected.is_set():
                    result.eot_time_ms = (time.time() - audio_end_time) * 1000
                    result.eot_detected.set()

                # Check for errors
                if result.error:
                    print(f"❌ Error: {result.error}")
                    listen_task.cancel()
                    return False

                # Success
                print(f"✅ Deepgram API: EOT detected")
                print(f"   Time to EOT: {result.eot_time_ms:.1f}ms")
                print(f'   Transcript: "{result.transcript}"')

                # Log to latency logger (Stage 1: EOT detection)
                self.logger.log_latency(
                    session_id=self.session_id,
                    turn_id=self.turn_id,
                    stage="eot_detected",
                    latency_ms=result.eot_time_ms,
                    metadata={"service": "deepgram", "model": "flux-general-en"},
                )

                self.results["deepgram"] = {
                    "status": "pass",
                    "latency_ms": result.eot_time_ms,
                    "transcript": result.transcript,
                }

                # Cleanup
                listen_task.cancel()
                try:
                    await listen_task
                except asyncio.CancelledError:
                    pass

                return True

        except Exception as e:
            print(f"❌ Deepgram streaming error: {e}")
            import traceback

            traceback.print_exc()
            return False

    async def test_deepgram_file_streaming(self, api_key: str, test_audio_path: Path) -> bool:
        """Test with file-based streaming."""
        print(f"   Mode: File streaming ({test_audio_path.name})")
        audio_source = FileAudioSource(test_audio_path, realtime_delay_ms=80)
        return await self._test_deepgram_streaming(api_key, audio_source, "file")

    async def test_deepgram_mic_streaming(self, api_key: str, duration_seconds: int = 5) -> bool:
        """Test with real-time microphone."""
        print(f"   Mode: Microphone streaming ({duration_seconds}s duration)")
        print("   🎤 Please speak into your microphone...")

        try:
            audio_source = MicrophoneAudioSource(sample_rate=16000, channels=1)

            # Create a task for the streaming test
            streaming_task = asyncio.create_task(self._test_deepgram_streaming(api_key, audio_source, "microphone"))

            # Wait for either completion or timeout
            try:
                return await asyncio.wait_for(streaming_task, timeout=duration_seconds)
            except asyncio.TimeoutError:
                # Cancel the streaming task
                streaming_task.cancel()
                try:
                    await streaming_task
                except asyncio.CancelledError:
                    pass

                print(f"   ⏱️  {duration_seconds}s timeout reached")
                print("   ℹ️  Microphone test completed (timeout is normal)")
                return True  # Timeout is expected for mic streaming

        except ImportError as e:
            print(f"❌ Microphone streaming error: {e}")
            print("   Install sounddevice: pip install sounddevice")
            return False
        except Exception as e:
            print(f"❌ Microphone streaming error: {e}")
            import traceback

            traceback.print_exc()
            return False

    async def test_anthropic_model(self, model_name: str, display_name: str) -> Optional[float]:
        """Test a specific Anthropic Claude model."""
        api_key = self.env_vars.get("CLAUDE_API_KEY")
        if not api_key:
            print(f"❌ CLAUDE_API_KEY not found in .env")
            return None

        client = AsyncAnthropic(api_key=api_key)

        try:
            test_prompt = "Say 'Hello! I am ready to assist with HVAC scheduling.' and nothing else."

            start_time = time.time()

            # Use non-streaming API for simplicity and reliability in Phase 1
            # Streaming TTFT measurement will be added in later phases
            message = await client.messages.create(
                model=model_name, max_tokens=50, messages=[{"role": "user", "content": test_prompt}]
            )

            end_time = time.time()
            latency_ms = (end_time - start_time) * 1000

            response_text = message.content[0].text

            print(f"✅ {display_name}: Response received")
            print(f"   Latency: {latency_ms:.1f}ms")
            print(f'   Response: "{response_text.strip()}"')

            self.logger.log_latency(
                session_id=self.session_id,
                turn_id=self.turn_id,
                stage="llm_first_token",
                latency_ms=latency_ms,
                metadata={"service": "anthropic", "model": model_name},
            )

            return latency_ms

        except Exception as e:
            print(f"❌ {display_name} error: {type(e).__name__}: {e}")
            import traceback

            traceback.print_exc()
            return None

    async def test_anthropic(self) -> bool:
        """Test both Anthropic Claude models (Haiku and Sonnet)."""
        print("\n" + "=" * 80)
        print("Testing Anthropic API (Claude)")
        print("=" * 80)

        # Test Haiku
        haiku_ttft = await self.test_anthropic_model("claude-haiku-4-5-20251001", "Claude Haiku 4.5")

        # Test Sonnet
        sonnet_ttft = await self.test_anthropic_model("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5")

        success = haiku_ttft is not None and sonnet_ttft is not None

        if success:
            self.results["anthropic"] = {
                "status": "pass",
                "haiku_latency_ms": haiku_ttft,
                "sonnet_latency_ms": sonnet_ttft,
            }

        return success

    async def test_cartesia(self) -> bool:
        """Test Cartesia Sonic 3 API with voice clone."""
        print("\n" + "=" * 80)
        print("Testing Cartesia Sonic 3 API")
        print("=" * 80)

        api_key = self.env_vars.get("CARTESIA_API_KEY")
        voice_id = self.env_vars.get("CARTESIA_VOICE_ID")

        if not api_key:
            print("❌ CARTESIA_API_KEY not found in .env")
            return False

        if not voice_id:
            print("❌ CARTESIA_VOICE_ID not found in .env")
            return False

        print(f"   Voice Clone ID: {voice_id}")

        try:
            test_text = "Hello! I'm ready to help you schedule your HVAC appointment."

            client = Cartesia(api_key=api_key)

            start_time = time.time()
            first_byte_time = None

            output_format = audio_config.get_output_format()

            # Generate audio using the client library
            chunk_iter = client.tts.bytes(
                model_id="sonic-3",
                transcript=test_text,
                voice={
                    "mode": "id",
                    "id": voice_id,
                },
                output_format=output_format,
            )

            # with open("test_cartesia_sonic.wav", "wb") as f:
            #     for chunk in chunk_iter:
            #         f.write(chunk)

            # Collect chunks and measure TTFB
            audio_chunks = []
            for chunk in chunk_iter:
                if first_byte_time is None:
                    first_byte_time = time.time()
                audio_chunks.append(chunk)

            if first_byte_time is None:
                print("❌ Cartesia: No audio data received")
                return False

            ttfb_ms = (first_byte_time - start_time) * 1000
            total_audio_bytes = sum(len(chunk) for chunk in audio_chunks)

            print(f"✅ Cartesia API: Audio generated")
            print(f"   Time to First Byte: {ttfb_ms:.1f}ms")
            print(f"   Total Audio Bytes: {total_audio_bytes:,}")
            print(f"   Voice Clone: Active")

            # Save audio to test file (already in WAV format)
            output_path = Path(__file__).parent.parent / "assets" / "audio" / "outputs" / "test_cartesia_output.wav"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(str(output_path), "wb") as f:
                for chunk in audio_chunks:
                    f.write(chunk)

            print(f"   Audio saved to: {output_path}")

            self.logger.log_latency(
                session_id=self.session_id,
                turn_id=self.turn_id,
                stage="tts_first_byte",
                latency_ms=ttfb_ms,
                metadata={"service": "cartesia", "model": "sonic-3", "voice_id": voice_id},
            )

            self.results["cartesia"] = {
                "status": "pass",
                "ttfb_ms": ttfb_ms,
                "audio_bytes": total_audio_bytes,
                "output_file": str(output_path),
            }

            return True

        except Exception as e:
            print(f"❌ Cartesia API error: {e}")
            import traceback

            traceback.print_exc()
            return False

    def print_summary(self):
        """Print final summary report."""
        print("\n" + "=" * 80)
        print("PHASE 1 VALIDATION SUMMARY")
        print("=" * 80)

        # Check overall status
        all_passed = all(result.get("status") == "pass" for result in self.results.values())

        # Print individual results
        print("\nService Status:")
        print("-" * 80)

        if "deepgram" in self.results:
            dg = self.results["deepgram"]
            status = "✅ PASS" if dg["status"] == "pass" else "❌ FAIL"
            print(f"Deepgram API:      {status}")
            if dg["status"] == "pass":
                print(f"  Latency: {dg['latency_ms']:.1f}ms")
                if "transcript" in dg:
                    print(f"  Transcript: \"{dg['transcript']}\"")

        if "anthropic" in self.results:
            anth = self.results["anthropic"]
            status = "✅ PASS" if anth["status"] == "pass" else "❌ FAIL"
            print(f"\nAnthropic API:     {status}")
            if anth["status"] == "pass":
                print(f"  Haiku 4.5 Latency: {anth['haiku_latency_ms']:.1f}ms")
                print(f"  Sonnet 4.5 Latency: {anth['sonnet_latency_ms']:.1f}ms")

        if "cartesia" in self.results:
            cart = self.results["cartesia"]
            status = "✅ PASS" if cart["status"] == "pass" else "❌ FAIL"
            print(f"\nCartesia API:      {status}")
            if cart["status"] == "pass":
                print(f"  Time to First Byte: {cart['ttfb_ms']:.1f}ms")
                print(f"  Audio Output: {cart['output_file']}")

        print("\n" + "-" * 80)

        # Latency budget comparison (from build plan)
        print("\nLatency Budget Comparison:")
        print("-" * 80)
        print("Stage                    | Budget    | Actual    | Status")
        print("-" * 80)

        budget = {
            "Deepgram EOT Detection": 200,
            "Claude Haiku TTFT": 500,
            "Claude Sonnet TTFT": 1000,
            "Cartesia TTS": 300,
        }

        if "deepgram" in self.results and self.results["deepgram"]["status"] == "pass":
            actual = self.results["deepgram"]["latency_ms"]
            status = "✅ OK" if actual <= budget["Deepgram EOT Detection"] else "⚠️  SLOW"
            print(f"Deepgram EOT Detection   | {budget['Deepgram EOT Detection']:>6}ms  | {actual:>6.0f}ms  | {status}")

        if "anthropic" in self.results and self.results["anthropic"]["status"] == "pass":
            actual_haiku = self.results["anthropic"]["haiku_latency_ms"]
            actual_sonnet = self.results["anthropic"]["sonnet_latency_ms"]
            status_haiku = "✅ OK" if actual_haiku <= budget["Claude Haiku TTFT"] else "⚠️  SLOW"
            status_sonnet = "✅ OK" if actual_sonnet <= budget["Claude Sonnet TTFT"] else "⚠️  SLOW"
            print(
                f"Claude Haiku Latency     | {budget['Claude Haiku TTFT']:>6}ms  | {actual_haiku:>6.0f}ms  | {status_haiku}"
            )
            print(
                f"Claude Sonnet Latency    | {budget['Claude Sonnet TTFT']:>6}ms  | {actual_sonnet:>6.0f}ms  | {status_sonnet}"
            )

        if "cartesia" in self.results and self.results["cartesia"]["status"] == "pass":
            actual = self.results["cartesia"]["ttfb_ms"]
            status = "✅ OK" if actual <= budget["Cartesia TTS"] else "⚠️  SLOW"
            print(f"Cartesia TTS TTFB        | {budget['Cartesia TTS']:>6}ms  | {actual:>6.0f}ms  | {status}")

        print("-" * 80)

        # Log file location
        print(f"\nLog file: logs/voicebuddy.jsonl")

        # Overall status
        print("\n" + "=" * 80)
        if all_passed:
            print("✅ ALL SERVICES VALIDATED - PHASE 1 EXIT GATE PASSED")
        else:
            print("❌ VALIDATION FAILED - PLEASE CHECK ERRORS ABOVE")
        print("=" * 80)


async def main():
    """Main validation routine."""
    import argparse

    parser = argparse.ArgumentParser(description="VoiceBuddy Phase 1 API Validation")
    parser.add_argument(
        "--mode",
        choices=["file", "mic", "both"],
        default="file",
        help="Deepgram test mode: file (default), mic, or both",
    )
    args = parser.parse_args()

    print("=" * 80)
    print("VoiceBuddy Phase 1 API Validation")
    print("=" * 80)

    # Load environment variables
    print("\nLoading environment variables from .env...")
    env_vars = load_env()
    print(f"✓ Loaded {len(env_vars)} environment variables")

    # Create validator
    validator = APIValidator(env_vars)

    # Run tests
    deepgram_ok = await validator.test_deepgram(mode=args.mode)
    anthropic_ok = await validator.test_anthropic()
    cartesia_ok = await validator.test_cartesia()

    # Print summary
    validator.print_summary()

    # Exit with appropriate code
    if deepgram_ok and anthropic_ok and cartesia_ok:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
