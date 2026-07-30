"""Is a posting genuinely out of range on seniority?

This is the *only* hard rejection left in selection. A skills gap no longer drops
a job — tailoring exists to close gaps, and anything else the user wants to judge
themselves from the shortlist tab. So this module is deliberately narrow: it
rejects staff/principal-and-above titles and stated requirements above the cap,
and stays quiet otherwise.

Both checks are string-level and run before any LLM call, so a clearly
over-levelled role never costs a token.
"""

import re

#: Levels above the senior IC band, plus the management track. Matched as whole
#: words so "Principal Financial Group" (a company) is not read as a level.
_SENIOR_TITLE = re.compile(
    r"\b("
    r"staff|principal|distinguished|fellow|architect|"
    r"director|vp|svp|evp|vice\s+president|head\s+of|chief|"
    r"c[teio]o|manager|mgr"
    r")\b",
    re.IGNORECASE,
)

#: "Principal" and "fellow" also occur inside company and programme names. When
#: the word is followed by something that is plainly not a level, let it pass.
_NOT_A_LEVEL = re.compile(
    r"\b(principal|fellow)\s+(financial|group|inc|llc|ltd|bank|capital|"
    r"insurance|investments?|programme?|program)\b",
    re.IGNORECASE,
)

#: "N years", "N+ years", "N to M years", "N-M years" — the number that opens a
#: requirement. The `experience` anchor is what keeps "founded 9 years ago" out.
_YEARS = re.compile(
    r"(?<!\d)(\d{1,2})\s*(?:\+|plus)?\s*(?:(?:to|-|–|—)\s*\d{1,2}\s*(?:\+|plus)?\s*)?"
    r"(?:yrs?|years?)(?![a-z])",
    re.IGNORECASE,
)
#: Text that must sit close to the number for it to be a seniority requirement.
_EXPERIENCE_ANCHOR = re.compile(r"experien|exp\b|background|working", re.IGNORECASE)
#: Phrasings that put the anchor *before* the number: "experience of 7 years".
_ANCHOR_BEFORE = re.compile(r"(experien\w*|background)\s+(?:of|with|in)?\s*$", re.IGNORECASE)


def required_years(text: str) -> int | None:
    """Lowest stated years-of-experience requirement, or None if none is stated.

    Lowest, not highest, on purpose. A JD saying "5-10 years" will interview a
    five-year candidate, and one that states a stretch number alongside a real
    one is advertising the real one. Reading the larger number would reject roles
    the user could plausibly get.
    """
    if not text:
        return None

    found: list[int] = []
    for match in _YEARS.finditer(text):
        before = text[max(0, match.start() - 60) : match.start()]
        after = text[match.end() : match.end() + 60]
        if _EXPERIENCE_ANCHOR.search(after) or _ANCHOR_BEFORE.search(before):
            found.append(int(match.group(1)))
    return min(found) if found else None


def is_too_senior(title: str, description: str, *, max_years: int = 8) -> bool:
    """True only for roles that are genuinely beyond the candidate's range."""
    if _SENIOR_TITLE.search(title or "") and not _NOT_A_LEVEL.search(title or ""):
        return True
    years = required_years(description or "")
    return years is not None and years > max_years
