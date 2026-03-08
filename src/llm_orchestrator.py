"""
Dual-layer Claude LLM orchestrator for VoiceBuddy.

Runs Haiku (filler) and Sonnet (full response) in parallel. Haiku provides
a quick acknowledgment while Sonnet generates the complete response.
Callbacks are synchronous — callers push results to an asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
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
        self.system_prompt_extra: str | None = None

        # Callbacks (synchronous — push to event queue)
        self.on_filler_ready: Callable[[str, float], None] | None = None
        self.on_full_ready: Callable[[str, float], None] | None = None
        self.on_full_token: Callable[[str], None] | None = None

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
        """Generate the full response with Sonnet, streaming tokens for sentence splitting."""
        start_ms = time.time() * 1000
        full_text = ""
        ttft_ms = 0.0

        # Prompt caching: cache_control on the system text block
        prompt = SYSTEM_PROMPT
        if self.system_prompt_extra:
            prompt = f"{prompt}\n\n{self.system_prompt_extra}"
        system_blocks = [
            {
                "type": "text",
                "text": prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        async with self.client.messages.stream(
            model="claude-sonnet-4-5-20250929",
            max_tokens=300,
            system=system_blocks,
            messages=self.conversation_history,
        ) as stream:
            async for token in stream.text_stream:
                if not full_text:
                    ttft_ms = (time.time() * 1000) - start_ms

                full_text += token
                self._current_partial_text = full_text

                # Stream tokens to sentence splitter via callback
                if self.on_full_token:
                    self.on_full_token(token)

        # Get final message for usage stats
        final_message = await stream.get_final_message()
        usage = final_message.usage

        # Check prompt caching effectiveness
        cache_read = getattr(usage, "cache_read_input_tokens", 0)
        cache_create = getattr(usage, "cache_creation_input_tokens", 0)
        logger.info(
            "Sonnet TTFT: %.0fms, tokens: %d, cache_read: %d, cache_create: %d",
            ttft_ms,
            usage.output_tokens,
            cache_read,
            cache_create,
        )

        # Clear partial tracking
        self._current_partial_text = ""

        # Append assistant response to conversation history
        self.conversation_history.append({"role": "assistant", "content": full_text})

        if self.on_full_ready and full_text.strip():
            self.on_full_ready(full_text.strip(), ttft_ms)

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
