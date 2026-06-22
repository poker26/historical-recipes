# -*- coding: utf-8 -*-
"""Compound text normalisation — consolidate the OCR-fragmented phytochemistry layer.

The reference books (Растительные ресурсы СССР, Госфармакопея, Химия алкалоидов) ARE
ingested and richly extracted (~30k specific-molecule links), but the molecules are
fragmented by OCR of Greek letters (β→В/Р/р/p/0, spelled «бета»/«β», plurals,
glycoside prefixes) and polluted by non-compounds (percentages, bare numbers, garble).
β-ситостерин alone is split ~10 ways across ~330 plants — so each variant is individually
below the association engine's plant-count gate and never surfaces.

``compound_merge_key`` collapses a compound string to a FAMILY-LEVEL key: spelled Greek →
symbol, a single leading Greek/OCR-Greek letter stripped (β-/В-/0- → bare stem), light
plural strip, junk → None. We deliberately merge the α/β/γ stereo-variants of one stem
(e.g. ситостерин): for the statistical action-association engine the family signal is what
matters, and consolidation beats fragmentation. The raw ``compound`` text is preserved;
this only feeds a derived grouping key.
"""
import re

# Spelled-out Greek (and the actual symbols) → canonical symbol.
_SPELLED = {"альфа": "α", "бета": "β", "гамма": "γ", "дельта": "δ",
            "эпсилон": "ε", "омега": "ω"}
# A SINGLE leading character that is a Greek letter or its common Cyrillic/Latin/digit
# OCR look-alike — stripped (with the dash) to merge the stereo-family.
_PREFIX = set("вpрb0βуgγаaαдδ")
_LETTER = re.compile("[а-яёa-z]")


def compound_merge_key(s: str | None) -> str | None:
    """Family-level merge key for a compound string, or None if it is not a compound
    (percentage / bare number / OCR garble / too short)."""
    if not s:
        return None
    t = s.strip().lower()
    if "%" in t:
        return None
    if any(ch in t for ch in "|/\\"):
        return None
    if not _LETTER.search(t):                       # must contain a letter (drops numbers)
        return None
    if len(t) < 4:
        return None
    for k, v in _SPELLED.items():
        t = re.sub(r"(?:^|\b)" + k + "-", v + "-", t)
        t = re.sub(r"\b" + k + r"\b", v, t)
    t = re.sub(r"\s+", " ", t).strip(" -.,")
    m = re.match(r"^(.)\s*[-–]\s*(.{4,})$", t)       # single leading char + dash + stem
    if m and m.group(1) in _PREFIX:
        t = m.group(2).strip()
    if len(t) > 8 and t[-1] in "ыи":                # crude plural strip on long stems
        t = t[:-1]
    return t or None
