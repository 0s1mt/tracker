"""Ransomware-specific classification: a curated RaaS/leak-site group name
registry plus a small set of ransom-note structural patterns, combined into
one signal that upgrades a generic `forum_post` into `ransomware_leak` and
tags which group is behind it - the concrete answer to "ransomware
forumlarını takip etmek" (priority #3 from the roadmap this module belongs
to). Still fully deterministic/offline, same reasoning as `alerting.py`:
transparent, zero API cost, no per-post LLM call.

Two signals, either one is enough to classify a post as ransomware-related:

1. `match_ransomware_group()` - exact/substring match against RANSOMWARE_GROUPS,
   a list of active/recently-active RaaS operations and their known aliases.
   This is a point-in-time snapshot (compiled from public threat intel as of
   this module's last update) - the ransomware landscape rebrands and
   collapses constantly (Hive, Conti, REvil, DarkSide are all defunct but
   still referenced historically; groups splinter into new names within
   weeks), so this list needs the same periodic manual review discipline as
   `underground_forums.FORUM_POOL` - it is NOT auto-updated from anywhere.

2. `has_ransom_note_pattern()` - regex over the handful of phrases that show
   up in almost every ransomware leak-site "we breached X" post regardless
   of which group wrote it (encryption claim, payment deadline, negotiation
   contact) - catches a new/unlisted or rebranded group's post that
   RANSOMWARE_GROUPS would otherwise miss.
"""
from __future__ import annotations

import re
import unicodedata

# Canonical name -> [canonical, alias, alias, ...]. Compiled from public RaaS
# tracking (ransomware.live, CTI reporting) - point-in-time, see module
# docstring. Defunct-but-still-referenced groups (Conti, REvil, Hive,
# DarkSide, Avaddon, Vice Society) are kept because forum posts still
# reference them retrospectively ("Conti-style", "ex-REvil affiliate").
#
# Distinctive names (not real English words / common brands) - safe to match
# as a bare word or short phrase, negligible false-positive risk.
RANSOMWARE_GROUPS: list[list[str]] = [
    ["lockbit"],
    ["qilin"],
    ["ransomhub"],
    ["blackcat", "alphv"],
    ["clop", "cl0p"],
    ["8base"],
    ["bianlian"],
    ["hunters international"],
    ["inc ransom", "inc ransomware"],
    ["rhysida"],
    ["dragonforce"],
    ["trigona"],
    ["noescape"],
    ["killsec"],
    ["cicada3301"],
    ["ra world", "ra group"],
    ["money message"],
    ["black basta", "blackbasta"],
    ["blacksuit"],
    ["babuk"],
    ["karakurt"],
    ["cuba ransomware"],
    ["nokoyawa"],
    ["darkvault"],
    ["revil", "sodinokibi"],
    ["darkside"],
    ["avaddon"],
    ["vice society"],
    ["medusalocker"],
]

# Ambiguous names that double as ordinary English words/brand names (play,
# meow, hive, royal, ...) - matching the bare word alone would false-positive
# constantly on unrelated posts, so these are ONLY matched via a qualified
# phrase ("play ransomware", "the play group", etc.), never standalone.
# "conti" and "akira" moved here after live false positives (2026-09-08):
# "conti" matched a shop listing selling "Conti VPN" subscriptions; "akira"
# risks matching the 1988 anime/common Japanese given name on general-chat
# subforums that mix in non-CTI content.
_AMBIGUOUS_RANSOMWARE_GROUPS: list[list[str]] = [
    ["conti", "conti ransomware", "conti group", "conti gang", "conti leaks", "ex-conti"],
    ["akira", "akira ransomware", "akira group", "akira gang", "akira leaks"],
    ["rorschach", "rorschach ransomware"],
    ["play", "play ransomware", "play group", "play gang"],
    ["meow", "meow ransomware", "meow leaks"],
    ["fog", "fog ransomware", "fog leak site"],
    ["cactus", "cactus ransomware"],
    ["everest", "everest ransomware", "everest group"],
    ["royal", "royal ransomware", "royal group"],
    ["lynx", "lynx ransomware"],
    ["hive", "hive ransomware"],
    ["abyss", "abyss ransomware"],
    ["snatch", "snatch ransomware", "snatch team"],
    ["termite", "termite ransomware"],
    ["safepay", "safepay ransomware"],
    ["interlock", "interlock ransomware"],
    ["sarcoma", "sarcoma ransomware"],
    ["medusa", "medusa ransomware", "medusa group"],
    ["monti", "monti ransomware"],
]

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")

# Phrases that show up across almost every ransomware group's leak-site /
# forum "new victim" post regardless of branding - encryption claim +
# payment/deadline + negotiation contact are the three recurring beats.
RANSOM_NOTE_PATTERNS = [
    re.compile(r"\b(we\s+have\s+)?encrypt(ed|ion)\s+(your|the)\s+(network|systems?|files?|data)\b", re.I),
    re.compile(r"\bpay\s+(us\s+)?within\s+\d+\s*(hours?|days?)\b", re.I),
    re.compile(r"\bdata\s+will\s+be\s+(published|leaked|released|sold)\b", re.I),
    re.compile(r"\bnegotiat(e|ion)s?\s+(chat|link|page)\b", re.I),
    re.compile(r"\bfull\s+dump\s+will\s+be\s+published\b", re.I),
    re.compile(r"\bstolen\s+(data|files)\s+from\b", re.I),
]


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    text = _NON_WORD_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _has_term(normalized: str, term: str) -> bool:
    """Always word-boundary, never plain substring - unlike alerting.py's
    keyword matching (which wants "database" to also catch "databases"),
    group-name identification wants precision: a bare substring match on a
    short name like "conti" also fires inside "contiene"/"continuous",
    misattributing an unrelated post to a specific RaaS group. Caught live
    (2026-09-08): a Spanish-language database-leak post matched "conti" via
    "... contiene informacion ...".
    """
    return re.search(rf"\b{re.escape(term)}\b", normalized) is not None


def match_ransomware_group(text: str) -> "str | None":
    """Returns the canonical group name on a hit, else None.

    Distinctive names (RANSOMWARE_GROUPS) match on any alias, bare word
    included. Ambiguous names (_AMBIGUOUS_RANSOMWARE_GROUPS - common English
    words/brand names) only match their qualified-phrase aliases
    (group[1:]) - the bare word (group[0]) is never matched standalone, to
    avoid tagging an unrelated post ("let's play a game") as a ransomware
    group mention.
    """
    if not text:
        return None
    normalized = _normalize_text(text)
    for group in RANSOMWARE_GROUPS:
        canonical = group[0]
        if any(_has_term(normalized, _normalize_text(alias)) for alias in group):
            return canonical
    for group in _AMBIGUOUS_RANSOMWARE_GROUPS:
        canonical = group[0]
        qualified_aliases = group[1:]
        if any(_has_term(normalized, _normalize_text(alias)) for alias in qualified_aliases):
            return canonical
    return None


def has_ransom_note_pattern(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in RANSOM_NOTE_PATTERNS)


def classify(text: str) -> dict:
    """Returns {"ransomware_group": str|None, "ransom_note_pattern": bool}."""
    return {
        "ransomware_group": match_ransomware_group(text),
        "ransom_note_pattern": has_ransom_note_pattern(text),
    }
