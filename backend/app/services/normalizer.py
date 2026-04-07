"""Text normalization: pre-reform orthography, old plant names, measures."""

# TODO: implement
# Key functions:
# - normalize_orthography(text, dictionary) -> text
#   - ѣ -> е, i -> и, ъ (end of word) -> remove, ѳ -> ф
# - normalize_lexicon(text, dictionary) -> text
#   - Old plant names -> modern
# - normalize_measures(text, dictionary) -> text
#   - золотник -> 4.27г, штоф -> 1.23л
#   - Реомюр -> Цельсий (×1.25)
