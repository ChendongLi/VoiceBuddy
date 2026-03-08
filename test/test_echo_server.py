"""
Phase 3 — Automated tests for the WebSocket echo server.

Each test starts its own server instance on a dynamically allocated port.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import socket
import struct
import sys
import time
from pathlib import Path

import pytest

# Make src importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from server import handle_connection, process_request


def _free_port() -> int:
    """Find an unused TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_echo_round_trip():
    """Send known PCM bytes, verify echoed bytes are identical, RTT < 200ms."""
    port = _free_port()
    async with serve(handle_connection, "localhost", port, process_request=process_request):
        uri = f"ws://localhost:{port}/ws"
        async with connect(uri) as ws:
            # 640 bytes = 320 Int16 samples = 20ms at 16kHz
            pcm_data = struct.pack("<320h", *range(320))
            assert len(pcm_data) == 640

            t0 = time.monotonic()
            await ws.send(pcm_data)
            echoed = await ws.recv()
            rtt_ms = (time.monotonic() - t0) * 1000

            assert isinstance(echoed, bytes)
            assert echoed == pcm_data, "Echoed bytes must be identical to sent bytes"
            assert rtt_ms < 200, f"RTT {rtt_ms:.1f}ms exceeds 200ms threshold"
            print(f"Echo RTT: {rtt_ms:.1f}ms")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ping_pong_latency():
    """Send JSON ping, verify pong structure and timestamps, RTT < 100ms."""
    port = _free_port()
    async with serve(handle_connection, "localhost", port, process_request=process_request):
        uri = f"ws://localhost:{port}/ws"
        async with connect(uri) as ws:
            client_ts = time.time() * 1000
            ping = json.dumps({"type": "ping", "t": client_ts})

            t0 = time.monotonic()
            await ws.send(ping)
            raw = await ws.recv()
            rtt_ms = (time.monotonic() - t0) * 1000

            pong = json.loads(raw)
            assert pong["type"] == "pong"
            assert pong["t"] == client_ts, "Pong must echo client timestamp"
            assert "server_ts" in pong, "Pong must include server_ts"
            assert isinstance(pong["server_ts"], float)
            assert rtt_ms < 100, f"Ping/pong RTT {rtt_ms:.1f}ms exceeds 100ms threshold"
            print(f"Ping/pong RTT: {rtt_ms:.1f}ms")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiple_chunks():
    """Send 100 chunks rapidly, verify all echoed in order."""
    port = _free_port()
    async with serve(handle_connection, "localhost", port, process_request=process_request):
        uri = f"ws://localhost:{port}/ws"
        num_chunks = 100

        async with connect(uri) as ws:
            sent_chunks = []
            for i in range(num_chunks):
                # First sample encodes the chunk index, rest are zeros
                data = struct.pack("<h", i) + b"\x00" * 638
                sent_chunks.append(data)
                await ws.send(data)

            # Receive all echoed chunks
            received = []
            for _ in range(num_chunks):
                echoed = await asyncio.wait_for(ws.recv(), timeout=5.0)
                received.append(echoed)

            assert len(received) == num_chunks

            for i, (sent, got) in enumerate(zip(sent_chunks, received, strict=False)):
                assert got == sent, f"Chunk {i} mismatch"

            print(f"All {num_chunks} chunks echoed correctly in order")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_binary_and_text_interleaved():
    """Verify binary and text frames are handled independently."""
    port = _free_port()
    async with serve(handle_connection, "localhost", port, process_request=process_request):
        uri = f"ws://localhost:{port}/ws"
        async with connect(uri) as ws:
            # Send binary
            pcm = b"\x01\x00" * 320  # 640 bytes
            await ws.send(pcm)

            # Send ping
            ping = json.dumps({"type": "ping", "t": 12345.0})
            await ws.send(ping)

            # Send another binary
            pcm2 = b"\x02\x00" * 320
            await ws.send(pcm2)

            # Receive: should get binary, pong, binary in order
            msg1 = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg2 = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg3 = await asyncio.wait_for(ws.recv(), timeout=2.0)

            assert isinstance(msg1, bytes) and msg1 == pcm
            pong = json.loads(msg2)
            assert pong["type"] == "pong" and pong["t"] == 12345.0
            assert isinstance(msg3, bytes) and msg3 == pcm2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_http_serves_html():
    """Verify HTTP GET / returns HTML content."""
    port = _free_port()

    def _blocking_get(p):
        conn = http.client.HTTPConnection("localhost", p, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        status = resp.status
        content_type = resp.getheader("Content-Type")
        conn.close()
        return status, body, content_type

    async with serve(handle_connection, "localhost", port, process_request=process_request):
        loop = asyncio.get_running_loop()
        status, body, content_type = await loop.run_in_executor(None, _blocking_get, port)

        assert status == 200
        assert "text/html" in content_type
        assert "VoiceBuddy" in body
        assert "downsample-processor" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_log_file_written(tmp_path, monkeypatch):
    """After echo session, verify JSONL log contains expected events."""
    log_file = tmp_path / "test.jsonl"

    # Patch LatencyLogger to use our temp file
    import latency_logger

    original_init = latency_logger.LatencyLogger.__init__

    def patched_init(self, log_file_path=str(log_file)):
        original_init(self, log_file_path)

    monkeypatch.setattr(latency_logger.LatencyLogger, "__init__", patched_init)

    port = _free_port()
    async with serve(handle_connection, "localhost", port, process_request=process_request):
        uri = f"ws://localhost:{port}/ws"
        async with connect(uri) as ws:
            # Send a few audio chunks to trigger logging
            pcm = b"\x00" * 640
            for _ in range(3):
                await ws.send(pcm)
                await ws.recv()

            # Send a ping
            await ws.send(json.dumps({"type": "ping", "t": 1000.0}))
            await ws.recv()

    # Small delay for file flush
    await asyncio.sleep(0.1)

    # Read and verify log
    assert log_file.exists(), "Log file should exist"
    lines = [line for line in log_file.read_text().strip().split("\n") if line]
    events = [json.loads(line) for line in lines]

    event_types = [e["event_type"] for e in events]

    # Should have connection event
    assert "connection" in event_types, f"Expected 'connection' event, got: {event_types}"

    # Should have state transition (START_OF_TURN)
    assert "state" in event_types, f"Expected 'state' event, got: {event_types}"

    # Find the state transition and verify it's IDLE -> USER_SPEAKING
    state_events = [e for e in events if e["event_type"] == "state"]
    assert any(
        e["data"]["from_state"] == "IDLE" and e["data"]["to_state"] == "USER_SPEAKING" for e in state_events
    ), "Should have IDLE -> USER_SPEAKING transition"

    print(f"Log contains {len(events)} events: {event_types}")
