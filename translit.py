"""Script normalization for Uzbek/Russian.

The local hard problem: customers mix Latin and Cyrillic Uzbek and throw in
Russian terms ("o'lcham" / "ўлчам" / "размер"). A good multilingual embedding
model already matches these semantically, but folding everything to one canonical
Latin form gives a cheap, reliable boost for keyword-ish and exact matches.

Note: this is a pragmatic heuristic, not a perfect linguistic transliterator.
Its only job is to make the SAME word collide regardless of how it was typed.
"""
import re

# Single Cyrillic char -> Latin (covers Uzbek + most Russian letters).
_CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g'", "д": "d", "е": "e",
    "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "қ": "q",
    "л": "l", "м": "m", "н": "n", "о": "o", "ў": "o'", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ҳ": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}

# Latin (incl. digraphs) -> Cyrillic. Longest keys first so digraphs win.
_LAT_TO_CYR = {
    "o'": "ў", "g'": "ғ", "sh": "ш", "ch": "ч", "yo": "ё", "yu": "ю",
    "ya": "я", "ts": "ц", "a": "а", "b": "б", "v": "в", "g": "г", "d": "д",
    "e": "е", "f": "ф", "h": "ҳ", "i": "и", "j": "ж", "k": "к", "l": "л",
    "m": "м", "n": "н", "o": "о", "p": "п", "q": "қ", "r": "р", "s": "с",
    "t": "т", "u": "у", "x": "х", "y": "й", "z": "з", "'": "ъ",
}
_LAT_PATTERN = re.compile("|".join(re.escape(k) for k in _LAT_TO_CYR))


def to_latin(text: str) -> str:
    """Transliterate any Cyrillic in the text to Latin; leave Latin untouched."""
    return "".join(_CYR_TO_LAT.get(ch.lower(), ch) for ch in text)


def to_cyrillic(text: str) -> str:
    """Transliterate Latin Uzbek to Cyrillic (best-effort, for display)."""
    return _LAT_PATTERN.sub(lambda m: _LAT_TO_CYR[m.group(0)], text.lower())


def fold(text: str) -> str:
    """Canonical, script-agnostic key used to make the same word collide.

    Lowercases, transliterates Cyrillic -> Latin, then strips the apostrophe
    variants so 'o'lcham', 'olcham' and 'ўлчам' all fold to 'olcham'.
    """
    latin = to_latin(text.lower())
    return re.sub(r"[ʻʼ'`´]", "", latin)
