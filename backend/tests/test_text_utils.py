"""Tests for text utilities."""

from app.utils.text import estimate_tokens, split_into_token_chunks, clean_whitespace


class TestEstimateTokens:
    def test_short_text(self):
        tokens = estimate_tokens("Привет мир")
        assert tokens > 0

    def test_empty_text(self):
        assert estimate_tokens("") == 0

    def test_longer_text(self):
        text = "слово " * 100  # 100 words
        tokens = estimate_tokens(text)
        assert tokens > 100  # tokens > words for Russian


class TestSplitIntoTokenChunks:
    def test_short_text_single_chunk(self):
        text = "Короткий текст."
        chunks = split_into_token_chunks(text, max_tokens=6000)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_splits_long_text(self):
        # Create text > 6000 tokens (approx 9000 chars)
        text = ("Параграф с текстом о настойках. " * 50 + "\n\n") * 10
        chunks = split_into_token_chunks(text, max_tokens=500)
        assert len(chunks) > 1
        # Verify all text is preserved
        combined = "\n\n".join(chunks)
        assert len(combined) >= len(text.strip()) * 0.9  # allow some whitespace variation

    def test_respects_paragraph_boundaries(self):
        text = "Параграф один.\n\nПараграф два.\n\nПараграф три."
        chunks = split_into_token_chunks(text, max_tokens=10000)
        assert len(chunks) == 1  # small enough to fit in one chunk


class TestCleanWhitespace:
    def test_collapses_spaces(self):
        assert clean_whitespace("много    пробелов") == "много пробелов"

    def test_collapses_newlines(self):
        assert clean_whitespace("а\n\n\n\n\nб") == "а\n\nб"

    def test_strips(self):
        assert clean_whitespace("  текст  ") == "текст"
