"""Reference-flora preprocessing — expand abbreviated genus names.

Academic flora references (Растительные ресурсы СССР/России, Флора СССР …) print
each genus block under a header

    Род 49. SALVIA L. — ШАЛФЕЙ.

and then abbreviate the genus to its initial in every species line below it:

    1. S. adenostachya Juz. — Ш. железистоколосый. …
    2. S. aethiopis L.      — Ш. эфиопский. …

The extractor (and our name-matching) needs the FULL Russian name ("Шалфей
эфиопский") to match the species onto the existing herbarium card instead of
minting a duplicate under the meaningless stub "Ш. эфиопский" — or, worse, fuzzy-
matching the bare epithet "эфиопский" onto an unrelated genus.

This is a stateful, whole-document transform (the header and the species lines it
governs can fall in different LLM chunks, so it MUST run before chunking): scan
line by line, remember the current genus from each header, and rewrite "Ш. " →
"Шалфей " until the next header. On books without this convention no header
matches, the genus stays unset, and the text passes through unchanged — so it is
safe to run unconditionally.

The OCR latin in these scans is often destroyed (SALVIA → "ЗАГУГА"), so the genus
is taken from the Russian (Cyrillic, all-caps) word after the dash, never the
latin.
"""
import re

# Header: "Род <N>. <latin, often OCR-garbled> — <РУССКИЙ РОД>." We key on the
# all-caps Cyrillic word after the dash; the latin before it is ignored (it is
# frequently unreadable). Dash may be em/en/figure/hyphen depending on the scan.
_GENUS_HEADER_RE = re.compile(
    r"\bРод\s+\d+\b.*?[—–−-]\s*([А-ЯЁ][А-ЯЁ-]{2,})"
)


def _expansion_re(initial: str) -> re.Pattern:
    """Match the genus initial used as an abbreviation: a single uppercase letter
    + dot + space(s), standing alone (not inside a word) and followed by a species
    epithet — lowercase ("Ш. эфиопский") OR a Capitalized person-name epithet
    ("Ж. Лаксмана", "А. Королькова"). Restricting to THIS genus's initial avoids
    touching morphology codes (Пк., Мн.); a Cyrillic-OCR'd latin "А." before a
    capital may also match but its (garbage) latin is discarded downstream anyway."""
    return re.compile(rf"(?<![А-Яа-яЁё]){re.escape(initial)}\.[ \t]+(?=[А-ЯЁа-яё])")


def expand_genus_abbreviations(text: str) -> tuple[str, dict]:
    """Expand single-letter genus abbreviations using the block headers.

    Returns ``(new_text, stats)`` where stats = {headers, expansions, genera}.
    A no-op (and empty stats) on text without the "Род N. … — ГЕНУС" convention.
    """
    if not text:
        return text, {"headers": 0, "expansions": 0, "genera": []}

    genus: str | None = None          # full Russian genus, title-cased
    exp_re: re.Pattern | None = None
    headers = 0
    expansions = 0
    genera: list[str] = []
    out_lines: list[str] = []

    for line in text.split("\n"):
        m = _GENUS_HEADER_RE.search(line)
        if m:
            genus = m.group(1).capitalize()   # ШАЛФЕЙ -> Шалфей
            exp_re = _expansion_re(genus[0])
            headers += 1
            if genus not in genera:
                genera.append(genus)
        if exp_re is not None:
            line, n = exp_re.subn(genus + " ", line)
            expansions += n
        out_lines.append(line)

    return "\n".join(out_lines), {
        "headers": headers, "expansions": expansions, "genera": genera,
    }
