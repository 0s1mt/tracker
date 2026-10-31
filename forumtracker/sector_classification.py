"""Deterministic victim-industry (sector) classification - the thing
`victim.sector` was always meant to hold, but never genuinely did.

The field existed in the schema from the start, but the only place that
ever set it (underground_forums.py's RSS normalizer) was stuffing in the
forum's own SUBFORUM/BOARD CATEGORY name ("Dos & Ddos", "tht yardım
merkezi") - a forum-navigation label, not an industry. That data wasn't
wrong to keep (it already lives in `tags`, untouched by this module), it
was just never a victim-industry signal and was misleadingly sitting in
the wrong field. Every other source (telegram_bridge.py, browser_forum.py)
left `sector` empty entirely. Same class of bug as victim.country_code
before entity_extraction.country_from_text() existed - see that module's
docstring for the parallel.

Two signals, domain checked first (cheaper, more precise when available):

1. `sector_from_domain()` - a handful of domain LABELS (not free text) that
   are strong, low-ambiguity industry signals by convention across many
   ccTLDs: "gov"/"mil" (spk.gov.tr, whitehouse.gov -> government),
   "edu"/"ac" (university.edu, "ac.uk/.jp/.kr" academic convention ->
   education). These are structural domain-naming conventions, not
   keyword guesses, so the false-positive risk is near zero.

2. `sector_from_text()` - curated keyword/phrase groups (English + the
   Turkish/Russian terms that actually show up in this forum pool's
   traffic, same bilingual-keyword approach entity_extraction.py's
   country matching uses), word-boundary matched. Deliberately modest in
   scope: unlike a country name, a forum leak-post title ("Turkey GSM
   Database", "Ziraat Bank 2M rows") rarely spells out its own industry in
   so many words - it names the ORGANIZATION, not its category. This
   module is honest about that limit: it catches generic/explicit mentions
   ("GSM", "bank", "hospital", "ministry") and nothing cleverer - a named
   organization with no sector keyword anywhere in the text stays
   unclassified, same as vecert's own "UNCLASSIFIED" cards show that
   limit is real for this kind of data, not unique to this tracker.
"""
from __future__ import annotations

import re
from typing import Optional

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    text = text.lower()
    text = _NON_WORD_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


# Domain labels that are strong, convention-based industry signals
# regardless of which ccTLD they sit under - see module docstring.
_DOMAIN_LABEL_SECTORS = {
    "gov": "government",
    "mil": "government",
    "edu": "education",
    "ac": "education",
}

# Sector -> keyword/phrase group, longest-first matched so e.g. "payment
# gateway" wins over a shorter unrelated substring. English plus the
# Turkish/Russian terms this pool's traffic actually uses.
SECTOR_TEXT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "government": (
        "ministry", "bakanligi", "bakanlığı", "bakanlik", "municipality", "belediye",
        "government database", "city council", "state agency", "federal agency",
    ),
    "finance": (
        "bank", "banka", "banking", "sigorta", "insurance", "crypto exchange",
        "payment gateway", "payment processor", "credit union",
    ),
    "healthcare": (
        "hospital", "hastane", "clinic", "klinik", "medical records", "health insurance",
    ),
    "education": (
        "university", "üniversite", "universite", "school district", "egitim bakanligi",
    ),
    "telecom": (
        "gsm", "mobile operator", "telekom", "isp database", "internet service provider",
    ),
    "technology": (
        "saas platform", "cloud storage provider", "software company", "tech company",
    ),
    "retail": (
        "e-commerce platform", "online store database", "supermarket chain", "magaza zinciri",
    ),
    "energy": (
        "power plant", "enerji santrali", "oil company", "gas company",
    ),
    "transportation": (
        "airline", "airport", "havayolu", "havalimani", "railway", "shipping company",
    ),
    "manufacturing": (
        "manufacturing plant", "factory database", "car manufacturer", "automaker",
    ),
}

_SECTOR_TEXT_PATTERNS = sorted(
    (
        (sector, re.compile(r"\b" + re.escape(_normalize(keyword)) + r"\b"))
        for sector, keywords in SECTOR_TEXT_KEYWORDS.items()
        for keyword in keywords
    ),
    key=lambda item: len(item[1].pattern),
    reverse=True,
)


def sector_from_domain(domain: Optional[str]) -> Optional[str]:
    if not domain:
        return None
    for label in domain.lower().split("."):
        if label in _DOMAIN_LABEL_SECTORS:
            return _DOMAIN_LABEL_SECTORS[label]
    return None


def sector_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    normalized = _normalize(text)
    for sector, pattern in _SECTOR_TEXT_PATTERNS:
        if pattern.search(normalized):
            return sector
    return None
