"""Tests for post-OCR text processing."""

from app.services.postprocessor import (
    clean_ocr_text,
    detect_chunk_boundaries,
    split_into_chunks,
    _join_broken_words,
)


class TestCleanOcrText:
    def test_removes_pipe_artifacts(self):
        assert clean_ocr_text("текст | с | артефактами") == "текст с артефактами"

    def test_removes_control_characters(self):
        assert clean_ocr_text("текст\x00\x01\x02нормальный") == "текстнормальный"

    def test_collapses_whitespace(self):
        assert clean_ocr_text("много    пробелов") == "много пробелов"

    def test_collapses_newlines(self):
        result = clean_ocr_text("текст\n\n\n\n\nтекст")
        assert result == "текст\n\nтекст"

    def test_rejoins_hyphenated_words(self):
        result = clean_ocr_text("настой-\nка")
        assert result == "настойка"

    def test_preserves_paragraph_structure(self):
        result = clean_ocr_text("Параграф 1.\n\nПараграф 2.")
        assert "\n\n" in result

    def test_full_cleanup(self, sample_ocr_text):
        result = clean_ocr_text(sample_ocr_text)
        assert "|" not in result
        assert "**42." in result
        assert "**43." in result


class TestJoinBrokenWords:
    def test_joins_broken_cyrillic(self, sample_broken_text):
        result = _join_broken_words(sample_broken_text)
        assert "приготовленiе" in result

    def test_preserves_normal_text(self):
        text = "нормальный текст без проблем"
        assert _join_broken_words(text) == text

    def test_preserves_short_words(self):
        # "и", "в", "у" are real short words
        text = "в доме и у реки"
        result = _join_broken_words(text)
        # Should not join them since not >60% single-char words
        assert "в" in result


class TestDetectChunkBoundaries:
    def test_bold_numbered_pattern(self, sample_ocr_text):
        boundaries = detect_chunk_boundaries(sample_ocr_text)
        assert len(boundaries) == 2
        assert boundaries[0]["title"] == "Анисовая водка"
        assert boundaries[0]["number"] == 42
        assert boundaries[0]["pattern"] == "bold_numbered"
        assert boundaries[1]["title"] == "Мятная водка"
        assert boundaries[1]["number"] == 43

    def test_section_sign_pattern(self):
        text = "§ 441. Розовая вода\nрецепт...\n§ 442. Лавандовая вода\nрецепт..."
        boundaries = detect_chunk_boundaries(text)
        assert len(boundaries) == 2
        assert boundaries[0]["pattern"] == "section_sign"

    def test_numero_sign_pattern(self):
        text = "№ 1. Анисовая\nтекст\n№ 2. Мятная\nтекст"
        boundaries = detect_chunk_boundaries(text)
        assert len(boundaries) == 2
        assert boundaries[0]["pattern"] == "numero_sign"

    def test_no_boundaries(self):
        text = "Просто текст без рецептов."
        boundaries = detect_chunk_boundaries(text)
        assert len(boundaries) == 0


class TestSplitIntoChunks:
    def test_splits_by_boundaries(self, sample_ocr_text):
        boundaries = detect_chunk_boundaries(sample_ocr_text)
        chunks = split_into_chunks(sample_ocr_text, boundaries)
        assert len(chunks) == 2
        assert chunks[0]["title"] == "Анисовая водка"
        assert "анису" in chunks[0]["text"]
        assert chunks[1]["title"] == "Мятная водка"
        assert "мяты" in chunks[1]["text"]

    def test_single_chunk_when_no_boundaries(self):
        text = "Просто текст."
        chunks = split_into_chunks(text, [])
        assert len(chunks) == 1
        assert chunks[0]["text"] == text
