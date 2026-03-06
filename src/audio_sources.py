"""
Audio Source Abstraction for VoiceBuddy

Provides a unified interface for streaming audio from different sources:
- FileAudioSource: Read from WAV files with simulated real-time delays
- MicrophoneAudioSource: Capture from system microphone in real-time
"""

import asyncio
import wave
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from pathlib import Path
from queue import Queue

try:
    import sounddevice as sd

    SOUNDDEVICE_AVAILABLE = True
except ImportError:
    SOUNDDEVICE_AVAILABLE = False


class AudioSource(ABC):
    """Abstract base class for audio sources (file or microphone)."""

    @abstractmethod
    async def get_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        """
        Stream audio data in chunks.

        Args:
            chunk_size: Size of each chunk in bytes

        Yields:
            bytes: Audio data chunks
        """
        pass

    @abstractmethod
    def get_sample_rate(self) -> int:
        """Get the audio sample rate in Hz."""
        pass

    @abstractmethod
    def get_channels(self) -> int:
        """Get the number of audio channels (1=mono, 2=stereo)."""
        pass

    @abstractmethod
    def get_sample_width(self) -> int:
        """Get the sample width in bytes (2 for 16-bit, 4 for 32-bit)."""
        pass


class FileAudioSource(AudioSource):
    """
    Reads audio from a WAV file and simulates real-time streaming.

    Provides controlled delays between chunks to simulate real-time playback,
    useful for testing streaming endpoints without requiring live microphone input.
    """

    def __init__(self, file_path: Path, realtime_delay_ms: int = 80):
        """
        Initialize file audio source.

        Args:
            file_path: Path to WAV file
            realtime_delay_ms: Delay between chunks in milliseconds (default: 80ms)
        """
        self.file_path = file_path
        self.realtime_delay_ms = realtime_delay_ms

        # Open file to read metadata
        with wave.open(str(file_path), "rb") as wf:
            self._sample_rate = wf.getframerate()
            self._channels = wf.getnchannels()
            self._sample_width = wf.getsampwidth()

    def get_sample_rate(self) -> int:
        return self._sample_rate

    def get_channels(self) -> int:
        return self._channels

    def get_sample_width(self) -> int:
        return self._sample_width

    async def get_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        """
        Stream audio chunks from file with real-time delays.

        Args:
            chunk_size: Size of each chunk in bytes

        Yields:
            bytes: Audio data chunks
        """
        delay_seconds = self.realtime_delay_ms / 1000.0

        with wave.open(str(self.file_path), "rb") as wf:
            while True:
                chunk = wf.readframes(chunk_size // self._sample_width)
                if not chunk:
                    break

                yield chunk

                # Simulate real-time playback delay
                await asyncio.sleep(delay_seconds)


class MicrophoneAudioSource(AudioSource):
    """
    Captures audio from the system microphone in real-time.

    Uses sounddevice library with a callback-based approach, bridging to
    async iterators via a queue.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        dtype: str = "int16",
        device: int | None = None,
    ):
        """
        Initialize microphone audio source.

        Args:
            sample_rate: Sample rate in Hz (default: 16000)
            channels: Number of channels (default: 1 for mono)
            dtype: Data type for audio samples (default: "int16" for 16-bit PCM)
            device: Device ID to use, None for default device
        """
        if not SOUNDDEVICE_AVAILABLE:
            raise ImportError("sounddevice library not available. Install with: pip install sounddevice")

        self._sample_rate = sample_rate
        self._channels = channels
        self._dtype = dtype
        self._device = device

        # Sample width mapping
        self._sample_width = 2 if dtype == "int16" else 4

        # Queue for bridging callback to async iterator
        self._queue: Queue = Queue()
        self._stream = None

    def get_sample_rate(self) -> int:
        return self._sample_rate

    def get_channels(self) -> int:
        return self._channels

    def get_sample_width(self) -> int:
        return self._sample_width

    def _audio_callback(self, indata, frames, time_info, status):
        """Callback function for sounddevice stream."""
        if status:
            print(f"Microphone status: {status}")

        # Convert to bytes and put in queue
        audio_bytes = indata.tobytes()
        self._queue.put(audio_bytes)

    async def get_chunks(self, chunk_size: int) -> AsyncIterator[bytes]:
        """
        Stream audio chunks from microphone.

        Args:
            chunk_size: Size of each chunk in bytes

        Yields:
            bytes: Audio data chunks from microphone
        """
        # Calculate blocksize (number of frames per callback)
        blocksize = chunk_size // (self._channels * self._sample_width)

        # Start the audio stream
        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype=self._dtype,
            device=self._device,
            blocksize=blocksize,
            callback=self._audio_callback,
        )

        try:
            self._stream.start()

            # Stream chunks from queue
            while True:
                # Get chunk from queue (non-blocking check)
                await asyncio.sleep(0.01)  # Small delay to prevent busy-waiting

                if not self._queue.empty():
                    chunk = self._queue.get()
                    yield chunk

        finally:
            # Clean up stream
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
