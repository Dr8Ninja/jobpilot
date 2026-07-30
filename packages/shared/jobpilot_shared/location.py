"""Classify a posting's location into india / remote / overseas.

Drives two things: the queue ranks India and remote roles above overseas ones,
and overseas roles get their own tab rather than being mixed in or dropped.

Pure string matching on the provider's own location field. Deliberately
conservative — an unrecognised location is `overseas`, never silently promoted
into the India/remote queue.
"""

import re
from typing import Literal

LocationKind = Literal["india", "remote", "overseas", "unknown"]

_INDIA_MARKERS = (
    "india",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "pune",
    "mumbai",
    "delhi",
    "gurugram",
    "gurgaon",
    "noida",
    "chennai",
    "kolkata",
    "ahmedabad",
    "jaipur",
    "chandigarh",
    "kochi",
    "cochin",
    "trivandrum",
    "thiruvananthapuram",
    "coimbatore",
    "indore",
    "bhubaneswar",
    "mohali",
    "vadodara",
    "nagpur",
    "mysuru",
    "mysore",
    "visakhapatnam",
    "surat",
    "lucknow",
    "varanasi",
)
_REMOTE_MARKERS = (
    "remote",
    "anywhere",
    "work from home",
    "wfh",
    "distributed",
    "global",
    "worldwide",
)
#: A "remote" that is really "remote *within* somewhere else" — "Remote - US",
#: "Canada (Remote)", "Remote - California", "Düsseldorf und Remote" — is not
#: open to a candidate in India.
#:
#: Enumerating the places is a losing game: countries, regions, US states, and
#: bare city names all appear, and the list is never finished. So the rule is
#: inverted. A remote posting counts as *open* only when the location field
#: contains nothing but these neutral words. Any other word is a qualifier, and
#: a qualifier the classifier does not recognise is treated as a place — which
#: keeps the failure conservative: an open role is at worst filed one tab across,
#: never the reverse.
_OPEN_REMOTE_WORDS = {
    "remote",
    "remotely",
    "anywhere",
    "worldwide",
    "global",
    "globally",
    "international",
    "distributed",
    "wfh",
    "work",
    "working",
    "from",
    "home",
    "hybrid",
    "onsite",
    "on",
    "site",
    "office",
    "based",
    "flexible",
    "fully",
    "full",
    "part",
    "time",
    "any",
    "location",
    "optional",
    "friendly",
    "first",
    "or",
    "and",
    "the",
    "in",
    "world",
    "n",
    "a",
    "tbd",
    "various",
    "multiple",
    "locations",
    "several",
    "all",
    "open",
    "unspecified",
    "of",
    # Nouns some boards append to the location field. They name no place.
    "job",
    "jobs",
    "role",
    "roles",
    "position",
    "positions",
    "opportunity",
    "only",
    "available",
    "everywhere",
    "no",
    "preference",
}
_WORDS = re.compile(r"[a-z]+")

#: The one case the word rule above cannot see: "Remote - IN", "Remote - OR".
#: Lowercased, those state codes are filler words, so this matches the original
#: string case-sensitively.
_STATE_CODE = re.compile(
    r"\b(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|"
    r"N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY]|"
    r"ON|QC|BC|AB|MB|SK|NS|NB|NL|PE)\b"
)


def classify_location(location: str | None, *, description: str = "") -> LocationKind:
    """Best-effort bucket for one posting.

    `description` is consulted only when the location field is empty or useless,
    because JD bodies mention many places that are not the job's location.
    """
    text = (location or "").strip()
    if not text:
        head = description[:400].lower()
        if any(marker in head for marker in _INDIA_MARKERS):
            return "india"
        if any(marker in head for marker in _REMOTE_MARKERS):
            return "remote"
        return "unknown"

    lowered = text.lower()

    # India wins over a remote marker: "Remote - India" is an India role.
    if any(marker in lowered for marker in _INDIA_MARKERS):
        return "india"

    if any(marker in lowered for marker in _REMOTE_MARKERS):
        words = _WORDS.findall(lowered)
        # Any word that is not neutral filler is a place qualifier: "Remote -
        # Austin", "Remote (Western States)", "Remote - EU".
        if all(word in _OPEN_REMOTE_WORDS for word in words) and not _STATE_CODE.search(text):
            return "remote"
        return "overseas"

    return "overseas"


def is_preferred(kind: str) -> bool:
    """India and remote are the roles the user actually wants surfaced first."""
    return kind in {"india", "remote"}
