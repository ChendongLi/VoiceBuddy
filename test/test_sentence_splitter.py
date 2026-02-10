"""Unit tests for the streaming sentence splitter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentence_splitter import SentenceSplitter


def _collect_sentences(text_or_tokens, *, char_by_char=False):
    """Helper: feed input to splitter and return emitted sentences."""
    sentences = []
    splitter = SentenceSplitter(on_sentence=lambda s: sentences.append(s))

    if char_by_char:
        for ch in text_or_tokens:
            splitter.feed(ch)
    elif isinstance(text_or_tokens, list):
        for token in text_or_tokens:
            splitter.feed(token)
    else:
        splitter.feed(text_or_tokens)

    splitter.flush()
    return sentences


class TestBasicSplit:
    def test_two_sentences(self):
        result = _collect_sentences("Hello world. How are you? ")
        assert len(result) == 1  # Combined because total < 15 words
        assert "Hello world." in result[0]
        assert "How are you?" in result[0]

    def test_question_and_exclamation(self):
        result = _collect_sentences(
            "How are you doing today my really great and wonderful dear friend over there at home? "
            "I'm doing absolutely great and wonderful and so very happy to hear from you today! "
        )
        assert len(result) == 2
        assert result[0].endswith("?")
        assert result[1].endswith("!")

    def test_long_sentences_split(self):
        s1 = "This is a fairly long sentence with many words that should be enough to pass the threshold. "
        s2 = "And this is another sentence that is also quite long with enough words to be emitted separately. "
        result = _collect_sentences(s1 + s2)
        assert len(result) == 2

    def test_single_sentence_no_trailing_space(self):
        result = _collect_sentences("Hello world.")
        # No trailing space, so period at end of buffer — flush emits it
        assert len(result) == 1
        assert result[0] == "Hello world."


class TestAbbreviations:
    def test_dr(self):
        result = _collect_sentences("Dr. Smith is here and he is ready to see you for your appointment today. ")
        assert len(result) == 1
        assert "Dr. Smith" in result[0]

    def test_mr(self):
        result = _collect_sentences("Mr. Jones called today and asked about the service appointment this week. ")
        assert len(result) == 1
        assert "Mr. Jones" in result[0]

    def test_mrs(self):
        result = _collect_sentences("Mrs. Johnson needs an appointment for her heating system repair this afternoon. ")
        assert len(result) == 1
        assert "Mrs. Johnson" in result[0]

    def test_st_ave(self):
        result = _collect_sentences("We are located at 742 St. Maple Ave. in the downtown area of the city. ")
        assert len(result) == 1
        assert "St." in result[0]
        assert "Ave." in result[0]


class TestDottedAbbreviations:
    def test_us(self):
        result = _collect_sentences(
            "The U.S. economy is strong and growing steadily throughout the entire fiscal year right now. "
        )
        assert len(result) == 1
        assert "U.S." in result[0]

    def test_pm(self):
        result = _collect_sentences("Come at 3 p.m. tomorrow and we will be ready to help you with the installation. ")
        assert len(result) == 1
        assert "p.m." in result[0]

    def test_am(self):
        result = _collect_sentences(
            "The appointment is at 9 a.m. on Monday and you should plan to arrive five minutes early. "
        )
        assert len(result) == 1
        assert "a.m." in result[0]


class TestShortFragmentBuffering:
    def test_short_prepended_to_next(self):
        sentences = []
        splitter = SentenceSplitter(on_sentence=lambda s: sentences.append(s))
        # First sentence is short (< 15 words)
        splitter.feed("Got it. ")
        assert len(sentences) == 0  # Buffered, not emitted

        # Second sentence brings total over threshold
        splitter.feed("Let me check on that service appointment for you right away and get you scheduled. ")
        splitter.flush()
        assert len(sentences) == 1
        assert sentences[0].startswith("Got it.")

    def test_flush_emits_short_fragment(self):
        sentences = []
        splitter = SentenceSplitter(on_sentence=lambda s: sentences.append(s))
        splitter.feed("Sure thing. ")
        splitter.flush()
        assert len(sentences) == 1
        assert sentences[0] == "Sure thing."


class TestStreamingTokens:
    def test_char_by_char(self):
        text = "This is a really long enough sentence to be emitted separately on its very own right now. And here is yet another sentence that should also work perfectly on its own too. "
        result = _collect_sentences(text, char_by_char=True)
        assert len(result) == 2

    def test_token_list(self):
        tokens = [
            "This ",
            "is ",
            "a ",
            "long ",
            "enough ",
            "sentence ",
            "to ",
            "pass ",
            "the ",
            "word ",
            "threshold ",
            "on ",
            "its ",
            "own ",
            "easily. ",
            "And ",
            "here ",
            "is ",
            "another ",
            "sentence ",
            "that ",
            "should ",
            "also ",
            "be ",
            "emitted ",
            "separately ",
            "from ",
            "the ",
            "first. ",
        ]
        result = _collect_sentences(tokens)
        assert len(result) == 2


class TestEllipsis:
    def test_ellipsis_mid_sentence(self):
        result = _collect_sentences(
            "Wait... really that is surprising? I had no idea that was the case at all honestly. "
        )
        assert len(result) >= 1
        # Should handle the ellipsis gracefully — not split at the dots

    def test_ellipsis_followed_by_sentence(self):
        result = _collect_sentences("Hmm... Let me think about that for a moment and get back to you soon. ")
        assert len(result) >= 1


class TestEdgeCases:
    def test_empty_input(self):
        result = _collect_sentences("")
        assert len(result) == 0

    def test_whitespace_only(self):
        result = _collect_sentences("   ")
        assert len(result) == 0

    def test_no_punctuation(self):
        result = _collect_sentences("hello world this is a test")
        assert len(result) == 1
        assert result[0] == "hello world this is a test"

    def test_multiple_spaces(self):
        result = _collect_sentences("Hello world.  How are you doing today my friend. ")
        # Both sentences exist in output
        combined = " ".join(result)
        assert "Hello world." in combined
        assert "How are you" in combined

    def test_newlines_as_whitespace(self):
        result = _collect_sentences("Hello world.\nHow are you doing today my friend.\n")
        combined = " ".join(result)
        assert "Hello world." in combined

    def test_inc_abbreviation(self):
        result = _collect_sentences("CoolBreeze Inc. is the best HVAC company in the entire greater metro area today. ")
        assert len(result) == 1
        assert "Inc." in result[0]


class TestDiscard:
    def test_discard_clears_buffer(self):
        """Feed tokens, call discard(), verify nothing is emitted on flush."""
        sentences = []
        splitter = SentenceSplitter(on_sentence=lambda s: sentences.append(s))
        splitter.feed("Hello world, this is a test sentence that ")
        splitter.feed("should not be emitted after discard. ")
        splitter.discard()
        splitter.flush()
        assert len(sentences) == 0

    def test_discard_then_new_input(self):
        """After discard, new input is processed normally."""
        sentences = []
        splitter = SentenceSplitter(on_sentence=lambda s: sentences.append(s))
        splitter.feed("This will be discarded and never seen. ")
        splitter.discard()
        splitter.feed("This is brand new text that should be emitted properly. ")
        splitter.flush()
        assert len(sentences) == 1
        assert "brand new" in sentences[0]
        assert "discarded" not in sentences[0]
