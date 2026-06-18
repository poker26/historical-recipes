"""First-line nickname filter for the public leaderboard/profile. Catches the
obvious; the admin block (moderation router) is the real backstop. Deliberately
conservative — better to miss a few (admin blocks them) than to false-positive on
legitimate names. Russian mat is a Scunthorpe minefield, so stems here are chosen
to rarely collide with normal words.
"""

# Latin look-alikes → Cyrillic, to defeat homoglyph evasion (xуй, пiзд, etc.).
_HOMO = str.maketrans({
    "a": "а", "b": "б", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м",
    "o": "о", "p": "р", "t": "т", "u": "и", "x": "х", "y": "у", "n": "н",
    "i": "и", "3": "з", "0": "о",
})

# Normalized offensive stems (substring match). Curated to avoid common collisions
# (no «сук» → Барсук, no «манда» → команда, no «жид» → жидкость, no bare «еб» → хлеба).
_BAD = (
    "хуй", "хуя", "хуё", "хует", "хуйн", "хуев",
    "пизд",
    "ёб", "ебал", "ебан", "ебл", "ебат", "ебут", "ебёт", "еби", "ебуч", "заеб", "наеб", "уеб", "выеб", "въеб", "долбоеб", "долбоёб",
    "бляд", "блять",
    "пидор", "пидар", "пидрил", "педик", "гондон", "гандон",
    "мудак", "мудил",
    "залуп", "ниггер", "хуила",
)


def _normalize(s: str) -> str:
    s = s.lower().translate(_HOMO)
    # keep only cyrillic letters; collapse runs of the same letter (хуууй → хуй)
    out: list[str] = []
    for ch in s:
        if "а" <= ch <= "я" or ch == "ё":
            if not out or out[-1] != ch:
                out.append(ch)
    return "".join(out)


def is_offensive(text: str) -> bool:
    if not text:
        return False
    n = _normalize(text)
    return any(b in n for b in _BAD)
