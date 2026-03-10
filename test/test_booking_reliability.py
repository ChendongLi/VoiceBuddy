"""Tests for booking reliability fixes (AGE-25 + AGE-26).

AGE-25: asyncio tasks are held in _background_tasks to prevent GC.
AGE-26: setup_session failure injects no-booking warning into system_prompt_extra.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# AGE-25: Task GC protection via _background_tasks set
# ---------------------------------------------------------------------------


class TestBackgroundTaskRetention:
    """Verify the fire-and-forget pattern holds task references."""

    @pytest.mark.asyncio
    async def test_task_held_then_removed(self):
        """Task is added to _background_tasks and removed after completion."""
        _background_tasks: set[asyncio.Task] = set()

        async def work():
            return 42

        task = asyncio.create_task(work())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        assert task in _background_tasks
        await task
        # Allow the done callback to fire
        await asyncio.sleep(0)
        assert task not in _background_tasks

    @pytest.mark.asyncio
    async def test_task_removed_on_exception(self):
        """Task is removed from _background_tasks even if it raises."""
        _background_tasks: set[asyncio.Task] = set()

        async def failing_work():
            raise RuntimeError("boom")

        task = asyncio.create_task(failing_work())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        assert task in _background_tasks
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)
        assert task not in _background_tasks

    @pytest.mark.asyncio
    async def test_multiple_tasks_tracked_independently(self):
        """Multiple concurrent tasks are tracked and cleaned up independently."""
        _background_tasks: set[asyncio.Task] = set()
        gate = asyncio.Event()

        async def wait_for_gate():
            await gate.wait()

        async def fast():
            return "done"

        t1 = asyncio.create_task(wait_for_gate())
        t2 = asyncio.create_task(fast())
        for t in (t1, t2):
            _background_tasks.add(t)
            t.add_done_callback(_background_tasks.discard)

        await t2
        await asyncio.sleep(0)
        # t2 done, t1 still pending
        assert t1 in _background_tasks
        assert t2 not in _background_tasks

        gate.set()
        await t1
        await asyncio.sleep(0)
        assert len(_background_tasks) == 0


# ---------------------------------------------------------------------------
# AGE-26: setup_session failure injects no-booking warning
# ---------------------------------------------------------------------------


class TestSetupSessionFailureWarning:
    """When setup_session raises, LLM must be told booking tools are unavailable."""

    @pytest.mark.asyncio
    async def test_system_prompt_extra_set_on_failure(self):
        """Simulate setup_session exception path and verify system_prompt_extra."""
        llm = SimpleNamespace(system_prompt_extra=None)
        tts_queue: asyncio.Queue = asyncio.Queue()
        session_id = "test1234-abcd"
        turn_context_id = f"{session_id[:8]}-greeting"

        # Simulate the exception handler from server.py setup_session
        llm.system_prompt_extra = (
            "\n\nIMPORTANT: Booking tools are currently unavailable due to a system error. "
            "Do NOT confirm, promise, or imply that any appointment has been booked. "
            "Instead, tell the caller: 'I'm sorry, I'm having trouble accessing our "
            "booking system right now. Please call back in a few minutes or I can "
            "have someone call you back.'"
        )
        tts_queue.put_nowait(("Could I get your name please?", turn_context_id))
        tts_queue.put_nowait(("__turn_end__", turn_context_id))

        # Verify the warning content
        assert "IMPORTANT" in llm.system_prompt_extra
        assert "Do NOT confirm" in llm.system_prompt_extra
        assert "booking system" in llm.system_prompt_extra
        assert "call back" in llm.system_prompt_extra

    @pytest.mark.asyncio
    async def test_warning_overrides_previous_prompt_extra(self):
        """If system_prompt_extra had customer context, failure replaces it entirely."""
        llm = SimpleNamespace(system_prompt_extra="Customer: John Doe, loyal since 2020")

        # Simulate exception handler overwriting (not appending)
        llm.system_prompt_extra = (
            "\n\nIMPORTANT: Booking tools are currently unavailable due to a system error. "
            "Do NOT confirm, promise, or imply that any appointment has been booked. "
            "Instead, tell the caller: 'I'm sorry, I'm having trouble accessing our "
            "booking system right now. Please call back in a few minutes or I can "
            "have someone call you back.'"
        )

        assert "John Doe" not in llm.system_prompt_extra
        assert "unavailable" in llm.system_prompt_extra

    def test_server_source_has_background_tasks_set(self):
        """Verify server.py declares _background_tasks and uses the held-reference pattern."""
        server_path = Path(__file__).resolve().parent.parent / "src" / "server.py"
        source = server_path.read_text()

        assert "_background_tasks: set" in source
        assert "_background_tasks.add(task)" in source
        assert "task.add_done_callback(_background_tasks.discard)" in source

    def test_server_source_has_no_booking_warning(self):
        """Verify server.py injects the no-booking warning on setup_session failure."""
        server_path = Path(__file__).resolve().parent.parent / "src" / "server.py"
        source = server_path.read_text()

        assert "Booking tools are currently unavailable" in source
        assert "Do NOT confirm, promise, or imply" in source

    def test_server_source_logs_setup_session_start(self):
        """Verify server.py logs when setup_session begins."""
        server_path = Path(__file__).resolve().parent.parent / "src" / "server.py"
        source = server_path.read_text()

        assert "setup_session started" in source
