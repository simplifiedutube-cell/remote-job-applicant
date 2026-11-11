"""Normalization rules behind the job fingerprint.

The fingerprint is `sha256(norm_company | norm_title | norm_location)` and it is the
primary key of `jobagent.jobs`, so dedupe is structural rather than a query. That makes
these three functions unusually load-bearing: changing any of them re-fingerprints every
row in the database. `tests/unit/test_fingerprint.py` pins a golden hash for exactly that
reason.

The asymmetry that drives every judgement call here: **over-normalizing is worse than
under-normalizing.** If two different jobs collapse to one fingerprint, the second one is
silently hidden and never applied to. If one job fails to collapse, it is merely seen
twice -- and M2's cosine-similarity pass (>0.92) exists to catch that tail. So when a rule
is ambiguous, we leave the distinction in.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = [
    "job_fingerprint",
    "norm_company",
    "norm_location",
    "norm_title",
]

# --------------------------------------------------------------------------- primitives

_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s-]", re.UNICODE)


def _fold(value: str) -> str:
    """NFKD, drop combining marks, casefold. Makes 'Zürich' == 'Zurich' == 'ZÜRICH'."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _squash(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip(" -")


def _depunct(value: str) -> str:
    """Drop punctuation but keep intra-word hyphens ('remote-first', 'e-commerce')."""
    return _squash(_PUNCT.sub(" ", value))


# --------------------------------------------------------------------------- company

# Unambiguous legal-entity forms ONLY. Every entry here is a suffix that carries no
# identifying information, so stripping it cannot merge two distinct companies.
#
# Deliberately EXCLUDED, despite being common suffixes: "group", "holdings", "company",
# "as". Those appear in real distinguishing names ("Publicis Group" vs "Publicis" are
# arguably one company, but "Access Group" vs "Access" are not), and a false merge hides
# a job permanently. Also excluded: "technologies", "labs", "systems", "solutions", "ai",
# "software", "digital" -- those are the distinguishing part of the name. "KNS
# Technologies" must never collapse to "kns".
_LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co",
        "llc", "llp", "lp", "plc", "ltd", "limited",
        "pvt", "private", "pte", "pty",
        "gmbh", "ag", "ug", "kg", "kgaa",
        "nv", "bv", "sa", "sas", "sarl", "srl", "spa",
        "ab", "oy", "oyj", "aps", "kk",
    }
)  # fmt: skip

_LEADING_ARTICLES: frozenset[str] = frozenset({"the"})

# Everything after a comma or pipe is a division/locale tail: "Acme, Inc" / "Acme | Careers".
_COMPANY_TAIL = re.compile(r"[,|].*$", re.DOTALL)


def norm_company(company: str) -> str:
    """Canonical company key.

    >>> norm_company("Acme Technologies Pvt. Ltd.")
    'acme technologies'
    >>> norm_company("KNS Technologies")
    'kns technologies'
    >>> norm_company("Acme, Inc") == norm_company("ACME  INC.")
    True
    """
    value = _fold(company)
    value = _COMPANY_TAIL.sub(" ", value)
    value = value.replace("&", " and ")
    value = _depunct(value)

    tokens = value.split()
    # Strip legal suffixes repeatedly: "pvt ltd" needs two passes.
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    while len(tokens) > 1 and tokens[0] in _LEADING_ARTICLES:
        tokens.pop(0)

    return " ".join(tokens)


# --------------------------------------------------------------------------- title

# Requisition identifiers, in the shapes portals actually emit.
_REQ_ID_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\(\s*#?\s*[a-z]{0,5}[-_ ]?\d{3,}\s*\)"),  # (#4821) (JR-0042)
    re.compile(r"\[\s*#?\s*[a-z]{0,5}[-_ ]?\d{3,}\s*\]"),  # [REQ 11823]
    # The en/em dashes are deliberate: portals really do emit "Engineer — 118234".
    re.compile(r"\s*[-–—]\s*#?\s*\d{4,}\s*$"),  # noqa: RUF001
    re.compile(r"\s*\bjob\s*id\s*:?\s*\S+\s*$"),
)

# A parenthetical/bracketed group is dropped only when EVERY token inside it is noise.
# That keeps "(Contract)" and "(Platform Team)" -- which change what the job is -- while
# dropping "(m/f/d)", "(Remote)", "(US)".
_TITLE_NOISE_TOKENS: frozenset[str] = frozenset(
    {
        # gender/diversity tags, once split on '/'
        "m", "f", "d", "w", "x", "all", "genders", "gender", "any",
        # work-arrangement tags
        "remote", "hybrid", "onsite", "on", "site", "first", "fully", "100", "wfh",
        "work", "from", "home", "anywhere", "worldwide", "global", "distributed",
        # location/region tags
        "us", "usa", "u", "s", "uk", "eu", "emea", "apac", "latam", "india", "ind",
        "canada", "germany", "europe", "america", "north", "based", "only", "location",
        # filler
        "and", "or", "the", "a",
    }
)  # fmt: skip

_BRACKETED = re.compile(r"[(\[]([^)\]]*)[)\]]")
_TRAILING_ARRANGEMENT = re.compile(
    r"\s*[-–—,]\s*(fully\s+)?(remote|hybrid|on-?site|wfh)(\s+first)?\s*$"  # noqa: RUF001
)


def _is_all_noise(inner: str) -> bool:
    tokens = [t for t in _depunct(inner.replace("/", " ")).split() if t]
    return bool(tokens) and all(t in _TITLE_NOISE_TOKENS for t in tokens)


def norm_title(title: str) -> str:
    """Canonical title key.

    Seniority and employment type are NEVER stripped -- 'Senior' and 'Junior' are
    different reqs, and merging them would hide one of them forever.

    >>> norm_title("Senior AI Engineer (m/f/d) [Remote] (#4821)")
    'senior ai engineer'
    >>> norm_title("Senior AI Engineer") == norm_title("Junior AI Engineer")
    False
    """
    value = _fold(title)

    for pattern in _REQ_ID_PATTERNS:
        value = pattern.sub(" ", value)

    value = _BRACKETED.sub(lambda m: " " if _is_all_noise(m.group(1)) else f" {m.group(1)} ", value)

    # Repeat: "Engineer - Remote - Hybrid" and ", Remote" both peel one layer per pass.
    previous = None
    while previous != value:
        previous = value
        value = _TRAILING_ARRANGEMENT.sub("", value)

    value = value.replace("/", " ").replace("&", " and ")
    return _depunct(value)


# --------------------------------------------------------------------------- location

_REMOTE_WORDS: frozenset[str] = frozenset(
    {"remote", "anywhere", "worldwide", "global", "wfh", "telecommute", "virtual", "distributed"}
)

# Multi-word remote phrases, collapsed before tokenization.
_REMOTE_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bwork\s+from\s+home\b"), " remote "),
    (re.compile(r"\bwork\s+from\s+anywhere\b"), " remote "),
    (re.compile(r"\bfully\s+remote\b"), " remote "),
    (re.compile(r"\bremote[-\s]first\b"), " remote "),
    (re.compile(r"\b100%?\s*remote\b"), " remote "),
    (re.compile(r"\bhome[-\s]based\b"), " remote "),
)

# Region qualifiers that MUST survive. "Remote (US only)" and "Remote (India)" are not
# interchangeable -- collapsing them would bury a job the candidate is eligible for
# behind one they are not.
_REGION_ALIASES: dict[str, str] = {
    "us": "us", "usa": "us", "united states": "us", "america": "us", "u s": "us",
    "us only": "us", "north america": "namer", "namer": "namer",
    "canada": "ca", "ca": "ca",
    "india": "in", "in": "in", "bharat": "in",
    "uk": "uk", "united kingdom": "uk", "britain": "uk", "gb": "uk", "england": "uk",
    "eu": "eu", "europe": "eu", "european union": "eu",
    "emea": "emea", "apac": "apac", "asia": "apac", "asia pacific": "apac",
    "latam": "latam", "latin america": "latam",
    "anz": "anz", "australia": "anz", "new zealand": "anz",
    "germany": "de", "france": "fr", "spain": "es", "poland": "pl",
    "singapore": "sg", "japan": "jp", "brazil": "br", "mexico": "mx",
}  # fmt: skip

# Tokens carrying no location information at all.
_LOCATION_FILLER: frozenset[str] = frozenset(
    {"only", "based", "location", "locations", "or", "and", "the", "any", "flexible", "eligible"}
)


def norm_location(location: str) -> str:
    """Canonical location key.

    Remote roles collapse to `remote`, but a surviving region qualifier is preserved as
    `remote:<region>`.

    >>> norm_location("Work From Home") == norm_location("Anywhere")
    True
    >>> norm_location("Remote (US only)")
    'remote:us'
    >>> norm_location("Remote (US only)") == norm_location("Remote - India")
    False
    """
    value = _fold(location)
    for pattern, replacement in _REMOTE_PHRASES:
        value = pattern.sub(replacement, value)
    value = _depunct(value)

    if not value:
        return "unspecified"

    tokens = [t for t in value.split() if t not in _LOCATION_FILLER]
    is_remote = any(t in _REMOTE_WORDS for t in tokens)
    remainder = [t for t in tokens if t not in _REMOTE_WORDS]

    if is_remote:
        region = _match_region(remainder)
        return f"remote:{region}" if region else "remote"

    # Physical location: keep comma structure so "springfield, il" != "springfield, mo".
    parts = [_squash(p) for p in _fold(location).split(",")]
    parts = [" ".join(t for t in _depunct(p).split() if t not in _LOCATION_FILLER) for p in parts]
    parts = [p for p in parts if p]
    return ",".join(parts) if parts else "unspecified"


def _match_region(tokens: list[str]) -> str | None:
    """Longest-match region lookup over the non-remote remainder ('north america' > 'america')."""
    if not tokens:
        return None
    for width in range(min(3, len(tokens)), 0, -1):
        for start in range(len(tokens) - width + 1):
            candidate = " ".join(tokens[start : start + width])
            if candidate in _REGION_ALIASES:
                return _REGION_ALIASES[candidate]
    return None


# --------------------------------------------------------------------------- fingerprint


def job_fingerprint(company: str, title: str, location: str) -> str:
    """`sha256(norm(company)|norm(title)|norm(location))` -- the primary key of `jobs`.

    Known and accepted limitation: "Senior AI Engineer (Remote)" at location "Bangalore"
    and "Senior AI Engineer" at location "Remote" still produce different fingerprints,
    because the arrangement lives in a different field in each case. Exact-match
    fingerprinting is the cheap first gate; M2's embedding pass is the second.
    """
    key = f"{norm_company(company)}|{norm_title(title)}|{norm_location(location)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
