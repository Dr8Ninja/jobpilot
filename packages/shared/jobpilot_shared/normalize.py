"""Text normalisation for whitelist comparison.

Two jobs, both adversarial:

1. Fold benign spelling variance so `Node.js`, `NodeJS`, and `node js` compare
   equal — otherwise the gate rejects honest output and burns retries.
2. Fold unicode homoglyphs so a token cannot evade the lexicon scan by swapping a
   Latin `e` for a Cyrillic `е`. Folding is what makes the scan see through that;
   `contains_homoglyph` is what lets the caller reject it outright.

`+` and `#` survive normalisation deliberately: without them `C++` and `C#` both
collapse to `c`, and the gate would accept either when the whitelist holds the other.
"""

import unicodedata

# Cyrillic and Greek characters that render identically to Latin ones in most fonts.
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",  # CYRILLIC SMALL LETTER A
        "е": "e",  # CYRILLIC SMALL LETTER IE
        "о": "o",  # CYRILLIC SMALL LETTER O
        "р": "p",  # CYRILLIC SMALL LETTER ER
        "с": "c",  # CYRILLIC SMALL LETTER ES
        "у": "y",  # CYRILLIC SMALL LETTER U
        "х": "x",  # CYRILLIC SMALL LETTER HA
        "і": "i",  # CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
        "А": "A",
        "Е": "E",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Х": "X",
        "ο": "o",  # GREEK SMALL LETTER OMICRON
        "α": "a",  # GREEK SMALL LETTER ALPHA
        "ρ": "p",  # GREEK SMALL LETTER RHO
        "Α": "A",
        "Ο": "O",
        "Ρ": "P",
    }
)

_KEPT_PUNCTUATION = {"+", "#"}


def fold_homoglyphs(text: str) -> str:
    return text.translate(_HOMOGLYPHS)


def contains_homoglyph(text: str) -> bool:
    """True if the string relies on a non-Latin lookalike character."""
    return fold_homoglyphs(text) != text


def normalize_skill(text: str) -> str:
    """Canonical comparison key for a skill or technology name.

    >>> normalize_skill("Node.js") == normalize_skill("NodeJS") == normalize_skill("node js")
    True
    >>> normalize_skill("C++") != normalize_skill("C#")
    True
    """
    text = unicodedata.normalize("NFKC", text)
    text = fold_homoglyphs(text)
    text = text.casefold()
    return "".join(c for c in text if c.isalnum() or c in _KEPT_PUNCTUATION)


def normalize_all(items: object) -> set[str]:
    """Normalise an iterable of strings into a comparison set, dropping empties."""
    assert not isinstance(items, str), "pass an iterable of strings, not a single string"
    return {key for key in (normalize_skill(i) for i in items) if key}  # type: ignore[union-attr]
