"""
Streaming sentence boundary detection for VoiceBuddy TTS pipeline.

Buffers streamed LLM tokens and emits complete sentences via a callback.
Handles abbreviations, short fragment buffering, and flush on stream end.
"""

from __future__ import annotations

from collections.abc import Callable

# Common abbreviations that should NOT trigger a sentence split
_ABBREVIATIONS = frozenset(
    {
        "dr",
        "mr",
        "mrs",
        "ms",
        "jr",
        "sr",
        "st",
        "ave",
        "blvd",
        "rd",
        "inc",
        "ltd",
        "corp",
        "co",
        "etc",
        "vs",
        "jan",
        "feb",
        "mar",
        "apr",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    }
)

# Multi-character abbreviations with dots (matched as-is in the buffer)
_DOTTED_ABBREVIATIONS = frozenset(
    {
        "u.s.",
        "a.m.",
        "p.m.",
        "e.g.",
        "i.e.",
        "d.c.",
    }
)

_MIN_WORDS_FOR_SENTENCE = 15


class SentenceSplitter:
    """Buffers streamed tokens and emits complete sentences."""

    def __init__(self, on_sentence: Callable[[str], None]):
        self._on_sentence = on_sentence
        self._buffer = ""
        self._pending_short = ""  # Fragments under _MIN_WORDS_FOR_SENTENCE words

    def feed(self, token: str):
        """Feed a single token from the LLM stream."""
        self._buffer += token
        self._try_emit()

    def flush(self):
        """Emit whatever remains in the buffer (called when LLM stream ends)."""
        remaining = (self._pending_short + self._buffer).strip()
        self._buffer = ""
        self._pending_short = ""
        if remaining:
            self._on_sentence(remaining)

    def _try_emit(self):
        """Check buffer for sentence boundaries and emit complete sentences."""
        while True:
            pos = self._find_sentence_end(self._buffer)
            if pos is None:
                break

            # Extract the sentence (including the punctuation)
            sentence = self._buffer[: pos + 1].strip()
            self._buffer = self._buffer[pos + 1 :]

            if not sentence:
                continue

            # Handle short fragment buffering
            combined = (self._pending_short + " " + sentence).strip() if self._pending_short else sentence
            word_count = len(combined.split())

            if word_count < _MIN_WORDS_FOR_SENTENCE:
                # Too short — buffer it and prepend to next sentence
                self._pending_short = combined
            else:
                self._pending_short = ""
                self._on_sentence(combined)

    def _find_sentence_end(self, text: str) -> int | None:
        """Find the position of the next sentence-ending punctuation.

        Returns the index of the punctuation character, or None if no valid
        sentence boundary is found.
        """
        i = 0
        while i < len(text):
            ch = text[i]

            if ch in ".?!":
                # Check if there's at least one character after (whitespace or end)
                # For '?' and '!', just need whitespace after
                if ch in "?!":
                    if i + 1 < len(text) and text[i + 1] in " \n\t":
                        return i
                    elif i + 1 >= len(text):
                        # End of buffer — wait for more tokens
                        return None
                    i += 1
                    continue

                # For '.', check it's not an abbreviation
                if ch == ".":
                    if self._is_abbreviation(text, i):
                        i += 1
                        continue

                    # Check for ellipsis (...)
                    if i + 1 < len(text) and text[i + 1] == ".":
                        # Skip all dots in the ellipsis
                        while i + 1 < len(text) and text[i + 1] == ".":
                            i += 1
                        # After ellipsis, if followed by space + uppercase or ?!, treat as boundary
                        if i + 1 < len(text) and text[i + 1] in " \n\t":
                            return i
                        i += 1
                        continue

                    # Regular period — need whitespace after
                    if i + 1 < len(text) and text[i + 1] in " \n\t":
                        return i
                    elif i + 1 >= len(text):
                        # End of buffer — wait for more tokens
                        return None

            i += 1

        return None

    def _is_abbreviation(self, text: str, dot_pos: int) -> bool:
        """Check if the period at dot_pos is part of an abbreviation."""
        # Extract the word before the dot
        word_start = dot_pos
        while word_start > 0 and text[word_start - 1].isalpha():
            word_start -= 1

        word_before_dot = text[word_start:dot_pos].lower()

        # Check single-word abbreviations (Dr., Mr., etc.)
        if word_before_dot in _ABBREVIATIONS:
            return True

        # Check dotted abbreviations (U.S., a.m., etc.)
        # Look backwards for a pattern like "X.Y." or "X.Y.Z."
        for abbr in _DOTTED_ABBREVIATIONS:
            abbr_len = len(abbr)
            start = dot_pos - abbr_len + 1
            if start >= 0 and text[start : dot_pos + 1].lower() == abbr:
                return True

        # Single uppercase letter followed by period (e.g., middle initials like "J.")
        # Exclude common single-letter words: "I", "a"
        if dot_pos > 0 and text[dot_pos - 1].isupper() and text[dot_pos - 1] not in "IA":
            is_single_letter = dot_pos < 2 or not text[dot_pos - 2].isalpha()
            if is_single_letter and dot_pos + 1 < len(text) and text[dot_pos + 1] == " ":
                # "J. Smith" — this is an initial, not a sentence end
                if dot_pos + 2 < len(text) and text[dot_pos + 2].isupper():
                    return True

        return False
