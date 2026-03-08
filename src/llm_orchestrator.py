"""
Dual-layer Claude LLM orchestrator for VoiceBuddy.

Runs Haiku (filler) and Sonnet (full response) in parallel. Haiku provides
a quick acknowledgment while Sonnet generates the complete response.
Callbacks are synchronous — callers push results to an asyncio.Queue.

When booking tools are configured, Sonnet enters a tool-use loop: it may
return tool_use blocks instead of text, which are dispatched to
BookingService and fed back as tool_result messages.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from prompts import FILLER_SYSTEM_PROMPT, SYSTEM_PROMPT  # noqa: E402

logger = logging.getLogger("voicebuddy.llm")


class LLMOrchestrator:
    """Dual-layer Claude orchestrator: Haiku filler + Sonnet full response."""

    def __init__(self):
        self.client = AsyncAnthropic(api_key=os.environ.get("CLAUDE_API_KEY"))
        self.conversation_history: list[dict] = []
        self._current_partial_text = ""

        # Callbacks (synchronous — push to event queue)
        self.on_filler_ready: Callable[[str, float], None] | None = None
        self.on_full_ready: Callable[[str, float], None] | None = None
        self.on_full_token: Callable[[str], None] | None = None

        # Booking integration (set via configure_booking)
        self._booking_service = None
        self._booking_tools: list[dict] = []
        self._customer_id: uuid.UUID | None = None

    def configure_booking(self, booking_service, tools: list[dict], customer_id: uuid.UUID) -> None:
        """Inject booking service, tool schemas, and customer ID for the current call."""
        self._booking_service = booking_service
        self._booking_tools = tools
        self._customer_id = customer_id

    async def process_turn(self, transcript: str):
        """Process a user turn: run Haiku filler and Sonnet full response in parallel."""
        self.conversation_history.append({"role": "user", "content": transcript})

        results = await asyncio.gather(
            self._run_haiku(transcript),
            self._run_sonnet(),
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                model = "haiku" if i == 0 else "sonnet"
                logger.error("LLM %s error: %s", model, result)

    async def _run_haiku(self, transcript: str):
        """Generate a quick filler response with Haiku."""
        start_ms = time.time() * 1000
        full_text = ""
        ttft_ms = 0.0

        async with self.client.messages.stream(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            system=FILLER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": transcript}],
        ) as stream:
            async for token in stream.text_stream:
                if not full_text:
                    ttft_ms = (time.time() * 1000) - start_ms
                full_text += token

        if self.on_filler_ready and full_text.strip():
            self.on_filler_ready(full_text.strip(), ttft_ms)
            logger.info("Haiku filler TTFT: %.0fms, text: %r", ttft_ms, full_text.strip())

    async def _run_sonnet(self):
        """Generate the full response with Sonnet, handling tool-use loops."""
        start_ms = time.time() * 1000
        ttft_ms = 0.0

        system_blocks = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        extra_kwargs = {}
        if self._booking_tools:
            extra_kwargs["tools"] = self._booking_tools

        # Tool-use loop: keep calling Sonnet until we get a final text response
        max_rounds = 5
        for _round in range(max_rounds):
            response = await self.client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=300,
                system=system_blocks,
                messages=self.conversation_history,
                **extra_kwargs,
            )

            if _round == 0:
                ttft_ms = (time.time() * 1000) - start_ms
                usage = response.usage
                cache_read = getattr(usage, "cache_read_input_tokens", 0)
                cache_create = getattr(usage, "cache_creation_input_tokens", 0)
                logger.info(
                    "Sonnet TTFT: %.0fms, tokens: %d, cache_read: %d, cache_create: %d",
                    ttft_ms,
                    usage.output_tokens,
                    cache_read,
                    cache_create,
                )

            # Process response content blocks
            text_parts: list[str] = []
            tool_uses: list[dict] = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append({"id": block.id, "name": block.name, "input": block.input})

            # If there are tool calls, dispatch them and feed results back
            if tool_uses and self._booking_service and self._customer_id:
                # Append assistant message with tool_use blocks to history
                self.conversation_history.append({"role": "assistant", "content": _serialize_content(response.content)})

                # Execute each tool call
                tool_results = []
                for tc in tool_uses:
                    result_str = await self._booking_service.handle_tool_call(
                        tc["name"], tc["input"], self._customer_id
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc["id"],
                            "content": result_str,
                        }
                    )
                    logger.info("Tool %s → %s", tc["name"], result_str[:120])

                self.conversation_history.append({"role": "user", "content": tool_results})

                # If stop_reason is tool_use, loop to get the next response
                if response.stop_reason == "tool_use":
                    continue

            # Final text response
            full_text = "".join(text_parts)
            self._current_partial_text = ""
            self.conversation_history.append({"role": "assistant", "content": full_text})

            if self.on_full_ready and full_text.strip():
                self.on_full_ready(full_text.strip(), ttft_ms)
            return

        logger.warning("Sonnet tool-use loop hit max rounds (%d)", max_rounds)

    def mark_interrupted(self):
        """Record partial response in history when barge-in interrupts."""
        partial = self._current_partial_text.strip()
        self._current_partial_text = ""
        if self.conversation_history and self.conversation_history[-1]["role"] == "assistant":
            return  # already had a complete entry
        if partial:
            self.conversation_history.append({"role": "assistant", "content": partial + " [interrupted]"})
        elif self.conversation_history and self.conversation_history[-1]["role"] == "user":
            self.conversation_history.append({"role": "assistant", "content": "[interrupted before response]"})


def _serialize_content(content_blocks) -> list[dict]:
    """Convert Anthropic content blocks to JSON-serializable dicts for conversation history."""
    result = []
    for block in content_blocks:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    return result
